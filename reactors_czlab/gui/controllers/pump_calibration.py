"""What a pump calibration run allows at each point in time.

``CalibrationRun`` has no state enum, but it has states: idle, running,
and awaiting a measurement. Which controls an operator may use follows
from them, and getting it wrong means offering Fit to someone who has
not recorded their last point, or letting a second run start while a
pump is already turning.

Pure: it maps the JSON ``get_calibration`` returns onto that decision,
so it can be tested without a server or a pump.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from reactors_czlab.core.calibration import (
    MAX_RUN_SECONDS,
    MIN_POINTS,
    MIN_RUN_SECONDS,
)
from reactors_czlab.core.data import MAX_OUTPUT


class RunState(StrEnum):
    """The three states a calibration run can be in."""

    #: Nothing in flight. Points may be run, fitted, cleared.
    idle = "idle"
    #: The pump is turning for a calibration point.
    running = "running"
    #: A point ran and its measured volume has not been entered.
    awaiting = "awaiting"


@dataclass(frozen=True)
class RunView:
    """Everything the pump calibration screen needs to draw itself."""

    state: RunState
    points: list[tuple[float, float]]
    pending_duty: float | None
    pending_seconds: float | None
    zero_flow_duty: float | None
    calibration: dict | None

    @property
    def has_slot(self) -> bool:
        """Whether this actuator can be calibrated at all.

        The MFCs have ``calibration=None`` on their channel, so every
        CalibrationRun method answers "has no calibration slot". The
        screen hides itself rather than offering controls that cannot
        work.
        """
        return self.calibration is not None

    @property
    def can_run_point(self) -> bool:
        """Whether a new point may be started."""
        return self.has_slot and self.state is RunState.idle

    @property
    def can_record(self) -> bool:
        """Whether a measured volume may be entered."""
        return self.state is RunState.awaiting

    @property
    def can_discard(self) -> bool:
        """Whether a completed, unmeasured point can be discarded."""
        return self.has_slot and self.state is RunState.awaiting

    @property
    def can_fit(self) -> bool:
        """Whether there is enough to fit both candidate models.

        Four *distinct* duties, not just four points: the fitter needs
        residual degrees of freedom to qualify prediction uncertainty.
        """
        if not self.has_slot or self.state is not RunState.idle:
            return False
        duties = {duty for duty, _ in self.points}
        return len(duties) >= MIN_POINTS

    @property
    def can_edit(self) -> bool:
        """Whether clear/reload/set-duties may be used.

        Not while a pump is turning, and not while a measurement is
        owed: clear_points would silently throw the pending point away.
        """
        return self.has_slot and self.state is RunState.idle

    @property
    def fitted(self) -> bool:
        """Whether an installed, fitted line exists."""
        return bool(self.calibration and self.calibration.get("is_fitted"))


def view_from_payload(payload: dict) -> RunView:
    """Build the view from what ``get_calibration`` returned."""
    pending = payload.get("pending")
    if payload.get("running"):
        state = RunState.running
    elif pending:
        state = RunState.awaiting
    else:
        state = RunState.idle

    return RunView(
        state=state,
        points=[tuple(point) for point in payload.get("run_points", [])],
        pending_duty=pending[0] if pending else None,
        pending_seconds=pending[1] if pending else None,
        zero_flow_duty=payload.get("zero_flow_duty"),
        calibration=payload.get("calibration"),
    )


def duty_error(duty: float | None) -> str | None:
    """Why a duty is not runnable, if it is not.

    Checked here as well as server-side so the operator is told before
    the pump does nothing for the ten seconds they asked for.
    """
    if duty is None:
        return "Enter a duty"
    if not 0 <= duty <= MAX_OUTPUT:
        return f"Duty must be between 0 and {MAX_OUTPUT:.0f} counts"
    return None


def seconds_error(seconds: float | None) -> str | None:
    """Why a run duration is not acceptable, if it is not."""
    if seconds is None:
        return "Enter a duration"
    if not MIN_RUN_SECONDS <= seconds <= MAX_RUN_SECONDS:
        return (
            f"Run time must be between {MIN_RUN_SECONDS:.0f} and "
            f"{MAX_RUN_SECONDS:.0f} seconds"
        )
    return None


def volume_error(volume: float | None) -> str | None:
    """Why a measured volume is not acceptable, if it is not.

    Zero is allowed deliberately: it is how an operator records that a
    duty is below the pump's stall floor. The server stores that evidence
    separately from the curve fit.
    """
    if volume is None:
        return "Enter the measured volume"
    if not 0 <= volume < float("inf"):
        return "Volume must be finite and cannot be negative"
    return None


def zero_flow_duty_error(duty: float | None) -> str | None:
    """Why optional zero-flow stall evidence is invalid, if supplied."""
    if duty is None:
        return None
    return duty_error(duty)


def calibration_chart(calibration: dict) -> tuple[dict, bool]:
    """Build a Plotly figure and report whether uncertainty is available."""
    measured = calibration.get("points", [])
    series = calibration.get("fit_series", {})
    duties = list(series.get("duty", [])) if isinstance(series, dict) else []
    fitted = list(series.get("flow", [])) if isinstance(series, dict) else []
    lower = list(series.get("lower", [])) if isinstance(series, dict) else []
    upper = list(series.get("upper", [])) if isinstance(series, dict) else []
    has_band = bool(duties) and all(
        len(values) == len(duties) for values in (fitted, lower, upper)
    )
    traces: list[dict] = [
        {
            "type": "scatter",
            "mode": "markers",
            "name": "Measurements",
            "x": [point[0] for point in measured],
            "y": [point[1] for point in measured],
            "marker": {"size": 9, "color": "#1f77b4"},
        },
    ]
    if has_band:
        traces.extend(
            [
                {
                    "type": "scatter",
                    "mode": "lines",
                    "name": "95% lower",
                    "x": duties,
                    "y": lower,
                    "line": {"width": 0},
                    "hoverinfo": "skip",
                    "showlegend": False,
                },
                {
                    "type": "scatter",
                    "mode": "lines",
                    "name": "95% prediction band",
                    "x": duties,
                    "y": upper,
                    "line": {"width": 0},
                    "fill": "tonexty",
                    "fillcolor": "rgba(31, 119, 180, 0.18)",
                },
                {
                    "type": "scatter",
                    "mode": "lines",
                    "name": f"{calibration.get('model', 'linear')} fit",
                    "x": duties,
                    "y": fitted,
                    "line": {"color": "#1f77b4", "width": 2},
                },
            ],
        )
    markers = (
        ("min", calibration["min_duty"], "#d62728"),
        ("dispense", calibration["dispense_duty"], "#ff7f0e"),
        ("max", calibration["max_duty"], "#2ca02c"),
    )
    layout = {
        "height": 420,
        "margin": {"l": 55, "r": 20, "t": 35, "b": 50},
        "xaxis": {"title": "Duty (counts)"},
        "yaxis": {"title": "Flow (mL/min)"},
        "legend": {"orientation": "h"},
        "shapes": [
            {
                "type": "line",
                "x0": duty,
                "x1": duty,
                "y0": 0,
                "y1": 1,
                "yref": "paper",
                "line": {"color": color, "dash": "dot"},
            }
            for _, duty, color in markers
        ],
        "annotations": [
            {
                "x": duty,
                "y": 1,
                "yref": "paper",
                "text": name,
                "showarrow": False,
                "font": {"color": color, "size": 10},
            }
            for name, duty, color in markers
        ],
    }
    return ({"data": traces, "layout": layout}, has_band)
