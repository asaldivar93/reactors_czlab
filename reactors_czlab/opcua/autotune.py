"""Reactor-owned OPC integration for non-blocking pH PID autotuning."""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from asyncua import ua, uamethod

from reactors_czlab.core.autotune import (
    AutotuneContext,
    AutotuneCoordinator,
    AutotunePhase,
    AutotuneRun,
    RelayTuneConfig,
    simc_pid,
    to_code_gains,
    tuning_rules,
    validate_autotune_selection,
)
from reactors_czlab.core.autotune_audit import AuditOutcome, AutotuneAudit
from reactors_czlab.core.control import ControlFactory
from reactors_czlab.core.data import ControlConfig, ControlMethod
from reactors_czlab.core.dispenser import check_unit
from reactors_czlab.opcua.actuator import ActuatorOpc, _argument

if TYPE_CHECKING:
    from asyncua.common.node import Node

    from reactors_czlab.core.reactor import Reactor

_logger = logging.getLogger("server.opcautotune")

AUTOTUNE_OPC_VERSION = 1


class ReactorAutotuneOpc:
    """Expose one reactor's coordinator through each eligible pump node."""

    def __init__(
        self,
        reactor: Reactor,
        actuator_nodes: Mapping[str, ActuatorOpc],
        *,
        audit: AutotuneAudit | None = None,
    ) -> None:
        """Bind the complete actuator-node map before methods are created."""
        self.reactor = reactor
        self.actuator_nodes = dict(actuator_nodes)
        self.context = AutotuneContext(
            reactor.id,
            reactor.volume,
            reactor.sensors,
            reactor.actuators,
            lambda: reactor.sampling.pairings,
        )
        self.audit = audit if audit is not None else AutotuneAudit(reactor.id)
        self.coordinator = AutotuneCoordinator(
            self.context,
            audit=self.audit,
        )
        # Synchronous boolean rather than an asyncio.Lock: start() is a
        # deliberately non-awaiting OPC method. A gain transaction raises
        # this before its first await and keeps it through controller locks,
        # readback publication/rollback, and audit recording, so start can
        # refuse without ever claiming the pumps mid-transaction.
        self._gain_change_in_progress = False
        self.reactor.autotune = self.coordinator
        for actuator_node in self.actuator_nodes.values():
            actuator_node.on_control_config_changed = (
                reactor.control_config_changed
            )

    async def init_methods(self, idx: int) -> None:
        """Create the public methods after all actuator OPC nodes exist."""
        for actuator_node in self.actuator_nodes.values():
            if actuator_node.actuator.channel.calibration is not None:
                await self._init_actuator_methods(actuator_node, idx)

    def preflight(
        self,
        base_id: str,
        sensor_id: str,
        acid_id: str,
        config: RelayTuneConfig,
    ) -> str:
        """Validate a proposed selection without claiming either pump."""
        if self.reactor.active_autotune_run() is not None:
            return self._response(
                False,
                f"{self.reactor.id} already has an active autotune",
                self._phase(),
            )
        try:
            run = AutotuneRun(
                self.context,
                sensor_id,
                base_id,
                acid_id,
                config,
            )
            flight = run.preflight()
        except Exception as exc:  # noqa: BLE001 - OPC validation boundary
            return self._response(False, str(exc), self._phase())
        return self._response(
            True,
            "autotune preflight passed",
            AutotunePhase.idle,
            selection={
                "sensor_id": sensor_id,
                "base_id": base_id,
                "acid_id": acid_id,
            },
            safety={
                "safe_low": flight.safe_low,
                "safe_high": flight.safe_high,
                "default_dose_budget_ml": flight.default_dose_budget_ml,
                "dose_budget_ml": flight.dose_budget_ml,
            },
            warnings=list(flight.warnings),
        )

    def start(
        self,
        base_id: str,
        sensor_id: str,
        acid_id: str,
        config: RelayTuneConfig,
    ) -> str:
        """Start the sample-driven workflow and return immediately."""
        if self._gain_change_in_progress:
            return self._response(
                False,
                "cannot start autotune while a gain change is in progress",
                self._phase(),
            )
        try:
            self.coordinator.start(sensor_id, base_id, acid_id, config)
        except Exception as exc:  # noqa: BLE001 - OPC validation boundary
            return self._response(False, str(exc), self._phase())
        return self.status()

    def status(self) -> str:
        """Return the current or most recent bounded server-owned snapshot."""
        run = self.coordinator.run
        if run is None:
            return self._response(
                True,
                "no autotune has been started",
                AutotunePhase.idle,
                current_ph=None,
                relay_direction="none",
                elapsed_seconds=0.0,
                trace=[],
                cycles=[],
                candidate_gains={},
            )
        snapshot = run.status()
        latest = snapshot.trace[-1] if snapshot.trace else None
        if latest is None or latest.requested_volume_ml == 0:
            relay_direction = "none"
        elif latest.requested_volume_ml > 0:
            relay_direction = "base"
        else:
            relay_direction = "acid"
        candidates = self._candidate_gains(run)
        result = asdict(snapshot.result) if snapshot.result is not None else None
        adjusted_doses = {
            "base": snapshot.base_dose_ml,
            "acid": snapshot.acid_dose_ml,
        }
        return self._response(
            True,
            snapshot.message,
            snapshot.phase,
            selection={
                "sensor_id": run.sensor_id,
                "base_id": run.base_id,
                "acid_id": run.acid_id,
            },
            setpoint=run.config.setpoint,
            hysteresis_ph=run.config.hysteresis,
            chemistry={
                "phosphate_molar": run.config.phosphate_molar,
                "base_molar": run.config.base_molar,
                "acid_molar": run.config.acid_molar,
            },
            max_minutes=run.config.max_minutes,
            current_ph=None if latest is None else latest.ph,
            relay_direction=relay_direction,
            elapsed_seconds=snapshot.elapsed_seconds,
            adjusted_doses_ml=adjusted_doses,
            # Deprecated mixed-version alias. Do not use in business logic.
            adjusted_boluses_ml=dict(adjusted_doses),
            dose={
                "actual_ml": snapshot.actual_dose_ml,
                "budget_ml": snapshot.dose_budget_ml,
            },
            safety={
                "safe_low": snapshot.safe_low,
                "safe_high": snapshot.safe_high,
            },
            noise_sigma=snapshot.noise_sigma,
            settling_cycles=snapshot.settling_cycles,
            clean_cycles=snapshot.clean_cycles,
            warnings=list(snapshot.warnings),
            trace=[asdict(item) for item in snapshot.trace],
            switch_times=list(snapshot.switch_times),
            cycles=[asdict(item) for item in snapshot.cycles],
            result=result,
            candidate_gains=candidates,
        )

    def abort(self, receiving_id: str) -> str:
        """Abort the active run from its selected base node."""
        run = self.coordinator.run
        if run is None or not run.is_active:
            return self._response(False, "there is no active autotune", self._phase())
        if run.base_id != receiving_id:
            return self._response(
                False,
                f"autotune is owned by base pump {run.base_id}",
                run.phase,
            )
        run.abort()
        return self.status()

    async def apply(self, receiving_id: str, rule: str) -> str:
        """Apply an identified rule to both selected PID controllers."""
        refusal = self._begin_gain_change()
        if refusal is not None:
            return refusal
        try:
            run = self.coordinator.run
            if run is None or run.base_id != receiving_id:
                return self._response(
                    False,
                    "this actuator has no identified autotune selection",
                    self._phase(),
                )
            return await self._apply_outcome(
                receiving_id,
                "apply",
                self.audit.prepare_apply(run, rule),
            )
        finally:
            self._gain_change_in_progress = False

    async def scale(self, receiving_id: str, target_ph: float) -> str:
        """Scale the last applied gains directly to ``target_ph``."""
        refusal = self._begin_gain_change()
        if refusal is not None:
            return refusal
        try:
            return await self._apply_outcome(
                receiving_id,
                "scale",
                self.audit.prepare_scale(self.context, target_ph),
            )
        finally:
            self._gain_change_in_progress = False

    async def reapply(self, receiving_id: str) -> str:
        """Reapply the last persisted gains after full revalidation."""
        refusal = self._begin_gain_change()
        if refusal is not None:
            return refusal
        try:
            return await self._apply_outcome(
                receiving_id,
                "reapply",
                self.audit.prepare_reapply(self.context),
            )
        finally:
            self._gain_change_in_progress = False

    async def _apply_outcome(
        self,
        receiving_id: str,
        action: str,
        outcome: AuditOutcome,
    ) -> str:
        active = self.reactor.active_autotune_run()
        if active is not None:
            return self._response(
                False,
                "autotune gains cannot change while a run is active",
                active.phase,
            )
        if not outcome.ok or outcome.data is None:
            return self._response(False, outcome.message, self._phase())
        candidate = outcome.data
        ok, message = await self._apply_candidate(
            receiving_id,
            action,
            candidate,
        )
        if not ok:
            audit = self.audit.record_apply_failure(action, candidate, message)
            if not audit.ok:
                message = f"{message}; {audit.message}"
            return self._response(False, message, self._phase())

        match action:
            case "apply":
                audit = self.audit.record_apply_success(candidate)
            case "scale":
                audit = self.audit.record_scale_success(candidate)
            case "reapply":
                audit = self.audit.record_reapply_success(candidate)
            case _:
                error_message = f"unsupported autotune action {action!r}"
                raise ValueError(error_message)
        if not audit.ok:
            message = f"{message}; {audit.message}"
        return self._response(
            True,
            message,
            self._phase(),
            candidate=dict(candidate),
        )

    async def _apply_candidate(
        self,
        receiving_id: str,
        expected_action: str,
        candidate: Mapping[str, Any],
    ) -> tuple[bool, str]:
        """Validate both complete PID configs, apply both, or roll back."""
        try:
            parsed = self._parse_candidate(candidate)
            if parsed["action"] != expected_action:
                error_message = (
                    f"candidate action {parsed['action']!r} does not match "
                    f"{expected_action!r}"
                )
                raise ValueError(error_message)
            if parsed["reactor_id"] != self.reactor.id:
                error_message = "autotune candidate belongs to another reactor"
                raise ValueError(error_message)
            if parsed["base_id"] != receiving_id:
                error_message = (
                    f"autotune candidate belongs to base pump {parsed['base_id']}"
                )
                raise ValueError(error_message)
            base_node = self.actuator_nodes[parsed["base_id"]]
            acid_node = self.actuator_nodes[parsed["acid_id"]]
        except (KeyError, TypeError, ValueError) as exc:
            return (False, f"cannot apply autotune gains: {exc}")

        nodes = sorted((base_node, acid_node), key=lambda item: item.id)
        async with AsyncExitStack() as stack:
            for node in nodes:
                await stack.enter_async_context(node._config_lock)
            try:
                active = self.reactor.active_autotune_run()
                if active is not None:
                    error_message = (
                        "autotune gains cannot change while a run is active"
                    )
                    raise ValueError(error_message)
                channel_index = validate_autotune_selection(
                    self.context,
                    parsed["sensor_id"],
                    parsed["base_id"],
                    parsed["acid_id"],
                    parsed["reference_ph"],
                )
                if channel_index != parsed["channel_index"]:
                    error_message = (
                        "selected pH channel no longer matches the candidate"
                    )
                    raise ValueError(error_message)

                old_configs = {
                    node.id: self._pid_config(node) for node in nodes
                }
                new_configs = {
                    node.id: self._pid_config(node, parsed["gains"])
                    for node in nodes
                }
                # Constructing both controllers is the non-mutating complete
                # validation pass. No live object changes until both pass.
                for node in nodes:
                    self._validate_complete_config(node, new_configs[node.id])
            except (TypeError, ValueError) as exc:
                return (False, f"cannot apply autotune gains: {exc}")

            try:
                for node in nodes:
                    reason = node.actuator.set_control_config(
                        new_configs[node.id],
                    )
                    if reason is not None:
                        error_message = f"{node.id} rejected gains: {reason}"
                        raise RuntimeError(error_message)
                for node in nodes:
                    await node.publish_pid_gains()
            except Exception as exc:  # noqa: BLE001 - rollback safety boundary
                rollback_errors: list[str] = []
                for node in nodes:
                    try:
                        reason = node.actuator.set_control_config(
                            old_configs[node.id],
                        )
                        if reason is not None:
                            rollback_errors.append(f"{node.id}: {reason}")
                    except Exception as rollback_exc:  # noqa: BLE001
                        rollback_errors.append(f"{node.id}: {rollback_exc}")
                for node in nodes:
                    try:
                        await node.publish_pid_gains()
                    except Exception as publish_exc:  # noqa: BLE001
                        rollback_errors.append(
                            f"{node.id} readback: {publish_exc}"
                        )
                message = f"atomic autotune gain apply failed: {exc}"
                if rollback_errors:
                    message += "; rollback errors: " + "; ".join(
                        rollback_errors,
                    )
                return (False, message)

        callbacks = {
            id(node.on_state_changed): node.on_state_changed
            for node in nodes
            if node.on_state_changed is not None
        }
        for callback in callbacks.values():
            try:
                callback()
            except Exception:
                _logger.exception("Autotune-gain checkpoint callback failed")

        gains = parsed["gains"]
        return (
            True,
            (
                f"applied {parsed['rule']} gains to {parsed['base_id']} and "
                f"{parsed['acid_id']}: kp={gains[0]:.6g}, "
                f"ki={gains[1]:.6g}, kd={gains[2]:.6g}"
            ),
        )

    @staticmethod
    def _parse_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and unpack the versioned audit candidate shape."""
        match candidate:
            case {
                "action": str(action),
                "reactor_id": str(reactor_id),
                "sensor_id": str(sensor_id),
                "base_id": str(base_id),
                "acid_id": str(acid_id),
                "channel_index": int(channel_index),
                "rule": str(rule),
                "reference_ph": (int() | float()) as reference_ph,
                "gains": {
                    "kp": (int() | float()) as kp,
                    "ki": (int() | float()) as ki,
                    "kd": (int() | float()) as kd,
                },
            }:
                pass
            case _:
                error_message = "autotune gain candidate has an unsupported shape"
                raise TypeError(error_message)
        numeric = (reference_ph, kp, ki, kd)
        if any(isinstance(value, bool) for value in (*numeric, channel_index)):
            error_message = "autotune gain candidate contains boolean numbers"
            raise TypeError(error_message)
        if channel_index < 0 or not all(math.isfinite(float(value)) for value in numeric):
            error_message = "autotune gain candidate contains invalid numbers"
            raise ValueError(error_message)
        return {
            "action": action,
            "reactor_id": reactor_id,
            "sensor_id": sensor_id,
            "base_id": base_id,
            "acid_id": acid_id,
            "channel_index": channel_index,
            "rule": rule,
            "reference_ph": float(reference_ph),
            "gains": (float(kp), float(ki), float(kd)),
        }

    @staticmethod
    def _pid_config(
        node: ActuatorOpc,
        gains: tuple[float, float, float] | None = None,
    ) -> ControlConfig:
        controller = node.actuator.controller
        if controller.method is not ControlMethod.pid:
            error_message = f"{node.id} no longer uses PID control"
            raise ValueError(error_message)
        selected = (
            (controller.kp, controller.ki, controller.kd)
            if gains is None
            else gains
        )
        return ControlConfig(
            ControlMethod.pid,
            output_unit=node.actuator.dispenser.unit,
            setpoint=controller.setpoint,
            kp=selected[0],
            ki=selected[1],
            kd=selected[2],
            min_integral=controller.min_integral,
            max_integral=controller.max_integral,
            auto_integral_band=controller._integral_band_is_default,
            backwards=controller.backwards,
        )

    @staticmethod
    def _validate_complete_config(
        node: ActuatorOpc,
        config: ControlConfig,
    ) -> None:
        reason = check_unit(config.output_unit, node.actuator.channel)
        if reason is not None:
            error_message = f"{node.id}: {reason}"
            raise ValueError(error_message)
        min_val, max_val = node.actuator.dispenser.demand_limits(
            pid=config.method is ControlMethod.pid,
        )
        ControlFactory().create_control(
            config,
            min_val=min_val,
            max_val=max_val,
        )

    @staticmethod
    def _candidate_gains(run: AutotuneRun) -> dict[str, dict[str, float]]:
        if run.result is None:
            return {}
        identified = run.result.identification
        continuous = tuning_rules(identified.Ku, identified.Pu)
        continuous["SIMC"] = simc_pid(identified.Ku, identified.Pu)
        return {
            name: dict(zip(("kp", "ki", "kd"), to_code_gains(*values), strict=True))
            for name, values in continuous.items()
        }

    def _phase(self) -> AutotunePhase:
        run = self.coordinator.run
        return AutotunePhase.idle if run is None else run.phase

    def _begin_gain_change(self) -> str | None:
        """Synchronously reserve the reactor for one gain transaction."""
        active = self.reactor.active_autotune_run()
        if active is not None:
            return self._response(
                False,
                "autotune gains cannot change while a run is active",
                active.phase,
            )
        if self._gain_change_in_progress:
            return self._response(
                False,
                "another autotune gain change is already in progress",
                self._phase(),
            )
        self._gain_change_in_progress = True
        return None

    @staticmethod
    def _response(
        ok: bool,
        message: str,
        phase: AutotunePhase,
        **data: Any,
    ) -> str:
        return json.dumps(
            {
                "version": AUTOTUNE_OPC_VERSION,
                "ok": ok,
                "message": message,
                "phase": phase.value,
                **data,
            },
            allow_nan=False,
        )

    async def _init_actuator_methods(
        self,
        actuator_node: ActuatorOpc,
        idx: int,
    ) -> None:
        base_id = actuator_node.id

        @uamethod
        def autotune_preflight(
            parent: Node,
            sensor_id: str,
            acid_id: str,
            setpoint: float,
            base_dose_ml: float,
            acid_dose_ml: float,
            hysteresis_ph: float,
            max_minutes: float,
            phosphate_molar: float,
            base_molar: float,
            acid_molar: float,
            dose_budget_ml: float,
            acknowledge_other_loops: bool,
            acknowledge_budget_override: bool,
        ) -> str:
            config = self._config(
                setpoint,
                base_dose_ml,
                acid_dose_ml,
                hysteresis_ph,
                max_minutes,
                phosphate_molar,
                base_molar,
                acid_molar,
                dose_budget_ml,
                acknowledge_other_loops,
                acknowledge_budget_override,
            )
            return self.preflight(base_id, sensor_id, acid_id, config)

        @uamethod
        def autotune_start(
            parent: Node,
            sensor_id: str,
            acid_id: str,
            setpoint: float,
            base_dose_ml: float,
            acid_dose_ml: float,
            hysteresis_ph: float,
            max_minutes: float,
            phosphate_molar: float,
            base_molar: float,
            acid_molar: float,
            dose_budget_ml: float,
            acknowledge_other_loops: bool,
            acknowledge_budget_override: bool,
        ) -> str:
            config = self._config(
                setpoint,
                base_dose_ml,
                acid_dose_ml,
                hysteresis_ph,
                max_minutes,
                phosphate_molar,
                base_molar,
                acid_molar,
                dose_budget_ml,
                acknowledge_other_loops,
                acknowledge_budget_override,
            )
            return self.start(base_id, sensor_id, acid_id, config)

        @uamethod
        def autotune_status(parent: Node) -> str:
            return self.status()

        @uamethod
        def autotune_abort(parent: Node) -> str:
            return self.abort(base_id)

        @uamethod
        async def autotune_apply(parent: Node, rule: str) -> str:
            return await self.apply(base_id, rule)

        @uamethod
        async def autotune_scale_to_setpoint(
            parent: Node,
            target_ph: float,
        ) -> str:
            return await self.scale(base_id, target_ph)

        @uamethod
        async def autotune_reapply_last(parent: Node) -> str:
            return await self.reapply(base_id)

        common = [
            _argument("Sensor_id", ua.ObjectIds.String),
            _argument("Acid_id", ua.ObjectIds.String),
            *(
                _argument(name, ua.ObjectIds.Double)
                for name in (
                    "Setpoint",
                    "Base_dose_ml",
                    "Acid_dose_ml",
                    "Hysteresis_ph",
                    "Max_minutes",
                    "Phosphate_molar",
                    "Base_molar",
                    "Acid_molar",
                    "Dose_budget_ml",
                )
            ),
            _argument("Acknowledge_other_loops", ua.ObjectIds.Boolean),
            _argument("Acknowledge_budget_override", ua.ObjectIds.Boolean),
        ]
        outarg = [_argument("Status", ua.ObjectIds.String)]
        for name, callback, inargs in (
            ("autotune_preflight", autotune_preflight, common),
            ("autotune_start", autotune_start, common),
            ("autotune_status", autotune_status, []),
            ("autotune_abort", autotune_abort, []),
            (
                "autotune_apply",
                autotune_apply,
                [_argument("Rule", ua.ObjectIds.String)],
            ),
            (
                "autotune_scale_to_setpoint",
                autotune_scale_to_setpoint,
                [_argument("Target_ph", ua.ObjectIds.Double)],
            ),
            ("autotune_reapply_last", autotune_reapply_last, []),
        ):
            await actuator_node.node.add_method(
                idx,
                f"{base_id}:{name}",
                callback,
                inargs,
                outarg,
            )

    @staticmethod
    def _config(
        setpoint: float,
        base_dose_ml: float,
        acid_dose_ml: float,
        hysteresis_ph: float,
        max_minutes: float,
        phosphate_molar: float,
        base_molar: float,
        acid_molar: float,
        dose_budget_ml: float,
        acknowledge_other_loops: bool,
        acknowledge_budget_override: bool,
    ) -> RelayTuneConfig:
        # OPC has no nullable numeric argument. Zero means "use the
        # chemistry-derived default"; negative values remain explicit and
        # are refused by the core validator.
        budget = None if dose_budget_ml == 0 else dose_budget_ml
        return RelayTuneConfig(
            setpoint=setpoint,
            base_dose_ml=base_dose_ml,
            acid_dose_ml=acid_dose_ml,
            hysteresis=hysteresis_ph,
            max_minutes=max_minutes,
            phosphate_molar=phosphate_molar,
            base_molar=base_molar,
            acid_molar=acid_molar,
            dose_budget_ml=budget,
            acknowledge_other_loops=acknowledge_other_loops,
            acknowledge_budget_override=acknowledge_budget_override,
        )
