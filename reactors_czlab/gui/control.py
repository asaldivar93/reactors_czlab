"""Pure helpers for the atomic control-configuration method."""

from __future__ import annotations

import math
from dataclasses import dataclass

from reactors_czlab.core.data import ControlMethod

#: Which fields each strategy consumes. The server receives one complete
#: argument tuple but deliberately populates only the selected method's
#: ``ControlConfig`` fields.
METHOD_FIELDS: dict[str, tuple[str, ...]] = {
    ControlMethod.manual: ("value",),
    ControlMethod.timer: ("time_on", "time_off", "value"),
    ControlMethod.on_boundaries: ("lb", "ub", "value", "backwards"),
    ControlMethod.pid: (
        "setpoint",
        "kp",
        "ki",
        "kd",
        "backwards",
        "auto_integral_band",
        "min_integral",
        "max_integral",
    ),
}

#: Config arguments following Method and Output_unit in the server method.
#: The form starts with all of them from ``get_control_config`` and overlays
#: the fields currently visible to the operator.
CONFIG_FIELDS = (
    "value",
    "time_on",
    "time_off",
    "lb",
    "ub",
    "setpoint",
    "kp",
    "ki",
    "kd",
    "min_integral",
    "max_integral",
    "auto_integral_band",
    "backwards",
)


@dataclass(frozen=True)
class DosePreview:
    """Pure volume-policy preview rendered by the control dialog."""

    pid: bool
    duty: float
    flow: float
    max_duration: float
    max_volume: float
    requested: float | None
    effective: float | None
    duration: float | None
    capped: bool


def dose_preview(
    method: str,
    output_unit: str,
    requested: object,
    limits: object,
) -> DosePreview | None:
    """Build a preview from server-calculated dose metadata.

    PID demand is calculated live from sensor feedback, so its preview is
    the maximum one decision can request. Other methods preview the edited
    volume and the authoritative finite-request cap.
    """
    if output_unit != "volume" or not isinstance(limits, dict):
        return None
    pid = method == ControlMethod.pid
    policy = limits.get("pid" if pid else "non_pid")
    if not isinstance(policy, dict):
        return None
    try:
        duty = float(policy["duty"])
        flow = float(policy["flow"])
        max_duration = float(policy["max_duration"])
        max_volume = float(policy["max_volume"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not all(
        math.isfinite(value)
        for value in (duty, flow, max_duration, max_volume)
    ):
        return None
    if pid:
        return DosePreview(
            True,
            duty,
            flow,
            max_duration,
            max_volume,
            None,
            None,
            None,
            False,
        )
    if isinstance(requested, bool) or not isinstance(requested, int | float):
        return DosePreview(
            False,
            duty,
            flow,
            max_duration,
            max_volume,
            None,
            None,
            None,
            False,
        )
    requested_number = float(requested)
    if not math.isfinite(requested_number):
        return DosePreview(
            False,
            duty,
            flow,
            max_duration,
            max_volume,
            requested_number,
            None,
            None,
            False,
        )
    effective = max(0.0, min(requested_number, max_volume))
    duration = 0.0 if effective == 0.0 else 60.0 * effective / flow
    return DosePreview(
        False,
        duty,
        flow,
        max_duration,
        max_volume,
        requested_number,
        effective,
        duration,
        requested_number > max_volume,
    )


def dose_preview_text(preview: DosePreview | None) -> tuple[str, bool]:
    """Format a concise preview and whether it represents a cap."""
    if preview is None:
        return ("", False)
    if preview.pid:
        return (
            (
                f"PID maximum: {preview.max_volume:.4g} mL per decision at "
                f"duty {preview.duty:.0f} "
                f"(up to {preview.max_duration:.3g} s)"
            ),
            False,
        )
    if preview.effective is None or preview.duration is None:
        return (
            "Enter a finite volume to preview its effective dose and duration",
            False,
        )
    text = (
        f"Requested {preview.requested:.4g} mL; effective "
        f"{preview.effective:.4g} mL at duty {preview.duty:.0f}; "
        f"estimated {preview.duration:.3g} s"
    )
    if preview.capped:
        text += " — request will be capped by the server"
    return (text, preview.capped)


def fields_for(method: str) -> tuple[str, ...]:
    """Return the config fields consumed by ``method``.

    Raises
    ------
    KeyError
        If the server does not implement ``method``.

    """
    return METHOD_FIELDS[method]


def build_config_args(
    method: int,
    output_unit: int,
    values: dict[str, object],
) -> tuple[object, ...]:
    """Build one complete ``apply_control_config`` argument tuple.

    Parameters
    ----------
    method, output_unit:
        Indices derived from the server-provided option lists.
    values:
        Every configuration field from the read-back payload, overlaid with
        values edited in the form.

    Returns
    -------
    tuple
        Arguments in the order declared by the OPC method.

    Raises
    ------
    ValueError
        If any declared method argument is missing.

    """
    for field in CONFIG_FIELDS:
        if field not in values:
            error_message = f"control config needs a value for {field}"
            raise ValueError(error_message)
    return (
        method,
        output_unit,
        *(values[field] for field in CONFIG_FIELDS),
    )
