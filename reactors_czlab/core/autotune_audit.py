"""Persistent, operator-readable audit history for pH PID autotuning.

This module intentionally prepares gain changes but never writes a controller.
Stage 4 owns the stable-lock, validate-both, apply-or-roll-back operation and
calls the ``record_*_success`` methods only after that operation succeeds.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from reactors_czlab.core.calibration import calibration_dir
from reactors_czlab.core.ph_model import Chemistry, buffering_intensity

if TYPE_CHECKING:
    from reactors_czlab.core.autotune import AutotuneContext, AutotuneRun

_logger = logging.getLogger("server.autotune_audit")

AUDIT_VERSION = 1
_SAFE_REACTOR = re.compile(r"^[A-Za-z0-9_-]+$")
_NUMERIC_KEYS = frozenset(
    {
        "actual_acid_ml", "actual_base_ml", "actual_dose_ml", "acid_bolus_ml", "acid_molar", "amplitude",
        "base_bolus_ml", "base_molar", "base_half_seconds", "base_requests", "acid_half_seconds", "acid_requests",
        "clean_cycles", "dose_budget_ml", "default_dose_budget_ml", "ended_at",
        "half_cycle_ratio", "hysteresis", "kd", "ki", "kp", "Ku", "Pu",
        "max_minutes", "noise_sigma", "period", "ph", "phosphate_molar",
        "peak_ph", "requested_acid_ml", "requested_base_ml", "requested_volume_ml",
        "safe_high", "safe_low", "sample_index", "setpoint", "settling_cycles",
        "started_at", "timestamp", "trough_ph", "u_acid", "u_base",
    },
)


@dataclass(frozen=True)
class AuditOutcome:
    """An operator-facing persistence/preparation result."""

    ok: bool
    message: str
    data: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class GainCandidate:
    """A fully validated dual-pump gain candidate for Stage 4 to apply."""

    action: str
    reactor_id: str
    sensor_id: str
    base_id: str
    acid_id: str
    channel_index: int
    rule: str
    reference_ph: float
    tuned_ph: float
    chemistry: Mapping[str, float]
    gains: tuple[float, float, float]

    def as_dict(self) -> dict[str, Any]:
        """Return the stable JSON-compatible representation."""
        return {
            "action": self.action,
            "reactor_id": self.reactor_id,
            "sensor_id": self.sensor_id,
            "base_id": self.base_id,
            "acid_id": self.acid_id,
            "channel_index": self.channel_index,
            "rule": self.rule,
            "reference_ph": self.reference_ph,
            "tuned_ph": self.tuned_ph,
            "chemistry": dict(self.chemistry),
            "gains": {"kp": self.gains[0], "ki": self.gains[1], "kd": self.gains[2]},
        }


def _utc_iso(now: datetime) -> str:
    """Format an injected UTC wall-clock value consistently."""
    return now.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _number(value: object, name: str) -> float:
    """Return a finite JSON number, refusing bool's numeric subclassing."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        error_message = f"{name} must be a finite number"
        raise TypeError(error_message)
    number = float(value)
    if not math.isfinite(number):
        error_message = f"{name} must be a finite number"
        raise ValueError(error_message)
    return number


def audit_path(reactor_id: str, *, directory: Callable[[], Path] = calibration_dir) -> Path:
    """Return the audit path, refusing identifiers that could escape it.

    Parameters
    ----------
    reactor_id:
        Reactor identifier used in the filename.

    Raises
    ------
    ValueError
        If the identifier could make the audit path leave its directory.

    """
    if not isinstance(reactor_id, str) or not _SAFE_REACTOR.fullmatch(reactor_id):
        error_message = f"unsafe reactor id for autotune audit: {reactor_id!r}"
        raise ValueError(error_message)
    root = Path(directory())
    filename = f"{reactor_id}_ph_autotune.json"
    path = root / filename
    if path.name != filename or path.parent != root:
        error_message = f"unsafe autotune audit path for {reactor_id!r}"
        raise ValueError(error_message)
    return path


