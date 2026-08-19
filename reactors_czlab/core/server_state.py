"""Durable, fail-safe checkpoints for the Raspberry Pi server.

The snapshot contains operator configuration and accumulated dispenser totals,
not live process state. Sensor values, physical outputs, controller memory,
delivery deadlines, calibration runs, and autotune ownership deliberately never
cross a process boundary.
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Never

from reactors_czlab.core.control import ControlFactory
from reactors_czlab.core.data import ControlConfig, ControlMethod, OutputUnit
from reactors_czlab.core.dispenser import Dispenser, check_unit
from reactors_czlab.core.reactor import MAX_SAMPLE_PERIOD, MIN_SAMPLE_PERIOD, Reactor

_logger = logging.getLogger("server.state")

SCHEMA_VERSION = 1
CHECKPOINT_INTERVAL = 60.0

_CONTROL_NUMERIC_FIELDS = (
    "time_on",
    "time_off",
    "lb",
    "ub",
    "setpoint",
    "value",
    "kp",
    "ki",
    "kd",
    "min_integral",
    "max_integral",
)
_CONTROL_BOOLEAN_FIELDS = ("backwards", "auto_integral_band")
_CONTROL_FIELDS = {
    "method",
    "output_unit",
    *_CONTROL_NUMERIC_FIELDS,
    *_CONTROL_BOOLEAN_FIELDS,
}


class StateValidationError(ValueError):
    """A checkpoint is malformed or incompatible with the live server."""


@dataclass(frozen=True)
class _ActuatorRecovery:
    """Validated state for one live actuator."""

    actuator_id: str
    config: ControlConfig
    total_volume: float
    paired: bool


@dataclass(frozen=True)
class _ReactorRecovery:
    """Validated state for one live reactor."""

    reactor: Reactor
    pairings: tuple[tuple[str, str, int], ...]
    actuators: tuple[_ActuatorRecovery, ...]


@dataclass(frozen=True)
class _RecoveryPlan:
    """A complete checkpoint that is safe to apply as one transaction."""

    sampling_period: float
    reactors: tuple[_ReactorRecovery, ...]


def default_state_file(environ: Mapping[str, str] | None = None) -> Path:
    """Return the configured state path or the per-user default.

    Parameters
    ----------
    environ:
        Environment mapping, injectable for tests. Defaults to ``os.environ``.

    """
    environment = os.environ if environ is None else environ
    configured = environment.get("REACTORS_STATE_FILE")
    if configured:
        return Path(configured).expanduser()
    return Path("~/.reactors_czlab/server-state.json").expanduser()


def control_config_from_actuator(actuator: Any) -> ControlConfig:
    """Build a complete serialisable config from an actuator's live objects."""
    controller = actuator.controller
    config = ControlConfig(
        method=controller.method,
        output_unit=actuator.dispenser.unit,
    )
    match controller.method:
        case ControlMethod.manual:
            config.value = controller.value
        case ControlMethod.timer:
            config.time_on = controller.time_on
            config.time_off = controller.time_off
            config.value = controller.value_on
        case ControlMethod.on_boundaries:
            config.lb = controller.lower_bound
            config.ub = controller.upper_bound
            config.value = controller.value_on
            config.backwards = controller.backwards
        case ControlMethod.pid:
            config.setpoint = controller.setpoint
            config.kp = controller.kp
            config.ki = controller.ki
            config.kd = controller.kd
            config.backwards = controller.backwards
            config.min_integral = controller.min_integral
            config.max_integral = controller.max_integral
            config.auto_integral_band = controller._integral_band_is_default
    return config


def _control_payload(actuator: Any) -> dict[str, object]:
    """Return every persisted field of one controller configuration."""
    config = control_config_from_actuator(actuator)
    payload = asdict(config)
    payload["method"] = config.method.value
    payload["output_unit"] = config.output_unit.value
    return payload


def _channels_payload(channels: list[Any]) -> list[dict[str, object]]:
    """Describe ordered channels without copying readings or calibration."""
    return [{"index": index, "units": str(channel.units)} for index, channel in enumerate(channels)]


