"""OPC-UA Actuator node."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import asdict, fields
from typing import TYPE_CHECKING

from asyncua import ua, uamethod

from reactors_czlab.core.calibration.run import CalibrationRun
from reactors_czlab.core.data import (
    ControlConfig,
    ControlMethod,
    OutputUnit,
)
from reactors_czlab.core.server_state import control_config_from_actuator

if TYPE_CHECKING:
    from asyncua import Server
    from asyncua.common.node import Node

    from reactors_czlab.core.actuator import Actuator

_logger = logging.getLogger("server.opcactuator")

control_method = {
    0: ControlMethod.manual,
    1: ControlMethod.timer,
    2: ControlMethod.on_boundaries,
    3: ControlMethod.pid,
}

output_unit_map = {
    0: OutputUnit.duty,
    1: OutputUnit.flow,
    2: OutputUnit.volume,
}


def _argument(name: str, data_type: int) -> ua.Argument:
    """Build one OPC method argument declaration."""
    argument = ua.Argument()
    argument.Name = name
    argument.DataType = ua.NodeId(data_type)
    return argument


class ActuatorOpc:
    """Actuator node."""

    def __init__(self, actuator: Actuator) -> None:
        """Initialize the OPC actuator node."""
        self.actuator = actuator
        self.id = actuator.id
        self.run = CalibrationRun(actuator)
        self._config_lock = asyncio.Lock()
        self.on_control_config_changed: Callable[[str], None] | None = None
        self.on_state_changed: Callable[[], None] | None = None

    def __repr__(self) -> str:
        """Print actuator id."""
        return f"ActuatorOpc(id: {self.actuator.id})"

    async def update_value(self) -> None:
        """Publish the actuator output and the pump data.

        Every write is change-gated. asyncua's default StatusValue trigger
        already suppresses notifications for identical values, so rewriting
        constants only costs work on the Pi. ``total_volume`` is therefore a
        step series: a row appears when delivered volume actually changes.
        """
        # old_value is what write_output() last pushed to the hardware.
        current = self.actuator.channel.old_value
        if await self._publish_if_changed(self.curr_value, current):
            _logger.debug("Updated %s with value %s", self.id, current)

        await self._publish_if_changed(
            self.total_volume,
            self.actuator.dispenser.total_volume,
        )
        cal = self.actuator.channel.calibration
        if cal is not None:
            await self._publish_if_changed(self.cal_a, cal.a)
            await self._publish_if_changed(self.cal_b, cal.b)
            await self._publish_if_changed(self.cal_r2, cal.r2)

    @staticmethod
    async def _publish_if_changed(variable: Node, value: float) -> bool:
        """Write one float only when its published value differs."""
        value = float(value)
        if await variable.get_value() == value:
            return False
        await variable.write_value(value)
        return True

    async def reset_total_volume(self) -> tuple[bool, str]:
        """Zero the accrued delivered volume without stopping the pump.

        Refused while a calibration run or an autotune owns the actuator:
        both drive the pump outside the ordinary accrual path, so zeroing
        the counter under them would race their own bookkeeping. The zero
        is published immediately rather than waiting for the next sampling
        cycle, and no configuration checkpoint is triggered - the total is
        durable history in PostgreSQL, not server configuration.

        Returns
        -------
        tuple[bool, str]
            Whether the reset was applied and an operator-readable
            message.

        """
        actuator = self.actuator
        if actuator.calibrating or actuator.autotune_owner is not None:
            message = f"{self.id}: cannot reset delivered volume while calibration or autotune owns the pump"
            _logger.warning(message)
            return (False, message)
        actuator.dispenser.reset_total_volume()
        await self._publish_if_changed(
            self.total_volume,
            actuator.dispenser.total_volume,
        )
        message = f"{self.id}: delivered volume reset to zero"
        _logger.info(message)
        return (True, message)

    async def init_node(
        self,
        server: Server,
        parent: Node,
        idx: int,
    ) -> None:
        """Add node and variables for the actuator."""
        actuator = self.actuator

        # Add actuator node to reactor
        self.node = await parent.add_object(idx, actuator.id)
        bnp = await parent.read_browse_name()
        bns = await self.node.read_browse_name()
        _logger.info("In node %s added %s", bnp.Name, bns.Name)

        # Add a node with variables holding the control config
        await self.init_control_node(idx)
        # Configuration changes are one method call. The variables below
        # are read-back only; subscribing to individually writable fields
        # exposed intermediate controller configurations to the fast loop.
        await self.init_control_methods(idx)
        # Expose the pump calibration workflow
        await self.init_calibration_methods(idx)
        # Only a calibrated pump accrues a mL total worth resetting; MFCs
        # have no calibration slot, so the reset method is not offered.
        if actuator.channel.calibration is not None:
            await self.init_reset_method(idx)

    async def apply_control_config(
        self,
        method_index: int,
        unit_index: int,
        value: float,
        time_on: float,
        time_off: float,
        lb: float,
        ub: float,
        setpoint: float,
        kp: float,
        ki: float,
        kd: float,
        min_integral: float,
        max_integral: float,
        auto_integral_band: bool,
        backwards: bool,
    ) -> tuple[bool, str]:
        """Atomically validate, apply and publish one control config.

        Returns
        -------
        tuple[bool, str]
            Whether the configuration was accepted and an
            operator-readable status message.

        """
        async with self._config_lock:
            return await self._apply_control_config_locked(
                method_index,
                unit_index,
                value,
                time_on,
                time_off,
                lb,
                ub,
                setpoint,
                kp,
                ki,
                kd,
                min_integral,
                max_integral,
                auto_integral_band,
                backwards,
            )

    async def _apply_control_config_locked(
        self,
        method_index: int,
        unit_index: int,
        value: float,
        time_on: float,
        time_off: float,
        lb: float,
        ub: float,
        setpoint: float,
        kp: float,
        ki: float,
        kd: float,
        min_integral: float,
        max_integral: float,
        auto_integral_band: bool,
        backwards: bool,
    ) -> tuple[bool, str]:
        """Apply a config while ``_config_lock`` is held."""
        before = self._control_configuration_signature()
        try:
            method = control_method[method_index]
        except KeyError:
            message = (
                f"control method index {method_index} is not one of "
                f"{sorted(control_method)}"
            )
            return (False, message)

        try:
            unit = output_unit_map[unit_index]
        except KeyError:
            message = (
                f"output unit index {unit_index} is not one of "
                f"{sorted(output_unit_map)}"
            )
            return (False, message)

        fields: dict[str, object]
        match method:
            case ControlMethod.manual:
                fields = {"value": value}

            case ControlMethod.timer:
                fields = {
                    "time_on": time_on,
                    "time_off": time_off,
                    "value": value,
                }

            case ControlMethod.on_boundaries:
                fields = {
                    "lb": lb,
                    "ub": ub,
                    "value": value,
                    "backwards": backwards,
                }

            case ControlMethod.pid:
                fields = {
                    "setpoint": setpoint,
                    "kp": kp,
                    "ki": ki,
                    "kd": kd,
                    "min_integral": min_integral,
                    "max_integral": max_integral,
                    "auto_integral_band": auto_integral_band,
                    "backwards": backwards,
                }

        config = ControlConfig(method, output_unit=unit, **fields)
        reason = self.actuator.set_control_config(config)
        if reason is not None:
            return (False, reason)

        # A selected-pump change can replace/reset the dispenser that owns a
        # live dose. Abort synchronously before the first awaited readback
        # write so cleanup cannot be delayed behind OPC I/O. An identical
        # complete config remains the historical no-op.
        changed = before != self._control_configuration_signature()
        if changed and self.on_control_config_changed is not None:
            try:
                self.on_control_config_changed(self.id)
            except Exception:
                _logger.exception("Control-change callback failed for %s", self.id)
        if changed and self.on_state_changed is not None:
            try:
                self.on_state_changed()
            except Exception:
                _logger.exception("State-change callback failed for %s", self.id)

        # Publish only after the actuator accepted the whole object. These
        # variables are now a read-back model, never a command surface.
        # Python ints infer Int64, but these MultiStateDiscrete variables are
        # UInt32. The explicit type matters even for a server-side write.
        await self.method.write_value(
            ua.Variant(method_index, ua.VariantType.UInt32),
        )
        await self.output_unit.write_value(
            ua.Variant(unit_index, ua.VariantType.UInt32),
        )
        for name, field_value in fields.items():
            await getattr(self, name).write_value(field_value)

        message = (
            f"{self.id}: {self.actuator.controller} in "
            f"{self.actuator.dispenser.unit}"
        )
        return (True, message)

    def _control_configuration_signature(self) -> tuple[object, ...]:
        """Return only configuration fields, excluding live controller state."""
        controller = self.actuator.controller
        configured = tuple(
            (spec.name, getattr(controller, spec.name))
            for spec in fields(controller)
            if spec.compare
        )
        return (type(controller), self.actuator.dispenser.unit, configured)

    async def publish_pid_gains(self) -> None:
        """Publish the three PID gain readbacks from the live controller."""
        controller = self.actuator.controller
        await self.kp.write_value(controller.kp)
        await self.ki.write_value(controller.ki)
        await self.kd.write_value(controller.kd)

    async def init_control_node(self, idx: int) -> None:
        """Add configuration variables for the control method.

        Every controller's parameters live side by side under one node; the
        server updates them as a read-back model after an atomic
        ``apply_control_config`` call succeeds.
        """
        config = control_config_from_actuator(self.actuator)
        method_index = next(
            index for index, method in control_method.items() if method is config.method
        )
        unit_index = next(
            index for index, unit in output_unit_map.items() if unit is config.output_unit
        )

        # Add Node to store the control settings
        self.control_method = await self.node.add_object(
            idx,
            f"{self.id}:ControlMethod",
        )

        # Add variable to set the desired status
        self.value = await self.control_method.add_variable(
            idx,
            f"{self.id}:value",
            config.value,
        )

        # Add variable to record the current status
        self.curr_value = await self.node.add_variable(
            idx,
            f"{self.id}:curr_value",
            0.0,
        )
        await self.curr_value.set_writable()

        # Published pump data. The browse names follow the
        # <reactor>:<name>:<channel> contract, so they reach the data table.
        self.total_volume = await self.node.add_variable(
            idx,
            f"{self.id}:total_volume",
            self.actuator.dispenser.total_volume,
        )
        self.cal_a = await self.node.add_variable(idx, f"{self.id}:cal_a", 0.0)
        self.cal_b = await self.node.add_variable(idx, f"{self.id}:cal_b", 0.0)
        self.cal_r2 = await self.node.add_variable(
            idx,
            f"{self.id}:cal_r2",
            0.0,
        )

        # ControlMethod
        self.method = await self.control_method.add_variable(
            idx,
            f"{self.id}:method",
            method_index,
            varianttype=ua.VariantType.UInt32,
        )
        enum_strings_variant = ua.Variant(
            [ua.LocalizedText(control_method[k]) for k in control_method],
            ua.VariantType.LocalizedText,
        )
        await self.method.add_property(
            ua.ObjectIds.MultiStateDiscreteType_EnumStrings,
            "EnumStrings",
            enum_strings_variant,
        )

        # Unit the demand is expressed in: raw counts, mL/min, or mL.
        self.output_unit = await self.control_method.add_variable(
            idx,
            f"{self.id}:output_unit",
            unit_index,
            varianttype=ua.VariantType.UInt32,
        )
        unit_strings_variant = ua.Variant(
            [ua.LocalizedText(output_unit_map[k]) for k in output_unit_map],
            ua.VariantType.LocalizedText,
        )
        await self.output_unit.add_property(
            ua.ObjectIds.MultiStateDiscreteType_EnumStrings,
            "EnumStrings",
            unit_strings_variant,
        )

        # TimerControl
        self.time_on = await self.control_method.add_variable(
            idx,
            f"{self.id}:time_on",
            config.time_on,
        )
        self.time_off = await self.control_method.add_variable(
            idx,
            f"{self.id}:time_off",
            config.time_off,
        )

        # OnBoundariesControl
        self.lb = await self.control_method.add_variable(
            idx,
            f"{self.id}:lb",
            config.lb,
        )
        self.ub = await self.control_method.add_variable(
            idx,
            f"{self.id}:ub",
            config.ub,
        )

        # PidControl. Seeded with the _PidControl / ControlConfig defaults
        # so a read is meaningful before the operator applies anything.
        self.setpoint = await self.control_method.add_variable(
            idx,
            f"{self.id}:setpoint",
            config.setpoint,
        )
        self.kp = await self.control_method.add_variable(
            idx,
            f"{self.id}:kp",
            config.kp,
        )
        self.ki = await self.control_method.add_variable(
            idx,
            f"{self.id}:ki",
            config.ki,
        )
        self.kd = await self.control_method.add_variable(
            idx,
            f"{self.id}:kd",
            config.kd,
        )
        self.min_integral = await self.control_method.add_variable(
            idx,
            f"{self.id}:min_integral",
            config.min_integral,
        )
        self.max_integral = await self.control_method.add_variable(
            idx,
            f"{self.id}:max_integral",
            config.max_integral,
        )
        self.auto_integral_band = await self.control_method.add_variable(
            idx,
            f"{self.id}:auto_integral_band",
            config.auto_integral_band,
        )

        # Shared by PID and on_boundaries: reverses the controller's sense
        # (an actuator runs one controller at a time, so one flag serves
        # both). Lets two actuators on one sensor drive opposing pumps.
        self.backwards = await self.control_method.add_variable(
            idx,
            f"{self.id}:backwards",
            config.backwards,
        )

    async def init_control_methods(self, idx: int) -> None:
        """Expose atomic configuration and its on-demand read model."""

        @uamethod
        async def apply_control_config(
            parent: Node,
            method: int,
            output_unit: int,
            value: float,
            time_on: float,
            time_off: float,
            lb: float,
            ub: float,
            setpoint: float,
            kp: float,
            ki: float,
            kd: float,
            min_integral: float,
            max_integral: float,
            auto_integral_band: bool,
            backwards: bool,
        ) -> tuple[bool, str]:
            """Apply one complete configuration."""
            return await self.apply_control_config(
                method,
                output_unit,
                value,
                time_on,
                time_off,
                lb,
                ub,
                setpoint,
                kp,
                ki,
                kd,
                min_integral,
                max_integral,
                auto_integral_band,
                backwards,
            )

        inargs = [
            _argument("Method", ua.ObjectIds.UInt32),
            _argument("Output_unit", ua.ObjectIds.UInt32),
            *(
                _argument(name, ua.ObjectIds.Double)
                for name in (
                    "Value",
                    "Time_on",
                    "Time_off",
                    "Lb",
                    "Ub",
                    "Setpoint",
                    "Kp",
                    "Ki",
                    "Kd",
                    "Min_integral",
                    "Max_integral",
                )
            ),
            _argument("Auto_integral_band", ua.ObjectIds.Boolean),
            _argument("Backwards", ua.ObjectIds.Boolean),
        ]
        outargs = [
            _argument("Accepted", ua.ObjectIds.Boolean),
            _argument("Message", ua.ObjectIds.String),
        ]
        await self.node.add_method(
            idx,
            f"{self.id}:apply_control_config",
            apply_control_config,
            inargs,
            outargs,
        )

        @uamethod
        def get_control_config(parent: Node) -> str:
            """Return the running configuration and enum options."""
            return self.control_config_json()

        await self.node.add_method(
            idx,
            f"{self.id}:get_control_config",
            get_control_config,
            [],
            [_argument("Config", ua.ObjectIds.String)],
        )

    def control_config_json(self) -> str:
        """Serialise the controller configuration that is actually running.

        Returns
        -------
        str
            JSON containing every configuration field plus enum names in
            the server's own index order.

        """
        controller = self.actuator.controller
        method = controller.method
        config = control_config_from_actuator(self.actuator)

        payload = {
            "method": method.value,
            "output_unit": config.output_unit.value,
            "demand": controller.value,
            "value": config.value,
            "time_on": config.time_on,
            "time_off": config.time_off,
            "lb": config.lb,
            "ub": config.ub,
            "setpoint": config.setpoint,
            "kp": config.kp,
            "ki": config.ki,
            "kd": config.kd,
            "min_integral": config.min_integral,
            "max_integral": config.max_integral,
            "auto_integral_band": config.auto_integral_band,
            "backwards": config.backwards,
            "methods": [item.value for item in control_method.values()],
            "output_units": [item.value for item in output_unit_map.values()],
            "dose_limits": self._dose_limits(),
        }
        return json.dumps(payload)

    def _dose_limits(self) -> dict[str, dict[str, float]] | None:
        """Calculate both volume policies from the live calibration."""
        cal = self.actuator.channel.calibration
        if cal is None or not cal.is_fitted or cal.installable_reason() is not None:
            return None
        dispenser = self.actuator.dispenser
        return {
            "non_pid": asdict(dispenser.dose_policy(pid=False)),
            "pid": asdict(dispenser.dose_policy(pid=True)),
        }

    def calibration_json(self) -> str:
        """Serialise the pump's calibration and its run state.

        Everything an operator needs to judge a calibration that is not
        already a published variable: the duty limits, when it was
        fitted, the measured points behind the line, and whether a run
        is in flight right now.

        Returns
        -------
        str
            A JSON object. ``calibration`` is ``null`` for an actuator
            with no calibration slot at all - the MFCs - which is how a
            caller knows to hide the pump calibration screen rather than
            show it empty.

        """
        channel = self.actuator.channel
        cal = channel.calibration
        state = {
            "actuator": self.id,
            # Run state is server-side and shared: ActuatorOpc builds one
            # CalibrationRun per actuator for the life of the process, so
            # a reloaded page or a second client sees the same run.
            "running": self.run.is_running,
            "pending": list(self.run.pending) if self.run.pending else None,
            "run_points": [list(point) for point in self.run.points],
            "zero_flow_duty": self.run.zero_flow_duty,
            "calibration": None,
        }
        if cal is not None:
            try:
                numeric_equation = cal.numeric_equation
                parameter_names = list(cal.named_parameters)
                named_parameters = cal.named_parameters
            except (KeyError, ValueError):
                numeric_equation = "unsupported calibration model"
                parameter_names = ["a", "b", "c"]
                named_parameters = {"a": cal.a, "b": cal.b, "c": cal.c}
            match cal.model:
                case "linear":
                    equation = "a * duty + b"
                case "dead-zone linear":
                    equation = "max(0, a * (duty - b))"
                case "saturating exponential":
                    equation = "a * (1 - exp(-b * (duty - c)))"
                case "logistic":
                    equation = "a / (1 + exp(-b * (duty - c)))"
                case "power":
                    equation = "a * duty ** b"
                case _:
                    equation = "unsupported"
            fit_series = {
                "duty": [point[0] for point in cal.fit_points],
                "flow": [point[1] for point in cal.fit_points],
                "lower": [point[2] for point in cal.fit_points],
                "upper": [point[3] for point in cal.fit_points],
            }
            state["calibration"] = {
                "file": cal.file,
                "model": cal.model,
                "a": cal.a,
                "b": cal.b,
                "c": cal.c,
                "r2": cal.r2,
                "residual": cal.residual,
                "aic": cal.aic,
                "equation": equation,
                "numeric_equation": numeric_equation,
                "parameter_names": parameter_names,
                "named_parameters": named_parameters,
                "equation_metadata": {
                    "dependent": "flow_ml_min",
                    "independent": "duty",
                    "parameters": {"a": cal.a, "b": cal.b, "c": cal.c},
                },
                "min_duty": cal.min_duty,
                "max_duty": cal.max_duty,
                "dispense_duty": cal.dispense_duty,
                "zero_flow_duty": cal.zero_flow_duty,
                "fitted_at": cal.fitted_at,
                "is_fitted": cal.is_fitted,
                "points": [list(point) for point in cal.points],
                "fit_points": [list(point) for point in cal.fit_points],
                "fit_series": fit_series,
                # The single authority on whether this may be installed,
                # and already worded for an operator. Carried through so
                # the screen shows the reason rather than deriving its
                # own.
                "installable_reason": cal.installable_reason(),
            }
        return json.dumps(state)

    async def init_calibration_methods(self, idx: int) -> None:
        """Expose the pump calibration workflow on the actuator node.

        Every method answers with a status string; the operator drives the
        run from a generic OPC client and reads the result off the call.
        """
        run = self.run

        @uamethod
        async def calibrate_point(
            parent: Node,
            duty: float,
            seconds: float,
        ) -> str:
            """Run the pump at a duty for a time, then stop it."""
            return await run.calibrate_point(duty, seconds)

        @uamethod
        def record_point(parent: Node, volume_ml: float) -> str:
            """Record the volume measured for the last point."""
            return run.record_point(volume_ml)

        @uamethod
        def fit_calibration(parent: Node) -> str:
            """Fit, store and install the collected points."""
            return run.fit()

        @uamethod
        def fit_calibration_with_zero_flow(
            parent: Node,
            zero_flow_duty: float,
        ) -> str:
            """Fit while supplying separately held stall evidence."""
            return run.fit(zero_flow_duty=zero_flow_duty)

        @uamethod
        def clear_points(parent: Node) -> str:
            """Throw the collected points away."""
            return run.clear_points()

        @uamethod
        def discard_pending_point(parent: Node) -> str:
            """Discard a completed point awaiting its measurement."""
            return run.discard_pending_point()

        @uamethod
        def reload_calibration(parent: Node) -> str:
            """Re-read the stored calibration from disk."""
            return run.reload()

        @uamethod
        def set_duties(
            parent: Node,
            min_duty: float,
            dispense_duty: float,
        ) -> str:
            """Adjust the stall floor and the dose duty without a refit."""
            return run.set_duties(min_duty, dispense_duty)

        inarg_duty = ua.Argument()
        inarg_duty.Name = "Duty"
        inarg_duty.DataType = ua.NodeId(ua.ObjectIds.Float)
        inarg_duty.Description = ua.LocalizedText(
            Text="PLC counts to drive the pump at",
        )

        inarg_seconds = ua.Argument()
        inarg_seconds.Name = "Seconds"
        inarg_seconds.DataType = ua.NodeId(ua.ObjectIds.Float)
        inarg_seconds.Description = ua.LocalizedText(
            Text="How long to run the pump for",
        )

        inarg_volume = ua.Argument()
        inarg_volume.Name = "Volume_ml"
        inarg_volume.DataType = ua.NodeId(ua.ObjectIds.Float)
        inarg_volume.Description = ua.LocalizedText(
            Text="Measured volume delivered by the last point, in mL",
        )

        inarg_min_duty = ua.Argument()
        inarg_min_duty.Name = "Min_duty"
        inarg_min_duty.DataType = ua.NodeId(ua.ObjectIds.Float)
        inarg_min_duty.Description = ua.LocalizedText(
            Text="Stall floor: the lowest duty the pump turns at",
        )

        inarg_dispense = ua.Argument()
        inarg_dispense.Name = "Dispense_duty"
        inarg_dispense.DataType = ua.NodeId(ua.ObjectIds.Float)
        inarg_dispense.Description = ua.LocalizedText(
            Text="Duty used for non-PID volume doses",
        )

        inarg_zero_flow = ua.Argument()
        inarg_zero_flow.Name = "Zero_flow_duty"
        inarg_zero_flow.DataType = ua.NodeId(ua.ObjectIds.Float)
        inarg_zero_flow.Description = ua.LocalizedText(
            Text="Stall evidence excluded from model fitting",
        )

        @uamethod
        def get_calibration(parent: Node) -> str:
            """Report the whole calibration state as JSON."""
            return self.calibration_json()

        outarg = ua.Argument()
        outarg.Name = "Status"
        outarg.DataType = ua.NodeId(ua.ObjectIds.String)

        # One method rather than eight more variables. These change only
        # when a pump is refitted, and `points` is a list and `file` a
        # string - neither fits the FLOAT `value` column that every
        # published variable ends up in.
        await self.node.add_method(
            idx,
            f"{self.id}:get_calibration",
            get_calibration,
            [],
            [outarg],
        )

        for name, callback, inargs in (
            ("calibrate_point", calibrate_point, [inarg_duty, inarg_seconds]),
            ("record_point", record_point, [inarg_volume]),
            ("fit_calibration", fit_calibration, []),
            (
                "fit_calibration_with_zero_flow",
                fit_calibration_with_zero_flow,
                [inarg_zero_flow],
            ),
            ("discard_pending_point", discard_pending_point, []),
            ("clear_points", clear_points, []),
            ("reload_calibration", reload_calibration, []),
            ("set_duties", set_duties, [inarg_min_duty, inarg_dispense]),
        ):
            await self.node.add_method(
                idx,
                f"{self.id}:{name}",
                callback,
                inargs,
                [outarg],
            )

    async def init_reset_method(self, idx: int) -> None:
        """Expose the accrued-volume reset on a calibrated pump node.

        A short method: it banks delivery through now, zeroes the total
        and publishes the zero synchronously, so it never goes through the
        slow-session path the calibration run needs.
        """

        @uamethod
        async def reset_total_volume(parent: Node) -> tuple[bool, str]:
            """Zero the delivered-volume counter while delivery continues."""
            return await self.reset_total_volume()

        await self.node.add_method(
            idx,
            f"{self.id}:reset_total_volume",
            reset_total_volume,
            [],
            [
                _argument("Accepted", ua.ObjectIds.Boolean),
                _argument("Message", ua.ObjectIds.String),
            ],
        )
