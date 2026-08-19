"""Collect pump calibration measurements and install fitted models."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from math import isfinite
from time import perf_counter
from typing import TYPE_CHECKING

from reactors_czlab.core.calibration.fitting import fit_models
from reactors_czlab.core.calibration.models import (
    MIN_DISPENSE_FLOW,
    Calibration,
    calibration_flow,
    calibration_zero_threshold,
)
from reactors_czlab.core.calibration.storage import (
    load_calibration,
    replacement_reason,
    save_calibration,
)
from reactors_czlab.core.data import MAX_OUTPUT

if TYPE_CHECKING:
    from reactors_czlab.core.actuator import Actuator

_logger = logging.getLogger("server.calibration")

#: A calibration point shorter than this cannot be measured accurately.
MIN_RUN_SECONDS = 1.0

#: Upper bound, so a mistyped duration cannot run a pump dry.
MAX_RUN_SECONDS = 600.0


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
        self.zero_flow_duty: float | None = None
        # Public so a test - or a bench script - can swap them after
        # construction; ActuatorOpc builds the run itself.
        self.clock = clock
        self.sleep = sleep

        self._pending: tuple[float, float] | None = None
        self._running = False

    def __repr__(self) -> str:
        """Print the actuator and how many points are collected."""
        return f"CalibrationRun({self.actuator.id}, {len(self.points)} points)"

    @property
    def is_running(self) -> bool:
        """Whether the pump is running for a calibration point right now.

        Read-only view of the flag ``calibrate_point`` sets, so a user
        interface can disable the controls a run must not race with
        without reaching into the run's internals.
        """
        return self._running

    @property
    def pending(self) -> tuple[float, float] | None:
        """The ``(duty, seconds)`` awaiting a measured volume, if any.

        ``record_point`` is the only useful thing to do while this is
        set, and a refused measurement deliberately leaves it in place
        so the operator can retype without re-running the pump.
        """
        return self._pending

    async def calibrate_point(self, duty: float, seconds: float) -> str:
        """Run the pump at ``duty`` for ``seconds``, then stop it.

        The elapsed time is measured rather than assumed: ``asyncio.sleep``
        overshoots, and that overshoot would go straight into the flow.
        """
        if self._running:
            return f"{self.actuator.id} is already calibrating"
        if self.actuator.autotune_owner is not None:
            return f"{self.actuator.id} is owned by an active autotune"
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
        """Attach the operator's measured volume to the last run.

        The volume arrives from a generic OPC client as a ``Float``, so
        it can be ``inf`` or ``nan``, and neither is caught downstream:
        the model fitter's parameter checks and every branch of
        ``Calibration.installable_reason()`` are comparisons, and a
        comparison against ``nan`` is false. A single bad argument
        therefore used to reach the calibration file and reload from it
        at every boot. Zero is accepted as direct stall evidence and is
        retained in ``zero_flow_duty`` rather than appended to the fitted
        points.

        The derived flow is checked as well as the argument: a finite
        but enormous volume over the shortest allowed run still
        overflows to ``inf`` in the division.

        A refused measurement leaves the pending point in place, so the
        operator can retype the number without re-running the pump.
        """
        if self._pending is None:
            return "no point is waiting for a measurement"
        if not 0 <= volume_ml < float("inf"):
            return (
                "volume must be a finite number of mL, zero or more, "
                f"got {volume_ml}"
            )

        duty, elapsed = self._pending
        flow = volume_ml / (elapsed / 60.0)
        if not 0 <= flow < float("inf"):
            return (
                f"volume {volume_ml} mL over {elapsed:.3f}s is not a "
                "flow that can be represented; measure a smaller volume"
            )

        self._pending = None
        if flow == 0.0:
            self.zero_flow_duty = max(
                duty,
                self.zero_flow_duty if self.zero_flow_duty is not None else 0.0,
            )
            return (
                f"duty {duty} recorded as zero-flow stall evidence "
                "(excluded from curve fitting)"
            )
        self.points.append((duty, flow))
        return (
            f"duty {duty} -> {flow:.4f} mL/min "
            f"({len(self.points)} points collected)"
        )

    def import_points(self, points: list[tuple[float, float]]) -> str:
        """Replace this run's measurements with previously recorded flows.

        Positive-flow rows become fitted measurements. A zero-flow row is
        retained only as stall evidence, with the highest such duty winning.
        This is intended for importing a run from a CSV at server startup;
        it never drives the pump.

        Parameters
        ----------
        points:
            ``(duty, flow_ml_min)`` measurements to import.

        Returns
        -------
        str
            An operator-readable import summary.

        Raises
        ------
        ValueError
            If a row contains an out-of-range duty, a non-finite value, or a
            negative flow.

        """
        if self._running:
            error_message = f"{self.actuator.id} is still calibrating"
            raise ValueError(error_message)
        if self._pending is not None:
            error_message = (
                f"{self.actuator.id} has a point awaiting a measurement"
            )
            raise ValueError(error_message)

        positive: list[tuple[float, float]] = []
        zero_flow_duty: float | None = None
        for duty, flow in points:
            if not isfinite(duty) or not 0 <= duty <= MAX_OUTPUT:
                error_message = (
                    f"imported duty must be finite and within 0 - "
                    f"{MAX_OUTPUT:.0f}, got {duty}"
                )
                raise ValueError(error_message)
            if not isfinite(flow) or flow < 0.0:
                error_message = (
                    "imported flow must be a finite number of mL/min, zero "
                    f"or more, got {flow} at duty {duty}"
                )
                raise ValueError(error_message)
            if flow == 0.0:
                zero_flow_duty = max(
                    duty,
                    zero_flow_duty if zero_flow_duty is not None else 0.0,
                )
            else:
                positive.append((duty, flow))

        self.points = positive
        self.zero_flow_duty = zero_flow_duty
        zero_summary = (
            f", zero-flow evidence at duty {zero_flow_duty:.0f}"
            if zero_flow_duty is not None
            else ""
        )
        return f"imported {len(positive)} positive-flow points{zero_summary}"

    def fit(self, zero_flow_duty: float | None = None) -> str:
        """Fit, store and install the collected positive-flow points.

        Parameters
        ----------
        zero_flow_duty:
            Optional stall evidence. It is retained separately and never
            participates in coefficients, residuals, AIC or uncertainty.

        """
        current = self.actuator.channel.calibration
        if current is None:
            return (
                f"{self.actuator.id} has no calibration slot on its "
                "channel; give it one in server_info.py"
            )

        if zero_flow_duty is not None:
            if not 0 <= zero_flow_duty < float("inf") or zero_flow_duty > MAX_OUTPUT:
                return (
                    f"zero-flow duty must be a finite duty within 0 - "
                    f"{MAX_OUTPUT:.0f}, got {zero_flow_duty}"
                )
            self.zero_flow_duty = max(
                zero_flow_duty,
                self.zero_flow_duty if self.zero_flow_duty is not None else 0.0,
            )

        try:
            fitted = fit_models(self.points)
        except ValueError as exc:
            _logger.warning("Fit refused for %s: %s", self.actuator.id, exc)
            return str(exc)

        min_duty = self._stall_floor(
            fitted.model,
            fitted.a,
            fitted.b,
            fitted.c,
            fitted.max_duty,
        )
        dispense_duty = current.dispense_duty
        if not (
            min_duty <= dispense_duty <= fitted.max_duty
            and self._flow_at(
                fitted.model,
                fitted.a,
                fitted.b,
                fitted.c,
                dispense_duty,
            )
            >= MIN_DISPENSE_FLOW
        ):
            dispense_duty = fitted.max_duty
        cal = Calibration(
            file=current.file,
            a=fitted.a,
            b=fitted.b,
            c=fitted.c,
            min_duty=min_duty,
            max_duty=fitted.max_duty,
            dispense_duty=dispense_duty,
            points=list(self.points),
            fitted_at=datetime.now(UTC).isoformat(),
            r2=fitted.r2,
            model=fitted.model,
            residual=fitted.residual,
            aic=fitted.aic,
            zero_flow_duty=self.zero_flow_duty,
            fit_points=fitted.fit_points,
        )
        reason = cal.installable_reason()
        if reason is not None:
            _logger.warning("Fit refused for %s: %s", self.actuator.id, reason)
            return f"{reason} - keeping the old calibration"

        save_calibration(cal)
        self.actuator.channel.calibration = cal
        self.actuator.refresh_controller_limits()
        return (
            f"fitted flow using {cal.model} model: {self._equation(cal)} "
            f"(r2 {cal.r2:.4f}, AIC {cal.aic:.6g}, residual "
            f"{cal.residual:.6g}), stall floor "
            f"{cal.min_duty:.0f}, qualified max duty {cal.max_duty:.0f}"
        )

    def clear_points(self) -> str:
        """Throw the collected points away, keeping the installed line."""
        self.points = []
        self.zero_flow_duty = None
        self._pending = None
        return f"cleared the collected points for {self.actuator.id}"

    def discard_pending_point(self) -> str:
        """Discard only the completed run awaiting a measurement."""
        if self._running:
            return f"{self.actuator.id} is still running a calibration point"
        if self._pending is None:
            return "no point is waiting for a measurement"
        duty, _ = self._pending
        self._pending = None
        return f"discarded the pending point at duty {duty}"

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
        # the rest of the fields. replacement_reason() then asks the one
        # question that does depend on fitted_at, and that
        # installable_reason() cannot answer because it never sees what
        # is being replaced.
        reason = stored.installable_reason() or replacement_reason(
            current,
            stored,
        )
        if reason is not None:
            return (
                f"stored calibration for {current.file} is unusable: "
                f"{reason} - not installing it"
            )

        self.actuator.channel.calibration = stored
        self.actuator.refresh_controller_limits()
        return f"reloaded {current.file}, fitted at {stored.fitted_at}"

    def set_duties(self, min_duty: float, dispense_duty: float) -> str:
        """Adjust the stall floor and the volume-dose duty without a refit."""
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

    def _stall_floor(
        self,
        model: str,
        a: float,
        b: float,
        c: float,
        max_duty: float,
    ) -> float:
        """Lowest duty the pump is believed to actually turn at.

        The fitted x-intercept is the estimate; a point that measured no
        volume at all is direct evidence and overrides it. Either one
        is capped at ``max_duty`` - a zero-flow reading taken above the
        pump's own ceiling is still evidence the pump does not turn
        there, but adopting it verbatim could push the floor past the
        ceiling and invert the usable band.
        """
        floor = calibration_zero_threshold(model, a, b, c)
        if self.zero_flow_duty is not None:
            floor = max(floor, self.zero_flow_duty)
        return min(floor, max_duty)

    @staticmethod
    def _flow_at(
        model: str,
        a: float,
        b: float,
        c: float,
        duty: float,
    ) -> float:
        """Evaluate fitted parameters before a Calibration exists."""
        return calibration_flow(model, a, b, c, duty)

    @staticmethod
    def _equation(calibration: Calibration) -> str:
        """Human-readable equation for a fitted calibration."""
        return calibration.numeric_equation