def snapshot(reactors: list[Reactor], sampling_period: float) -> dict[str, object]:
    """Build a JSON-compatible checkpoint from the live server state."""
    reactor_rows: list[dict[str, object]] = []
    for reactor in sorted(reactors, key=lambda item: item.id):
        pairings = [
            {"sensor": sensor_id, "actuator": actuator_id, "channel": channel}
            for sensor_id, paired in reactor.sampling.pairings.items()
            for actuator_id, channel in paired
        ]
        pairings.sort(
            key=lambda row: (row["sensor"], row["actuator"], row["channel"]),
        )
        reactor_rows.append(
            {
                "id": reactor.id,
                "sensors": [
                    {
                        "id": sensor.id,
                        "channels": _channels_payload(sensor.channels),
                    }
                    for sensor in sorted(reactor.sensors.values(), key=lambda item: item.id)
                ],
                "actuators": [
                    {
                        "id": actuator.id,
                        "channels": _channels_payload(actuator.info.channels),
                        "control": _control_payload(actuator),
                        "total_volume": actuator.dispenser.total_volume,
                    }
                    for actuator in sorted(reactor.actuators.values(), key=lambda item: item.id)
                ],
                "pairings": pairings,
            },
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "sampling_period": sampling_period,
        "reactors": reactor_rows,
    }


def _fail(message: str) -> Never:
    """Raise the checkpoint validation error used at every boundary."""
    raise StateValidationError(message)


def _exact_mapping(
    value: object,
    keys: set[str],
    context: str,
) -> dict[str, object]:
    """Require a JSON object with exactly ``keys``."""
    if not isinstance(value, dict):
        _fail(f"{context} must be an object")
    actual = set(value)
    if actual != keys:
        _fail(f"{context} fields differ: expected {sorted(keys)}, got {sorted(actual)}")
    return value


def _list(value: object, context: str) -> list[object]:
    """Require a JSON array."""
    if not isinstance(value, list):
        _fail(f"{context} must be an array")
    return value


def _string(value: object, context: str) -> str:
    """Require a non-empty JSON string."""
    if not isinstance(value, str) or not value:
        _fail(f"{context} must be a non-empty string")
    return value


