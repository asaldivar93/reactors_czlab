"""Fit, store and reload the linear calibration of a pump.

Standard library only: this module runs on the Pi, which carries neither
numpy nor psycopg. The fit is an ordinary least squares of ``flow`` on
``duty`` and needs no more than that.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

from reactors_czlab.core.data import MAX_OUTPUT, Calibration

if TYPE_CHECKING:
    from reactors_czlab.core.actuator import Actuator
    from reactors_czlab.core.data import Channel

_logger = logging.getLogger("server.calibration")

#: Environment variable overriding where calibrations are stored.
CALIBRATION_ENV = "REACTORS_CALIBRATION_DIR"

#: Fewest distinct duty points a fit will accept.
MIN_POINTS = 2

#: A calibration point shorter than this cannot be measured accurately.
MIN_RUN_SECONDS = 1.0

#: Upper bound, so a mistyped duration cannot run a pump dry.
MAX_RUN_SECONDS = 600.0


def calibration_dir() -> Path:
    """Directory holding the calibration files, created if missing."""
    override = os.environ.get(CALIBRATION_ENV)
    path = (
        Path(override)
        if override
        else Path.home() / ".reactors_czlab" / "calibrations"
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def calibration_path(name: str) -> Path:
    """Path of the calibration file for ``name``."""
    return calibration_dir() / f"{name}.json"


def fit_line(
    points: list[tuple[float, float]],
) -> tuple[float, float, float]:
    """Fit ``flow = a * duty + b`` by ordinary least squares.

    Parameters
    ----------
    points:
        Measured ``(duty, flow)`` pairs.

    Returns
    -------
    tuple
        ``(a, b, r2)``.

    Raises
    ------
    ValueError
        If fewer than ``MIN_POINTS`` distinct duty values were measured, or
        if the fitted slope is not positive - a pump that delivers less at a
        higher duty is wired backwards or was measured wrongly, and its line
        cannot be safely inverted.

    """
    if len({duty for duty, _ in points}) < MIN_POINTS:
        error_message = (
            f"need at least {MIN_POINTS} distinct duty points, got "
            f"{len(points)} measurements"
        )
        raise ValueError(error_message)

    n = len(points)
    mean_x = sum(duty for duty, _ in points) / n
    mean_y = sum(flow for _, flow in points) / n
    sxx = sum((duty - mean_x) ** 2 for duty, _ in points)
    sxy = sum((duty - mean_x) * (flow - mean_y) for duty, flow in points)

    a = sxy / sxx
    if a <= 0:
        error_message = (
            f"fitted slope {a:.6g} is not positive; the pump delivers less "
            "at a higher duty"
        )
        raise ValueError(error_message)
    b = mean_y - a * mean_x

    syy = sum((flow - mean_y) ** 2 for _, flow in points)
    r2 = 0.0 if syy == 0 else (sxy**2) / (sxx * syy)

    return a, b, r2


def save_calibration(cal: Calibration) -> None:
    """Write a calibration to its file atomically.

    The temp-file-then-replace dance means a power cut during the write
    leaves either the old calibration or the new one, never a half file.
    """
    path = calibration_path(cal.file)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(cal), indent=2), encoding="utf-8")
    os.replace(tmp, path)
    _logger.info("Saved calibration %s: a=%s b=%s", cal.file, cal.a, cal.b)


def load_calibration(name: str) -> Calibration | None:
    """Read a stored calibration.

    Every step - resolving and creating the calibration directory,
    reading the file, parsing its JSON, and coercing its fields - is
    covered by the same guard, because any of them can fail on a
    hand-edited file or an unfriendly filesystem and none of them may
    take the server down.

    The guard is a bare ``except Exception``, deliberately. This function
    reads a file an operator can hand-edit into anything: earlier passes
    only anticipated ``OSError``/``ValueError``/``TypeError`` and still
    missed ``OverflowError`` from ``float()`` on an oversized JSON
    integer. Enumerating every exception type ``json.loads``,
    ``Calibration(**raw)``, ``float()`` and tuple-unpacking can raise on
    adversarial input is not a task with a stable finish line, and the
    function's one contract - log and return ``None`` - does not depend
    on which exception type it was. ``BaseException`` subclasses that are
    not ``Exception`` (``KeyboardInterrupt``, ``SystemExit``) still
    propagate, which is correct: those are not data problems.

    Returns
    -------
    Calibration or None
        ``None`` when the calibration directory cannot be created, the
        file is absent, unreadable, malformed, has a field of the wrong
        type or value, or holds a line that cannot be inverted. Every
        one of those is logged and left for the operator.

    """
    try:
        path = calibration_path(name)
        if not path.exists():
            _logger.warning("No stored calibration for %s at %s", name, path)
            return None

        raw = json.loads(path.read_text(encoding="utf-8"))
        cal = Calibration(**raw)
        cal.a = float(cal.a)
        cal.b = float(cal.b)
        cal.min_duty = float(cal.min_duty)
        cal.max_duty = float(cal.max_duty)
        cal.dispense_duty = float(cal.dispense_duty)
        cal.r2 = float(cal.r2)
        cal.points = [(float(d), float(f)) for d, f in cal.points]
    except Exception:  # see docstring: this function must never raise
        _logger.exception("Unreadable calibration file for %s", name)
        return None

    if cal.a <= 0:
        _logger.warning(
            "Calibration %s has a non-positive slope %s, ignoring",
            name,
            cal.a,
        )
        return None
    return cal


def load_into(channel: Channel) -> bool:
    """Install the stored calibration for ``channel``, if there is one.

    Returns
    -------
    bool
        True when a stored calibration replaced the channel's placeholder.
        False when there was nothing stored, or what was stored is not
        safe to install - either way the channel keeps its previous
        (placeholder or otherwise) calibration.

    """
    if channel.calibration is None:
        return False

    stored = load_calibration(channel.calibration.file)
    if stored is None:
        return False

    reason = stored.installable_reason()
    if reason is not None:
        _logger.warning(
            "Stored calibration for %s is unusable, keeping the "
            "existing one: %s",
            channel.calibration.file,
            reason,
        )
        return False

    channel.calibration = stored
    return True


class CalibrationRun:
    """Collect calibration points from one actuator and fit them.

    Every method returns a status string: the operator drives this from a
    generic OPC client and reads the result straight off the method call.
    """

    def __init__(
        self,
        actuator: Actuator,
        clock: Callable[[], float] = perf_counter,
        sleep: Callable[[float], object] = asyncio.sleep,
    ) -> None:
        """Attach a run to an actuator.

        Parameters
        ----------
        actuator:
            The pump being calibrated.
        clock, sleep:
            Injectable so the tests neither wait nor guess at drift.

        """
        self.actuator = actuator
        self.points: list[tuple[float, float]] = []
        # Public so a test - or a bench script - can swap them after
        # construction; ActuatorOpc builds the run itself.
        self.clock = clock
        self.sleep = sleep

        self._pending: tuple[float, float] | None = None
        self._running = False

    def __repr__(self) -> str:
        """Print the actuator and how many points are collected."""
        return f"CalibrationRun({self.actuator.id}, {len(self.points)} points)"

    async def calibrate_point(self, duty: float, seconds: float) -> str:
        """Run the pump at ``duty`` for ``seconds``, then stop it.

        The elapsed time is measured rather than assumed: ``asyncio.sleep``
        overshoots, and that overshoot would go straight into the flow.
        """
        if self._running:
            return f"{self.actuator.id} is already calibrating"
        if not 0 <= duty <= MAX_OUTPUT:
            return f"duty must be within 0 - {MAX_OUTPUT}, got {duty}"
        if not MIN_RUN_SECONDS <= seconds <= MAX_RUN_SECONDS:
            return (
                f"seconds must be within {MIN_RUN_SECONDS} - "
                f"{MAX_RUN_SECONDS}, got {seconds}"
            )

        self._running = True
        self.actuator.calibrating = True
        start = self.clock()
        try:
            self.actuator.write(duty)
            await self.sleep(seconds)
        finally:
            elapsed = self.clock() - start
            self.actuator.write(0)
            # write() bypasses the change guard, so put old_value back in
            # step or the control loop will not rewrite the same value.
            self.actuator.channel.old_value = 0
            self.actuator.calibrating = False
            self._running = False

        self._pending = (duty, elapsed)
        _logger.info(
            "Calibration point on %s: duty %s for %.3fs",
            self.actuator.id,
            duty,
            elapsed,
        )
        return (
            f"ran duty {duty} for {elapsed:.3f}s - now record the measured "
            "volume in mL"
        )

    def record_point(self, volume_ml: float) -> str:
        """Attach the operator's measured volume to the last run."""
        if self._pending is None:
            return "no point is waiting for a measurement"

        duty, elapsed = self._pending
        self._pending = None
        flow = volume_ml / (elapsed / 60.0)
        self.points.append((duty, flow))
        return (
            f"duty {duty} -> {flow:.4f} mL/min "
            f"({len(self.points)} points collected)"
        )

    def fit(self) -> str:
        """Fit, store and install the collected points."""
        current = self.actuator.channel.calibration
        if current is None:
            return (
                f"{self.actuator.id} has no calibration slot on its "
                "channel; give it one in server_info.py"
            )

        try:
            a, b, r2 = fit_line(self.points)
        except ValueError as exc:
            _logger.warning("Fit refused for %s: %s", self.actuator.id, exc)
            return str(exc)

        min_duty = self._stall_floor(a, b, current.max_duty)
        cal = Calibration(
            file=current.file,
            a=a,
            b=b,
            min_duty=min_duty,
            max_duty=current.max_duty,
            dispense_duty=current.dispense_duty,
            points=list(self.points),
            fitted_at=datetime.now(UTC).isoformat(),
            r2=r2,
        )
        reason = cal.installable_reason()
        if reason is not None:
            _logger.warning("Fit refused for %s: %s", self.actuator.id, reason)
            return f"{reason} - keeping the old calibration"

        save_calibration(cal)
        self.actuator.channel.calibration = cal
        self.actuator.refresh_controller_limits()
        return (
            f"fitted flow = {a:.6g} * duty + {b:.6g} (r2 {r2:.4f}), "
            f"stall floor {cal.min_duty:.0f}"
        )

    def clear_points(self) -> str:
        """Throw the collected points away, keeping the installed line."""
        self.points = []
        self._pending = None
        return f"cleared the collected points for {self.actuator.id}"

    def reload(self) -> str:
        """Re-read the stored calibration from disk."""
        current = self.actuator.channel.calibration
        if current is None:
            return f"{self.actuator.id} has no calibration slot on its channel"

        stored = load_calibration(current.file)
        if stored is None:
            return f"no usable stored calibration for {current.file}"

        # A calibration file is operator-editable, and load_calibration()
        # only checks the slope's sign. installable_reason() is the one
        # place that decides whether the rest of the numbers are safe to
        # drive a pump with - not gated on is_fitted, since a hand-edited
        # file can set fitted_at to "" while leaving dangerous numbers in
        # the rest of the fields.
        reason = stored.installable_reason()
        if reason is not None:
            return (
                f"stored calibration for {current.file} is unusable: "
                f"{reason} - not installing it"
            )

        self.actuator.channel.calibration = stored
        self.actuator.refresh_controller_limits()
        return f"reloaded {current.file}, fitted at {stored.fitted_at}"

    def set_duties(self, min_duty: float, dispense_duty: float) -> str:
        """Adjust the stall floor and the bolus duty without a refit."""
        cal = self.actuator.channel.calibration
        if cal is None:
            return f"{self.actuator.id} has no calibration slot on its channel"
        if not 0 <= min_duty <= MAX_OUTPUT:
            return f"min duty must be within 0 - {MAX_OUTPUT}, got {min_duty}"
        if not 0 <= dispense_duty <= MAX_OUTPUT:
            return f"dispense duty must be within 0 - {MAX_OUTPUT}"

        # Validate a candidate rather than the live object, so a refused
        # change cannot leave the channel's calibration half-updated.
        candidate = replace(
            cal,
            min_duty=min_duty,
            dispense_duty=dispense_duty,
        )
        reason = candidate.installable_reason()
        if reason is not None:
            return reason

        cal.min_duty = min_duty
        cal.dispense_duty = dispense_duty
        save_calibration(cal)
        self.actuator.refresh_controller_limits()
        return f"min duty {min_duty}, dispense duty {dispense_duty}"

    def _stall_floor(self, a: float, b: float, max_duty: float) -> float:
        """Lowest duty the pump is believed to actually turn at.

        The fitted x-intercept is the estimate; a point that measured no
        volume at all is direct evidence and overrides it. Either one
        is capped at ``max_duty`` - a zero-flow reading taken above the
        pump's own ceiling is still evidence the pump does not turn
        there, but adopting it verbatim could push the floor past the
        ceiling and invert the usable band.
        """
        floor = max(0.0, -b / a)
        measured = [duty for duty, flow in self.points if flow <= 0]
        if measured:
            floor = max(floor, max(measured))
        return min(floor, max_duty)
