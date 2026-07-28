"""OPC-UA Actuator node."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from asyncua import ua, uamethod

from reactors_czlab.core.calibration import CalibrationRun
from reactors_czlab.core.data import ControlConfig, ControlMethod, OutputUnit

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


class ActuatorOpc:
    """Actuator node."""

    def __init__(self, actuator: Actuator) -> None:
        """Initialize the OPC actuator node."""
        self.actuator = actuator
        self.id = actuator.id
        self.run = CalibrationRun(actuator)

    def __repr__(self) -> str:
        """Print actuator id."""
        return f"ActuatorOpc(id: {self.actuator.id})"

    async def update_value(self) -> None:
        """Publish the actuator output and pump data if they changed."""
        published = await self.curr_value.get_value()
        # old_value is what write_output() last pushed to the hardware.
        current = self.actuator.channel.old_value
        if current != published:
            await self.curr_value.write_value(float(current))
            _logger.debug("Updated %s with value %s", self.id, current)

        await self.total_volume.write_value(
            float(self.actuator.dispenser.total_volume),
        )
        cal = self.actuator.channel.calibration
        if cal is not None:
            await self.cal_a.write_value(float(cal.a))
            await self.cal_b.write_value(float(cal.b))
            await self.cal_r2.write_value(float(cal.r2))

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
        # Start a subscription to the variables in the control
        await self.init_control_subscription(server)
        # Expose the pump calibration workflow
        await self.init_calibration_methods(idx)

    async def init_control_subscription(self, server: Server) -> None:
        """Create a subscription to the control parameters."""
        sub = await server.create_subscription(500, self)
        on_config = await self.control_method.get_variables()
        await sub.subscribe_data_change(on_config)

    async def datachange_notification(
        self,
        node: Node,
        val: float,
        data: object,
    ) -> None:
        """Read the control configuration, and update the actuator."""
        _logger.debug("Config update: %s:%s:%s", self.actuator.id, node, val)
        index = await self.method.get_value()
        try:
            method = control_method[index]
        except KeyError:
            _logger.exception(
                "%s is not a member of %s",
                index,
                sorted(control_method),
            )
            return

        unit_index = await self.output_unit.get_value()
        try:
            unit = output_unit_map[unit_index]
        except KeyError:
            _logger.exception(
                "%s is not a member of %s",
                unit_index,
                sorted(output_unit_map),
            )
            return

        config = ControlConfig(
            method,
            value=await self.value.get_value(),
            output_unit=unit,
        )

        # Only read the variables the selected method actually needs.
        match method:
            case ControlMethod.manual:
                pass

            case ControlMethod.timer:
                config.time_on = await self.time_on.get_value()
                config.time_off = await self.time_off.get_value()

            case ControlMethod.on_boundaries:
                config.lb = await self.lb.get_value()
                config.ub = await self.ub.get_value()

            case ControlMethod.pid:
                config.setpoint = await self.setpoint.get_value()

        self.actuator.set_control_config(config)
        _logger.debug("Control config: %s", config)

    async def init_control_node(self, idx: int) -> None:
        """Add configuration variables for the control method.

        Every controller's parameters live side by side under one node; the
        client writes ``method`` plus the parameters that method uses.
        """
        # Add Node to store the control settings
        self.control_method = await self.node.add_object(
            idx,
            f"{self.id}:ControlMethod",
        )

        # Add variable to set the desired status
        self.value = await self.control_method.add_variable(
            idx,
            f"{self.id}:value",
            0.0,
        )
        await self.value.set_writable()

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
            0.0,
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
            0,
            varianttype=ua.VariantType.UInt32,
        )
        await self.method.set_writable()
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
            0,
            varianttype=ua.VariantType.UInt32,
        )
        await self.output_unit.set_writable()
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
            0.0,
        )
        await self.time_on.set_writable()
        self.time_off = await self.control_method.add_variable(
            idx,
            f"{self.id}:time_off",
            0.0,
        )
        await self.time_off.set_writable()

        # OnBoundariesControl
        self.lb = await self.control_method.add_variable(
            idx,
            f"{self.id}:lb",
            0.0,
        )
        await self.lb.set_writable()
        self.ub = await self.control_method.add_variable(
            idx,
            f"{self.id}:ub",
            0.0,
        )
        await self.ub.set_writable()

        # PidControl
        self.setpoint = await self.control_method.add_variable(
            idx,
            f"{self.id}:setpoint",
            0.0,
        )
        await self.setpoint.set_writable()

        # Sensor used as control variable
        self.curr_sensor = await self.control_method.add_variable(
            idx,
            f"{self.id}:reference_sensor",
            0,
            varianttype=ua.VariantType.UInt32,
        )
        await self.curr_sensor.set_writable()

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
        def clear_points(parent: Node) -> str:
            """Throw the collected points away."""
            return run.clear_points()

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
            """Adjust the stall floor and the bolus duty without a refit."""
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
            Text="Duty used for volume boluses",
        )

        outarg = ua.Argument()
        outarg.Name = "Status"
        outarg.DataType = ua.NodeId(ua.ObjectIds.String)

        for name, callback, inargs in (
            ("calibrate_point", calibrate_point, [inarg_duty, inarg_seconds]),
            ("record_point", record_point, [inarg_volume]),
            ("fit_calibration", fit_calibration, []),
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