def _number(value: object, context: str, *, nonnegative: bool = False) -> float:
    """Require a finite JSON number, optionally non-negative."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        _fail(f"{context} must be a number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        _fail(f"{context} must be a finite representable number: {exc}")
    if not math.isfinite(number):
        _fail(f"{context} must be finite")
    if nonnegative and number < 0.0:
        _fail(f"{context} must be non-negative")
    return number


def _integer(value: object, context: str) -> int:
    """Require a JSON integer, excluding booleans."""
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{context} must be an integer")
    return value


def _validate_channels(
    raw: object,
    live_channels: list[Any],
    context: str,
) -> None:
    """Require exact ordered channel indexes and unit labels."""
    rows = _list(raw, f"{context}.channels")
    expected = _channels_payload(live_channels)
    normalised: list[dict[str, object]] = []
    for position, value in enumerate(rows):
        row = _exact_mapping(
            value,
            {"index", "units"},
            f"{context}.channels[{position}]",
        )
        normalised.append(
            {
                "index": _integer(row["index"], f"{context}.channels[{position}].index"),
                "units": _string(row["units"], f"{context}.channels[{position}].units"),
            },
        )
    if normalised != expected:
        _fail(f"{context} channel topology differs: expected {expected}, got {normalised}")


def _validate_control(raw: object, actuator: Any, context: str) -> ControlConfig:
    """Validate all fields and calibration-dependent demand limits."""
    fields = _exact_mapping(raw, _CONTROL_FIELDS, context)
    try:
        method = ControlMethod(_string(fields["method"], f"{context}.method"))
    except ValueError as exc:
        _fail(f"{context}.method is unsupported: {exc}")
    try:
        unit = OutputUnit(_string(fields["output_unit"], f"{context}.output_unit"))
    except ValueError as exc:
        _fail(f"{context}.output_unit is unsupported: {exc}")

    values = {name: _number(fields[name], f"{context}.{name}") for name in _CONTROL_NUMERIC_FIELDS}
    booleans: dict[str, bool] = {}
    for name in _CONTROL_BOOLEAN_FIELDS:
        value = fields[name]
        if not isinstance(value, bool):
            _fail(f"{context}.{name} must be a boolean")
        booleans[name] = value

    config = ControlConfig(
        method=method,
        output_unit=unit,
        **values,
        **booleans,
    )
    if method is ControlMethod.timer and (config.time_on < 0.0 or config.time_off < 0.0):
        _fail(f"{context} timer intervals must be non-negative")
    if method is ControlMethod.on_boundaries and config.lb > config.ub:
        _fail(f"{context} lower boundary may not exceed upper boundary")

    reason = check_unit(unit, actuator.channel)
    if reason is not None:
        _fail(f"{context} is incompatible with the current calibration: {reason}")
    dispenser = Dispenser(unit, actuator.channel, actuator.control_period)
    min_val, max_val = dispenser.demand_limits(
        pid=method is ControlMethod.pid,
    )
    try:
        ControlFactory().create_control(
            config,
            min_val=min_val,
            max_val=max_val,
        )
    except (TypeError, ValueError) as exc:
        _fail(f"{context} is invalid: {exc}")
    return config


def _validate_device_rows(
    raw: object,
    live: Mapping[str, Any],
    context: str,
    *,
    actuator: bool,
) -> dict[str, dict[str, object]]:
    """Validate unique device IDs and return rows keyed by live ID."""
    rows: dict[str, dict[str, object]] = {}
    expected_keys = {"id", "channels", "control", "total_volume"} if actuator else {"id", "channels"}
    for position, value in enumerate(_list(raw, context)):
        row_context = f"{context}[{position}]"
        row = _exact_mapping(value, expected_keys, row_context)
        device_id = _string(row["id"], f"{row_context}.id")
        if device_id in rows:
            _fail(f"{context} contains duplicate id {device_id!r}")
        if device_id not in live:
            _fail(f"{context} contains unknown id {device_id!r}")
        channels = live[device_id].info.channels if actuator else live[device_id].channels
        _validate_channels(row["channels"], channels, row_context)
        rows[device_id] = row
    if set(rows) != set(live):
        _fail(f"{context} topology differs: expected {sorted(live)}, got {sorted(rows)}")
    return rows


def _validate_pairings(
    raw: object,
    reactor: Reactor,
    context: str,
) -> tuple[tuple[str, str, int], ...]:
    """Validate IDs, channel indexes, and one-sensor-per-actuator uniqueness."""
    result: list[tuple[str, str, int]] = []
    paired_actuators: set[str] = set()
    seen_rows: set[tuple[str, str, int]] = set()
    for position, value in enumerate(_list(raw, context)):
        row_context = f"{context}[{position}]"
        row = _exact_mapping(
            value,
            {"sensor", "actuator", "channel"},
            row_context,
        )
        sensor_id = _string(row["sensor"], f"{row_context}.sensor")
        actuator_id = _string(row["actuator"], f"{row_context}.actuator")
        channel = _integer(row["channel"], f"{row_context}.channel")
        if sensor_id not in reactor.sensors:
            _fail(f"{row_context} names unknown sensor {sensor_id!r}")
        if actuator_id not in reactor.actuators:
            _fail(f"{row_context} names unknown actuator {actuator_id!r}")
        if channel < 0 or channel >= len(reactor.sensors[sensor_id].channels):
            _fail(f"{row_context}.channel {channel} is outside sensor {sensor_id!r}'s channel indexes")
        item = (sensor_id, actuator_id, channel)
        if item in seen_rows:
            _fail(f"{context} contains duplicate pairing {item!r}")
        if actuator_id in paired_actuators:
            _fail(f"{actuator_id!r} is paired more than once")
        seen_rows.add(item)
        paired_actuators.add(actuator_id)
        result.append(item)
    return tuple(result)


def validate_snapshot(raw: object, reactors: list[Reactor]) -> _RecoveryPlan:
    """Validate a whole checkpoint without changing a live object.

    Raises
    ------
    StateValidationError
        If any schema, topology, pairing, numeric, enum, or calibration check
        fails. Callers must apply only the returned complete plan.

    """
    root = _exact_mapping(
        raw,
        {"schema_version", "sampling_period", "reactors"},
        "checkpoint",
    )
    version = _integer(root["schema_version"], "checkpoint.schema_version")
    if version != SCHEMA_VERSION:
        _fail(f"unsupported schema version {version}; expected {SCHEMA_VERSION}")
    period = _number(root["sampling_period"], "checkpoint.sampling_period")
    if period < MIN_SAMPLE_PERIOD or period > MAX_SAMPLE_PERIOD:
        _fail(f"checkpoint.sampling_period must be between {MIN_SAMPLE_PERIOD:g} and {MAX_SAMPLE_PERIOD:g} seconds")

    live_by_id = {reactor.id: reactor for reactor in reactors}
    if len(live_by_id) != len(reactors):
        _fail("live reactor ids are not unique")
    plans: list[_ReactorRecovery] = []
    seen_reactors: set[str] = set()
    for position, value in enumerate(_list(root["reactors"], "checkpoint.reactors")):
        context = f"checkpoint.reactors[{position}]"
        row = _exact_mapping(
            value,
            {"id", "sensors", "actuators", "pairings"},
            context,
        )
        reactor_id = _string(row["id"], f"{context}.id")
        if reactor_id in seen_reactors:
            _fail(f"checkpoint contains duplicate reactor id {reactor_id!r}")
        if reactor_id not in live_by_id:
            _fail(f"checkpoint contains unknown reactor id {reactor_id!r}")
        seen_reactors.add(reactor_id)
        reactor = live_by_id[reactor_id]
        _validate_device_rows(
            row["sensors"],
            reactor.sensors,
            f"{context}.sensors",
            actuator=False,
        )
        actuator_rows = _validate_device_rows(
            row["actuators"],
            reactor.actuators,
            f"{context}.actuators",
            actuator=True,
        )
        pairings = _validate_pairings(row["pairings"], reactor, f"{context}.pairings")
        paired_ids = {actuator_id for _, actuator_id, _ in pairings}
        actuator_plans: list[_ActuatorRecovery] = []
        for actuator_id, actuator in reactor.actuators.items():
            actuator_row = actuator_rows[actuator_id]
            config = _validate_control(
                actuator_row["control"],
                actuator,
                f"{context}.actuators[{actuator_id!r}].control",
            )
            total = _number(
                actuator_row["total_volume"],
                f"{context}.actuators[{actuator_id!r}].total_volume",
                nonnegative=True,
            )
            actuator_plans.append(
                _ActuatorRecovery(
                    actuator_id,
                    config,
                    total,
                    actuator_id in paired_ids,
                ),
            )
        plans.append(
            _ReactorRecovery(reactor, pairings, tuple(actuator_plans)),
        )
    if seen_reactors != set(live_by_id):
        _fail(f"checkpoint reactor topology differs: expected {sorted(live_by_id)}, got {sorted(seen_reactors)}")
    return _RecoveryPlan(period, tuple(plans))


def _safe_config(config: ControlConfig, *, paired: bool) -> ControlConfig:
    """Apply restart semantics without carrying live controller memory."""
    match config.method, paired:
        case ControlMethod.manual, _:
            return ControlConfig(
                ControlMethod.manual,
                value=0.0,
                output_unit=config.output_unit,
            )
        case ControlMethod.timer, _:
            return replace(config)
        case ((ControlMethod.on_boundaries | ControlMethod.pid), True):
            return replace(config)
        case ((ControlMethod.on_boundaries | ControlMethod.pid), False):
            return ControlConfig(
                ControlMethod.manual,
                value=0.0,
                output_unit=config.output_unit,
            )


def _reset_to_safe_defaults(
    reactors: list[Reactor],
    periods: Mapping[str, float],
) -> None:
    """Remove every partial effect after an unexpected apply-time failure."""
    for reactor in reactors:
        reactor.update_period(periods[reactor.id])
        reactor.sampling.pairings = defaultdict(list)
        reactor.unpaired.actuators = list(reactor.actuators)
        reactor.clear_recovery_gates()
        for actuator in reactor.actuators.values():
            actuator.set_control_config(
                ControlConfig(ControlMethod.manual, value=0.0),
            )
            actuator.dispenser.total_volume = 0.0
            actuator.dispenser.reset()
            actuator.write(0.0)
            actuator.channel.old_value = 0.0


def apply_recovery(plan: _RecoveryPlan) -> None:
    """Apply a fully validated recovery plan before any loop can run."""
    reactors = [item.reactor for item in plan.reactors]
    original_periods = {reactor.id: reactor.period for reactor in reactors}
    try:
        for reactor_plan in plan.reactors:
            reactor = reactor_plan.reactor
            reactor.update_period(plan.sampling_period)
            pairings: dict[str, list[tuple[str, int]]] = defaultdict(list)
            paired_ids: set[str] = set()
            for sensor_id, actuator_id, channel in reactor_plan.pairings:
                pairings[sensor_id].append((actuator_id, channel))
                paired_ids.add(actuator_id)
            reactor.sampling.pairings = pairings
            reactor.unpaired.actuators = [actuator_id for actuator_id in reactor.actuators if actuator_id not in paired_ids]

            for actuator_plan in reactor_plan.actuators:
                actuator = reactor.actuators[actuator_plan.actuator_id]
                config = _safe_config(
                    actuator_plan.config,
                    paired=actuator_plan.paired,
                )
                reason = actuator.set_control_config(config)
                if reason is not None:
                    error_message = f"validated config for {actuator.id} was rejected: {reason}"
                    raise StateValidationError(error_message)
                actuator.dispenser.total_volume = actuator_plan.total_volume
                actuator.dispenser.reset()
                actuator.write(0.0)
                actuator.channel.old_value = 0.0
                if config.method is ControlMethod.timer:
                    reactor.defer_recovered_timer(
                        actuator.id,
                        paired=actuator_plan.paired,
                    )
    except Exception as exc:
        _reset_to_safe_defaults(reactors, original_periods)
        if isinstance(exc, StateValidationError):
            raise
        error_message = f"checkpoint could not be applied atomically: {exc}"
        raise StateValidationError(error_message) from exc


class StateStore:
    """Load and atomically checkpoint one server state file."""

    def __init__(self, path: Path) -> None:
        """Store the path without touching the filesystem."""
        self.path = path.expanduser()
        self._last_totals: tuple[tuple[str, float], ...] | None = None

    @staticmethod
    def _totals(reactors: list[Reactor]) -> tuple[tuple[str, float], ...]:
        """Return a stable signature used to suppress unchanged writes."""
        return tuple(
            sorted((actuator.id, actuator.dispenser.total_volume) for reactor in reactors for actuator in reactor.actuators.values()),
        )

    def restore(self, reactors: list[Reactor]) -> float | None:
        """Restore a valid checkpoint, quarantine a rejected one, or do nothing.

        Returns
        -------
        float or None
            The recovered sampling period, or ``None`` when no state was used.

        """
        if not self.path.exists():
            self._last_totals = self._totals(reactors)
            _logger.info("No server checkpoint at %s; starting safe", self.path)
            return None
        try:
            with self.path.open(encoding="utf-8") as stream:
                raw = json.load(stream)
            plan = validate_snapshot(raw, reactors)
            apply_recovery(plan)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            StateValidationError,
        ) as exc:
            _logger.error(
                "Rejected server checkpoint %s: %s; starting manual/zero",
                self.path,
                exc,
            )
            self._quarantine()
            self._last_totals = self._totals(reactors)
            return None
        self._last_totals = self._totals(reactors)
        _logger.info("Recovered server checkpoint from %s", self.path)
        return plan.sampling_period

    def _quarantine(self) -> Path | None:
        """Move a rejected snapshot aside for operator inspection."""
        if not self.path.is_file():
            return None
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        rejected = self.path.with_name(f"{self.path.name}.rejected-{timestamp}")
        try:
            self.path.replace(rejected)
        except OSError:
            _logger.exception("Could not quarantine rejected checkpoint %s", self.path)
            return None
        _logger.warning("Quarantined rejected checkpoint as %s", rejected)
        return rejected

    def checkpoint(
        self,
        reactors: list[Reactor],
        sampling_period: float,
        *,
        only_if_totals_changed: bool = False,
    ) -> bool:
        """Atomically write a complete checkpoint.

        Write failures are logged and remain nonfatal. The totals signature is
        advanced only after a durable replacement succeeds, so a later periodic
        pass retries a failed write.
        """
        totals = self._totals(reactors)
        if only_if_totals_changed and totals == self._last_totals:
            return False
        try:
            self._write_snapshot(snapshot(reactors, sampling_period))
        except (OSError, TypeError, ValueError):
            _logger.exception("Could not checkpoint server state to %s", self.path)
            return False
        self._last_totals = totals
        return True

    def _write_snapshot(self, payload: dict[str, object]) -> None:
        """Replace the state file and fsync both file and containing directory."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(
                payload,
                stream,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)
        directory_fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    async def checkpoint_loop(
        self,
        reactors: list[Reactor],
        sampling_period: Callable[[], float],
    ) -> None:
        """Checkpoint changed totals no more than once per minute."""
        import asyncio

        while True:
            await asyncio.sleep(CHECKPOINT_INTERVAL)
            self.checkpoint(
                reactors,
                sampling_period(),
                only_if_totals_changed=True,
            )
