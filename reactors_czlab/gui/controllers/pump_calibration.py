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
    def can_fit(self) -> bool:
        """Whether there is enough to fit a line.

        Two *distinct* duties, not just two points: fit_line refuses a
        pair at the same duty, and finding that out through a rejected
        fit wastes the operator's run.
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
    duty is below the pump's stall floor, which is evidence the fit
    uses.
    """
    if volume is None:
        return "Enter the measured volume"
    if volume < 0:
        return "Volume cannot be negative"
    return None