class AutotuneAudit:
    """Versioned append-only pH-autotune audit document for one reactor."""

    def __init__(
        self,
        reactor_id: str,
        *,
        directory: Callable[[], Path] = calibration_dir,
        utcnow: Callable[[], datetime] = lambda: datetime.now(UTC),
        replace_file: Callable[..., None] = os.replace,
        run_id: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        """Bind injectable filesystem and time dependencies for safe tests."""
        self.reactor_id = reactor_id
        self._directory = directory
        self._utcnow = utcnow
        self._replace_file = replace_file
        self._run_id = run_id

    @property
    def path(self) -> Path:
        """Path of this reactor's audit document."""
        return audit_path(self.reactor_id, directory=self._directory)

    def read(self) -> AuditOutcome:
        """Read and validate the existing document without raising data errors."""
        try:
            path = self.path
            if not path.exists():
                return AuditOutcome(False, f"no autotune audit exists for {self.reactor_id}")
            raw = json.loads(path.read_text(encoding="utf-8"))
            document = self._validate_document(raw)
        except Exception as exc:  # noqa: BLE001 - arbitrary operator file/filesystem boundary
            _logger.warning("Could not read autotune audit for %s: %s", self.reactor_id, exc)
            return AuditOutcome(False, f"autotune audit is unreadable: {exc}")
        return AuditOutcome(True, "autotune audit loaded", document)

    def record_started(self, run: AutotuneRun) -> AuditOutcome:
        """Append the record required once preflight passed and ownership began."""
        try:
            document = self._read_or_new()
            generated_id = self._run_id()
            if not isinstance(generated_id, str) or not generated_id:
                error_message = "generated autotune audit run id is invalid"
                raise ValueError(error_message)
            if any(item["run_id"] == generated_id for item in document["runs"]):
                error_message = f"autotune audit run id {generated_id!r} already exists"
                raise ValueError(error_message)
            record = self._run_record(run, generated_id)
            document["runs"].append(record)
            self._write(document)
        except Exception as exc:  # noqa: BLE001 - persistence must not interrupt a started run
            _logger.warning("Could not save started autotune audit for %s: %s", self.reactor_id, exc)
            return AuditOutcome(False, f"autotune audit was not saved: {exc}")
        return AuditOutcome(True, "autotune run audit started", {"run_id": record["run_id"]})

    def record_terminal(self, run: AutotuneRun) -> AuditOutcome:
        """Update the started record with terminal results or a failure reason."""
        try:
            if run.audit_id is None:
                error_message = "autotune run has no persisted audit id"
                raise ValueError(error_message)
            document = self._read_or_new()
            record = next((item for item in document["runs"] if item["run_id"] == run.audit_id), None)
            if record is None:
                error_message = f"autotune audit run {run.audit_id} is missing"
                raise ValueError(error_message)
            record.update(self._terminal_record(run))
            self._write(document)
        except Exception as exc:  # noqa: BLE001 - terminal safety cleanup must finish
            _logger.warning("Could not save terminal autotune audit for %s: %s", self.reactor_id, exc)
            return AuditOutcome(False, f"autotune terminal audit was not saved: {exc}")
        return AuditOutcome(True, "autotune terminal audit saved")

    def prepare_apply(self, run: AutotuneRun, rule: str) -> AuditOutcome:
        """Build an identified-run candidate; it deliberately does not mutate pumps."""
        try:
            if run.result is None:
                error_message = "autotune has no identified gains to apply"
                raise ValueError(error_message)
            from reactors_czlab.core.autotune import (
                to_code_gains,
                tuning_rules,
            )

            continuous = tuning_rules(run.result.identification.Ku, run.result.identification.Pu)
            if rule == "SIMC":
                from reactors_czlab.core.autotune import simc_pid

                selected = simc_pid(run.result.identification.Ku, run.result.identification.Pu)
            else:
                selected = continuous[rule]
            gains = to_code_gains(*selected)
            channel_index = self._validate_selection(run.context, run.sensor_id, run.base_id, run.acid_id, run.config.setpoint)
            candidate = GainCandidate(
                "apply", self.reactor_id, run.sensor_id, run.base_id, run.acid_id, channel_index,
                rule, run.config.setpoint, run.config.setpoint, self._chemistry_from_run(run), gains,
            )
        except Exception as exc:  # noqa: BLE001 - return all persisted-data refusals
            return AuditOutcome(False, f"cannot prepare autotune apply: {exc}")
        return AuditOutcome(True, "autotune gains are ready for atomic apply", candidate.as_dict())

    def prepare_scale(self, context: AutotuneContext, target_ph: float) -> AuditOutcome:
        """Prepare a reference-aware scale candidate without compounding gains."""
        try:
            target = _number(target_ph, "target pH")
            latest = self._latest()
            candidate = self._candidate_from_latest(latest, "scale")
            channel = self._validate_selection(context, candidate.sensor_id, candidate.base_id, candidate.acid_id, target)
            if channel != candidate.channel_index:
                error_message = "selected pH channel no longer matches the applied autotune"
                raise ValueError(error_message)
            current = self._current_gains(context, candidate)
            expected = candidate.gains
            if current != expected:
                error_message = "controller gains no longer match the last applied autotune"
                raise ValueError(error_message)
            chemistry = candidate.chemistry
            phosphate = chemistry["phosphate_molar"]
            ratio = buffering_intensity(target, phosphate, Chemistry()) / buffering_intensity(
                candidate.reference_ph,
                phosphate,
                Chemistry(),
            )
            scaled = tuple(value * ratio for value in expected)
            if not all(math.isfinite(value) for value in scaled):
                error_message = "scaled gains are non-finite"
                raise ValueError(error_message)
            prepared = GainCandidate(
                "scale", self.reactor_id, candidate.sensor_id, candidate.base_id, candidate.acid_id,
                # A successful write makes this target the next reference;
                # B->C thus telescopes to the direct A->C scale.
                channel, candidate.rule, target, candidate.tuned_ph, chemistry, scaled,
            )
        except Exception as exc:  # noqa: BLE001 - return all persisted-data refusals
            return AuditOutcome(False, f"cannot prepare autotune scaling: {exc}")
        return AuditOutcome(True, "scaled gains are ready for atomic apply", prepared.as_dict())

    def prepare_reapply(self, context: AutotuneContext) -> AuditOutcome:
        """Revalidate the exact saved selection before returning a reapply candidate."""
        try:
            latest = self._latest()
            candidate = self._candidate_from_latest(latest, "reapply")
            channel = self._validate_selection(context, candidate.sensor_id, candidate.base_id, candidate.acid_id, candidate.reference_ph)
            if channel != candidate.channel_index:
                error_message = "selected pH channel no longer matches the applied autotune"
                raise ValueError(error_message)
            candidate = GainCandidate(
                candidate.action, candidate.reactor_id, candidate.sensor_id, candidate.base_id,
                candidate.acid_id, channel, candidate.rule, candidate.reference_ph,
                candidate.tuned_ph, candidate.chemistry, candidate.gains,
            )
        except Exception as exc:  # noqa: BLE001 - return all persisted-data refusals
            return AuditOutcome(False, f"cannot prepare autotune reapply: {exc}")
        return AuditOutcome(True, "stored gains are ready for atomic reapply", candidate.as_dict())

    def record_apply_success(self, candidate: Mapping[str, Any]) -> AuditOutcome:
        """Record a completed atomic gain write and replace ``latest_applied``."""
        return self._record_event("apply", True, candidate, None)

    def record_scale_success(self, candidate: Mapping[str, Any]) -> AuditOutcome:
        """Record a completed atomic scale write and replace ``latest_applied``."""
        return self._record_event("scale", True, candidate, None)

    def record_reapply_success(self, candidate: Mapping[str, Any]) -> AuditOutcome:
        """Record a completed atomic reapply without mislabeling it as apply."""
        return self._record_event("reapply", True, candidate, None)

    def record_apply_failure(self, action: str, candidate: Mapping[str, Any] | None, reason: str) -> AuditOutcome:
        """Make an unsuccessful Stage-4 gain write visible without claiming success."""
        if action not in {"apply", "scale", "reapply"}:
            return AuditOutcome(False, f"unknown autotune audit action {action!r}")
        return self._record_event(action, False, candidate, reason)

    def _record_event(self, action: str, succeeded: bool, candidate: Mapping[str, Any] | None, reason: str | None) -> AuditOutcome:
        try:
            document = self._read_or_new()
            event: dict[str, Any] = {"at": _utc_iso(self._utcnow()), "action": action, "ok": succeeded}
            if candidate is not None:
                validated = self._candidate_from_latest_or_mapping(candidate, action)
                event["candidate"] = validated.as_dict()
                if succeeded:
                    document["latest_applied"] = validated.as_dict()
            if reason is not None:
                if not isinstance(reason, str) or not reason:
                    error_message = "failure reason must be a non-empty string"
                    raise ValueError(error_message)
                event["reason"] = reason
            document["events"].append(event)
            self._write(document)
        except Exception as exc:  # noqa: BLE001 - audit write failures must be visible, not fatal
            _logger.warning("Could not save autotune %s event for %s: %s", action, self.reactor_id, exc)
            return AuditOutcome(False, f"autotune {action} audit was not saved: {exc}")
        return AuditOutcome(True, f"autotune {action} event saved")

    def _read_or_new(self) -> dict[str, Any]:
        path = self.path
        if not path.exists():
            return {"version": AUDIT_VERSION, "reactor_id": self.reactor_id, "runs": [], "events": [], "latest_applied": None}
        return self._validate_document(json.loads(path.read_text(encoding="utf-8")))

    def _write(self, document: Mapping[str, Any]) -> None:
        path = self.path
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(document, indent=2, allow_nan=False), encoding="utf-8")
        self._replace_file(tmp, path)

    def _run_record(self, run: AutotuneRun, run_id: str) -> dict[str, Any]:
        if run.context.reactor_id != self.reactor_id:
            error_message = "autotune run belongs to a different reactor"
            raise ValueError(error_message)
        channel = self._validate_selection(run.context, run.sensor_id, run.base_id, run.acid_id, run.config.setpoint)
        return {
            "run_id": run_id,
            "started_utc": _utc_iso(self._utcnow()),
            "started_at": run.started_at,
            "selection": {"sensor_id": run.sensor_id, "base_id": run.base_id, "acid_id": run.acid_id, "channel_index": channel},
            "chemistry": self._chemistry_from_run(run),
            "safety": {"safe_low": run.safe_low, "safe_high": run.safe_high, "dose_budget_ml": run.dose_budget_ml},
            "initial_boluses_ml": {"base": run.config.u_base, "acid": run.config.u_acid},
            "phase": run.phase.value,
            "message": run.message,
        }

    def _terminal_record(self, run: AutotuneRun) -> dict[str, Any]:
        result = run.result
        record: dict[str, Any] = {
            "ended_utc": _utc_iso(self._utcnow()), "ended_at": run.ended_at,
            "phase": run.phase.value, "message": run.message, "terminal_reason": run.message,
            "adjusted_boluses_ml": {"base": run.base_bolus_ml, "acid": run.acid_bolus_ml},
            "actual_dose_ml": run.actual_dose_ml,
            "trace": [self._sample(item) for item in run.samples[-240:]],
            "switch_times": list(run.switch_times[-240:]),
            "cycles": [self._cycle(item) for item in run.cycle_history[-12:]],
        }
        if run.noise_sigma is not None:
            record["baseline_sigma"] = run.noise_sigma
        if result is not None:
            identification = result.identification
            record["identification"] = {
                "Ku": identification.Ku,
                "Pu": identification.Pu,
                "amplitude": identification.amplitude,
                "mean_ph": identification.mean_ph,
                "cycles_used": identification.cycles_used,
            }
            from reactors_czlab.core.autotune import (
                simc_pid,
                to_code_gains,
                tuning_rules,
            )

            candidates = {name: to_code_gains(*values) for name, values in tuning_rules(identification.Ku, identification.Pu).items()}
            candidates["SIMC"] = to_code_gains(*simc_pid(identification.Ku, identification.Pu))
            record["candidates"] = {name: {"kp": values[0], "ki": values[1], "kd": values[2]} for name, values in candidates.items()}
        return record

    @staticmethod
    def _sample(item: Any) -> dict[str, float]:
        return {"timestamp": item.timestamp, "ph": item.ph, "requested_volume_ml": item.requested_volume_ml, "actual_dose_ml": item.actual_dose_ml}

    @staticmethod
    def _cycle(item: Any) -> dict[str, float | int]:
        return {name: getattr(item, name) for name in item.__dataclass_fields__}

    @staticmethod
    def _chemistry_from_run(run: AutotuneRun) -> dict[str, float]:
        return {"phosphate_molar": run.config.phosphate_molar, "base_molar": run.config.base_molar, "acid_molar": run.config.acid_molar}

    @staticmethod
    def _validate_selection(context: AutotuneContext, sensor_id: str, base_id: str, acid_id: str, setpoint: float) -> int:
        from reactors_czlab.core.autotune import validate_autotune_selection

        return validate_autotune_selection(context, sensor_id, base_id, acid_id, setpoint)

    def _latest(self) -> Mapping[str, Any]:
        outcome = self.read()
        if not outcome.ok or outcome.data is None:
            error_message = outcome.message
            raise TypeError(error_message)
        latest = outcome.data["latest_applied"]
        if latest is None:
            error_message = "no successful autotune gain application is recorded"
            raise TypeError(error_message)
        return latest

    def _candidate_from_latest(self, latest: Mapping[str, Any], action: str) -> GainCandidate:
        return self._candidate_from_latest_or_mapping(latest, action)

    def _candidate_from_latest_or_mapping(self, raw: Mapping[str, Any], action: str) -> GainCandidate:
        if not isinstance(raw, Mapping):
            error_message = "autotune candidate is not a mapping"
            raise TypeError(error_message)
        try:
            reactor_id = raw["reactor_id"]
            sensor_id, base_id, acid_id = raw["sensor_id"], raw["base_id"], raw["acid_id"]
            channel = raw["channel_index"]
            rule = raw["rule"]
            reference = _number(raw["reference_ph"], "reference_ph")
            tuned = _number(raw["tuned_ph"], "tuned_ph")
            chemistry_raw = raw["chemistry"]
            gains_raw = raw["gains"]
        except KeyError as exc:
            error_message = f"autotune candidate is missing {exc.args[0]}"
            raise ValueError(error_message) from exc
        if reactor_id != self.reactor_id or not all(isinstance(item, str) and item for item in (sensor_id, base_id, acid_id, rule)):
            error_message = "autotune candidate identities are invalid"
            raise ValueError(error_message)
        if isinstance(channel, bool) or not isinstance(channel, int) or channel < 0:
            error_message = "autotune candidate channel index is invalid"
            raise ValueError(error_message)
        if not isinstance(chemistry_raw, Mapping) or not isinstance(gains_raw, Mapping):
            error_message = "autotune candidate chemistry or gains is invalid"
            raise TypeError(error_message)
        chemistry = {name: _number(chemistry_raw[name], f"chemistry.{name}") for name in ("phosphate_molar", "base_molar", "acid_molar")}
        if not all(value > 0 for value in chemistry.values()):
            error_message = "autotune candidate chemistry must be positive"
            raise ValueError(error_message)
        gains = tuple(_number(gains_raw[name], f"gains.{name}") for name in ("kp", "ki", "kd"))
        return GainCandidate(action, reactor_id, sensor_id, base_id, acid_id, channel, rule, reference, tuned, chemistry, gains)

    @staticmethod
    def _current_gains(context: AutotuneContext, candidate: GainCandidate) -> tuple[float, float, float]:
        values: list[tuple[float, float, float]] = []
        for identifier in (candidate.base_id, candidate.acid_id):
            controller = context.actuators[identifier].controller
            values.append(tuple(_number(getattr(controller, name), f"{identifier}.{name}") for name in ("kp", "ki", "kd")))
        if values[0] != values[1]:
            error_message = "base and acid controller gains are not shared"
            raise ValueError(error_message)
        return values[0]

    def _validate_document(self, raw: object) -> dict[str, Any]:
        match raw:
            case {"version": version, "reactor_id": reactor_id, "runs": runs, "events": events, "latest_applied": latest}:
                pass
            case _:
                error_message = "autotune audit document has an unsupported shape"
                raise TypeError(error_message)
        if isinstance(version, bool) or version != AUDIT_VERSION:
            error_message = f"unsupported autotune audit version {version!r}"
            raise ValueError(error_message)
        if reactor_id != self.reactor_id or not isinstance(runs, list) or not isinstance(events, list):
            error_message = "autotune audit document identities or histories are invalid"
            raise ValueError(error_message)
        document = {"version": version, "reactor_id": reactor_id, "runs": runs, "events": events, "latest_applied": latest}
        self._validate_nested(document)
        if latest is not None:
            self._candidate_from_latest_or_mapping(latest, "stored")
        run_ids: set[str] = set()
        for item in runs:
            if not isinstance(item, Mapping) or not isinstance(item.get("run_id"), str):
                error_message = "autotune audit run history is invalid"
                raise TypeError(error_message)
            if not item["run_id"] or item["run_id"] in run_ids:
                error_message = "autotune audit run ids must be unique and non-empty"
                raise ValueError(error_message)
            run_ids.add(item["run_id"])
            self._validate_run(item)
        for item in events:
            if not isinstance(item, Mapping) or not isinstance(item.get("action"), str) or not isinstance(item.get("ok"), bool):
                error_message = "autotune audit event history is invalid"
                raise TypeError(error_message)
            if "candidate" in item:
                self._candidate_from_latest_or_mapping(item["candidate"], "event")
        return document

    def _validate_run(self, record: Mapping[str, Any]) -> None:
        """Reject corrupt numeric detail in an otherwise well-shaped run record."""
        try:
            selection = record["selection"]
            chemistry = record["chemistry"]
            safety = record["safety"]
            initial = record["initial_boluses_ml"]
        except KeyError as exc:
            error_message = f"autotune audit run is missing {exc.args[0]}"
            raise ValueError(error_message) from exc
        if not all(
            isinstance(value, Mapping)
            for value in (selection, chemistry, safety, initial)
        ):
            error_message = "autotune audit run has malformed nested data"
            raise ValueError(error_message)
        channel = selection.get("channel_index")
        if isinstance(channel, bool) or not isinstance(channel, int) or channel < 0:
            error_message = "autotune audit run channel index is invalid"
            raise ValueError(error_message)
        for name in ("phosphate_molar", "base_molar", "acid_molar"):
            _number(chemistry.get(name), f"chemistry.{name}")
        for name in ("safe_low", "safe_high", "dose_budget_ml"):
            _number(safety.get(name), f"safety.{name}")
        self._boluses(initial, "initial_boluses_ml")
        if "adjusted_boluses_ml" in record:
            adjusted = record["adjusted_boluses_ml"]
            if not isinstance(adjusted, Mapping):
                error_message = "adjusted boluses are malformed"
                raise ValueError(error_message)
            self._boluses(adjusted, "adjusted_boluses_ml")
        for name in ("trace", "switch_times", "cycles"):
            if name not in record:
                continue
            if not isinstance(record[name], list):
                error_message = f"autotune audit {name} is malformed"
                raise TypeError(error_message)
        for value in record.get("switch_times", []):
            _number(value, "switch_times")

    @staticmethod
    def _boluses(raw: Mapping[str, Any], name: str) -> None:
        for side in ("base", "acid"):
            if _number(raw.get(side), f"{name}.{side}") <= 0:
                error_message = f"{name}.{side} must be positive"
                raise ValueError(error_message)

    def _validate_nested(self, value: object, name: str = "document") -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if not isinstance(key, str):
                    error_message = f"{name} has a non-string key"
                    raise TypeError(error_message)
                child_name = f"{name}.{key}"
                if key in _NUMERIC_KEYS:
                    _number(child, child_name)
                else:
                    self._validate_nested(child, child_name)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                self._validate_nested(child, f"{name}[{index}]")
        elif isinstance(value, float) and not math.isfinite(value):
            error_message = f"{name} is non-finite"
            raise ValueError(error_message)
