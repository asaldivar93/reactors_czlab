"""Pure state and validation for the pH PID autotuning screen.

The OPC methods return versioned JSON documents.  This module is the one
place that interprets those documents and turns them into a stable view model;
the NiceGUI page only copies the result onto existing elements.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

AUTOTUNE_VERSION = 1
DEFAULT_SETPOINT = 7.0
DEFAULT_DOSE_ML = 0.20
DEFAULT_HYSTERESIS_PH = 0.02
DEFAULT_MAX_MINUTES = 30.0
DEFAULT_PHOSPHATE_MM = 14.0
DEFAULT_TITRANT_M = 0.5
GAIN_RULES = ("TL-PI", "ZN-PID", "TL-PID", "SIMC")
ACTIVE_PHASES = frozenset(
    {"baseline", "adapting", "settling", "collecting"},
)


class ViewMode(StrEnum):
    """The mutually exclusive bodies of the screen."""

    setup = "setup"
    running = "running"
    identified = "identified"
    failed = "failed"


@dataclass(frozen=True)
class FormState:
    """Editable values, expressed in the units shown to an operator."""

    sensor_id: str = ""
    base_id: str = ""
    acid_id: str = ""
    setpoint: float = DEFAULT_SETPOINT
    base_dose_ml: float = DEFAULT_DOSE_ML
    acid_dose_ml: float = DEFAULT_DOSE_ML
    hysteresis_ph: float = DEFAULT_HYSTERESIS_PH
    max_minutes: float = DEFAULT_MAX_MINUTES
    phosphate_mm: float = DEFAULT_PHOSPHATE_MM
    base_molar: float = DEFAULT_TITRANT_M
    acid_molar: float = DEFAULT_TITRANT_M
    dose_budget_override_ml: float | None = None
    acknowledge_other_loops: bool = False
    acknowledge_budget_override: bool = False

    def opc_args(self) -> tuple[object, ...]:
        """Return arguments after the receiving base-pump node."""
        return (
            self.sensor_id,
            self.acid_id,
            self.setpoint,
            self.base_dose_ml,
            self.acid_dose_ml,
            self.hysteresis_ph,
            self.max_minutes,
            self.phosphate_mm / 1000.0,
            self.base_molar,
            self.acid_molar,
            self.dose_budget_override_ml or 0.0,
            self.acknowledge_other_loops,
            self.acknowledge_budget_override,
        )


@dataclass(frozen=True)
class GainView:
    """One server-calculated controller candidate."""

    rule: str
    kp: float
    ki: float
    kd: float

    @property
    def has_derivative(self) -> bool:
        """Whether applying this rule enables derivative action."""
        return not math.isclose(self.kd, 0.0, abs_tol=1e-15)


@dataclass(frozen=True)
class TracePoint:
    """One bounded pH sample from the server."""

    seconds: float
    ph: float


@dataclass(frozen=True)
class PreflightView:
    """Accepted or refused preflight, including server-owned wording."""

    ok: bool
    message: str
    safe_low: float | None = None
    safe_high: float | None = None
    default_budget_ml: float | None = None
    effective_budget_ml: float | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunView:
    """Everything that may change while the component tree stays fixed."""

    mode: ViewMode
    phase: str
    ok: bool
    message: str
    form: FormState = field(default_factory=FormState)
    current_ph: float | None = None
    relay_direction: str = "none"
    elapsed_seconds: float = 0.0
    safe_low: float | None = None
    safe_high: float | None = None
    dose_actual_ml: float = 0.0
    dose_budget_ml: float | None = None
    noise_sigma: float | None = None
    settling_cycles: int = 0
    clean_cycles: int = 0
    adjusted_base_ml: float | None = None
    adjusted_acid_ml: float | None = None
    warnings: tuple[str, ...] = ()
    trace: tuple[TracePoint, ...] = ()
    ku: float | None = None
    pu_seconds: float | None = None
    gains: tuple[GainView, ...] = ()

    @property
    def active(self) -> bool:
        """Whether the server owns pumps for this run."""
        return self.phase in ACTIVE_PHASES


def decode_payload(raw: object) -> Mapping[str, Any]:
    """Decode and validate the common versioned OPC response envelope.

    Raises
    ------
    ValueError
        If the response is malformed or uses an unsupported version.

    """
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as err:
        error_message = f"invalid autotune JSON: {err.msg}"
        raise ValueError(error_message) from err

    match payload:
        case {
            "version": int(version),
            "ok": bool(),
            "message": str(),
            "phase": str(),
        } if not isinstance(version, bool) and version == AUTOTUNE_VERSION:
            return payload
        case {"version": int(version)}:
            error_message = f"unsupported autotune response version {version}"
            raise ValueError(error_message)
        case _:
            error_message = "autotune response has an unsupported shape"
            raise ValueError(error_message)


def preflight_from_payload(raw: object) -> PreflightView:
    """Map one preflight response without changing its operator message."""
    payload = decode_payload(raw)
    safety = _mapping(payload.get("safety"))
    if payload["ok"]:
        safe_low = _float(safety.get("safe_low"), "safe low")
        safe_high = _float(safety.get("safe_high"), "safe high")
        default_budget = _float(
            safety.get("default_dose_budget_ml"),
            "default dose budget",
        )
        effective_budget = _float(
            safety.get("dose_budget_ml"),
            "effective dose budget",
        )
        if safe_low >= safe_high:
            error_message = "autotune preflight safety limits are not ordered"
            raise ValueError(error_message)
        if default_budget <= 0 or effective_budget <= 0:
            error_message = "autotune preflight dose budgets must be positive"
            raise ValueError(error_message)
    else:
        # Refusals are deliberately allowed to carry only the common envelope;
        # their server-owned message is the useful result.
        safe_low = _optional_float(safety.get("safe_low"))
        safe_high = _optional_float(safety.get("safe_high"))
        default_budget = _optional_float(
            safety.get("default_dose_budget_ml"),
        )
        effective_budget = _optional_float(safety.get("dose_budget_ml"))
    return PreflightView(
        ok=payload["ok"],
        message=payload["message"],
        safe_low=safe_low,
        safe_high=safe_high,
        default_budget_ml=default_budget,
        effective_budget_ml=effective_budget,
        warnings=_strings(payload.get("warnings")),
    )


def run_from_payload(raw: object) -> RunView:
    """Map sparse idle and complete run payloads onto explicit UI states."""
    payload = decode_payload(raw)
    phase = payload["phase"]
    match phase:
        case "idle":
            mode = ViewMode.setup
        case "baseline" | "adapting" | "settling" | "collecting":
            mode = ViewMode.running
        case "identified":
            mode = ViewMode.identified
        case "aborted" | "failed":
            mode = ViewMode.failed
        case _:
            error_message = f"unsupported autotune phase {phase!r}"
            raise ValueError(error_message)

    form = _form_from_status(payload)
    safety = _mapping(payload.get("safety"))
    dose = _mapping(payload.get("dose"))
    doses = _dose_values(payload, "adjusted")
    result = _mapping(payload.get("result"))
    identification = _mapping(result.get("identification"))
    candidates = _mapping(payload.get("candidate_gains"))

    gains: list[GainView] = []
    for rule in GAIN_RULES:
        values = _mapping(candidates.get(rule))
        if not values:
            continue
        gains.append(
            GainView(
                rule,
                _float(values.get("kp"), f"{rule} kp"),
                _float(values.get("ki"), f"{rule} ki"),
                _float(values.get("kd"), f"{rule} kd"),
            ),
        )

    raw_trace = tuple(
        (
            _float(item.get("timestamp"), "trace timestamp"),
            _float(item.get("ph"), "trace pH"),
        )
        for value in _sequence(payload.get("trace"))
        if (item := _mapping(value))
    )
    first_timestamp = raw_trace[0][0] if raw_trace else 0.0
    trace = tuple(
        TracePoint(timestamp - first_timestamp, ph)
        for timestamp, ph in raw_trace
    )

    return RunView(
        mode=mode,
        phase=phase,
        ok=payload["ok"],
        message=payload["message"],
        form=form,
        current_ph=_optional_float(payload.get("current_ph")),
        relay_direction=str(payload.get("relay_direction", "none")),
        elapsed_seconds=_float(payload.get("elapsed_seconds", 0.0), "elapsed"),
        safe_low=_optional_float(safety.get("safe_low")),
        safe_high=_optional_float(safety.get("safe_high")),
        dose_actual_ml=_float(dose.get("actual_ml", 0.0), "actual dose"),
        dose_budget_ml=_optional_float(dose.get("budget_ml")),
        noise_sigma=_optional_float(payload.get("noise_sigma")),
        settling_cycles=_int(payload.get("settling_cycles", 0), "settling cycles"),
        clean_cycles=_int(payload.get("clean_cycles", 0), "clean cycles"),
        adjusted_base_ml=_optional_float(doses.get("base")),
        adjusted_acid_ml=_optional_float(doses.get("acid")),
        warnings=_strings(payload.get("warnings")),
        trace=trace,
        ku=_optional_float(identification.get("Ku")),
        pu_seconds=_optional_float(identification.get("Pu")),
        gains=tuple(gains),
    )


def validate_form(form: FormState) -> tuple[str, ...]:
    """Return all local setup problems before an OPC round trip."""
    errors: list[str] = []
    if not form.sensor_id:
        errors.append("Select a pH sensor")
    if not form.base_id:
        errors.append("Select a base pump")
    if not form.acid_id:
        errors.append("Select an acid pump")
    if form.base_id and form.base_id == form.acid_id:
        errors.append("Base and acid pumps must be different")

    positive = {
        "Setpoint": form.setpoint,
        "Base dose": form.base_dose_ml,
        "Acid dose": form.acid_dose_ml,
        "Hysteresis": form.hysteresis_ph,
        "Maximum time": form.max_minutes,
        "Phosphate concentration": form.phosphate_mm,
        "Base concentration": form.base_molar,
        "Acid concentration": form.acid_molar,
    }
    for label, value in positive.items():
        if not math.isfinite(value) or value <= 0:
            errors.append(f"{label} must be finite and positive")
    override = form.dose_budget_override_ml
    if override is not None:
        if not math.isfinite(override) or override <= 0:
            errors.append("Dose budget override must be finite and positive")
        elif not form.acknowledge_budget_override:
            errors.append("Acknowledge the dose budget override")
    if not form.acknowledge_other_loops:
        errors.append("Acknowledge effects on other control loops")
    return tuple(errors)


def calibration_timestamp(raw: object) -> str:
    """Extract the installed calibration timestamp for a pump selector."""
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return "could not be read"
    calibration = _mapping(_mapping(payload).get("calibration"))
    fitted_at = calibration.get("fitted_at")
    if calibration.get("is_fitted") and isinstance(fitted_at, str) and fitted_at:
        return fitted_at
    return "no fitted calibration"


def replace_selection(
    form: FormState,
    *,
    sensor_id: str,
    base_id: str,
    acid_id: str,
) -> FormState:
    """Overlay live address-book selections onto a sparse idle form."""
    return replace(
        form,
        sensor_id=form.sensor_id or sensor_id,
        base_id=form.base_id or base_id,
        acid_id=form.acid_id or acid_id,
    )


def _form_from_status(payload: Mapping[str, Any]) -> FormState:
    selection = _mapping(payload.get("selection"))
    chemistry = _mapping(payload.get("chemistry"))
    doses = _dose_values(payload, "adjusted")
    return FormState(
        sensor_id=_string(selection.get("sensor_id")),
        base_id=_string(selection.get("base_id")),
        acid_id=_string(selection.get("acid_id")),
        setpoint=_optional_float(payload.get("setpoint")) or DEFAULT_SETPOINT,
        base_dose_ml=(
            _optional_float(doses.get("base")) or DEFAULT_DOSE_ML
        ),
        acid_dose_ml=(
            _optional_float(doses.get("acid")) or DEFAULT_DOSE_ML
        ),
        hysteresis_ph=(
            _optional_float(payload.get("hysteresis_ph"))
            or DEFAULT_HYSTERESIS_PH
        ),
        max_minutes=(
            _optional_float(payload.get("max_minutes"))
            or DEFAULT_MAX_MINUTES
        ),
        phosphate_mm=(
            (_optional_float(chemistry.get("phosphate_molar")) or 0.014)
            * 1000.0
        ),
        base_molar=(
            _optional_float(chemistry.get("base_molar"))
            or DEFAULT_TITRANT_M
        ),
        acid_molar=(
            _optional_float(chemistry.get("acid_molar"))
            or DEFAULT_TITRANT_M
        ),
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _dose_values(payload: Mapping[str, Any], prefix: str) -> Mapping[str, Any]:
    """Read canonical dose values, falling back at the JSON boundary."""
    canonical = _mapping(payload.get(f"{prefix}_doses_ml"))
    if canonical:
        return canonical
    # Deprecated mixed-version alias. Business/UI state remains canonical.
    return _mapping(payload.get(f"{prefix}_boluses_ml"))


def _sequence(value: object) -> list[object] | tuple[object, ...]:
    return value if isinstance(value, (list, tuple)) else ()


def _strings(value: object) -> tuple[str, ...]:
    return tuple(item for item in _sequence(value) if isinstance(item, str))


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _float(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        error_message = f"autotune {label} is not numeric"
        raise TypeError(error_message)
    result = float(value)
    if not math.isfinite(result):
        error_message = f"autotune {label} is not finite"
        raise ValueError(error_message)
    return result


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return _float(value, "value")


def _int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        error_message = f"autotune {label} is not an integer"
        raise TypeError(error_message)
    return value
