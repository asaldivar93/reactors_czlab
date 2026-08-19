"""Turn a controller's demand into a duty value for a pump.

A controller answers *what should I demand?*; this module answers *how do I
deliver that?*. Keeping the two apart is what lets the existing PID and
on-boundaries strategies command a pump in mL/min or mL without knowing that
pumps or calibrations exist.

Standard library only - this runs on the Pi.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING

from reactors_czlab.core.data import MAX_OUTPUT, MIN_DISPENSE_FLOW, OutputUnit

if TYPE_CHECKING:
    from reactors_czlab.core.data import Channel

_logger = logging.getLogger("server.dispenser")

#: Fallback decision period for an actuator that no Reactor owns - in tests
#: or on the bench. Deliberately non-zero: a zero period would disable the
#: volume-mode re-trigger guard entirely.
DEFAULT_CONTROL_PERIOD = 10.0

#: Seconds in a minute. Flow is mL/min, every clock here is seconds.
_SECONDS_PER_MINUTE = 60.0

#: Longest one ordinary manual, timer, or boundary dose may run.
MAX_NON_PID_DOSE_SECONDS = 60.0 * 60.0


@dataclass(frozen=True)
class DosePolicy:
    """Server-authoritative delivery limit for one volume decision."""

    duty: float
    flow: float
    max_duration: float
    max_volume: float


@dataclass(frozen=True)
class DosePlan:
    """One volume request translated into a quantized pump run."""

    duty: float | int
    time_s: float
    flow: float
    delivered: float
    residual: float
    saturated: bool

    @property
    def duration(self) -> float:
        """Alias spelling out that ``time_s`` is a duration."""
        return self.time_s

    @property
    def predicted_volume(self) -> float:
        """Alias for the volume expected from this plan."""
        return self.delivered


def check_unit(unit: OutputUnit, channel: Channel) -> str | None:
    """Check whether ``unit`` can be used on ``channel``.

    Parameters
    ----------
    unit:
        The output unit a new control config asks for.
    channel:
        The actuator channel the config would drive.

    Returns
    -------
    str or None
        ``None`` when the unit is usable, otherwise why it is not. Callers
        reject the config rather than silently treating mL/min as raw
        counts, which would peg a pump.

    Notes
    -----
    ``duty`` mode needs nothing from the calibration and is always
    allowed. ``flow``/``volume`` additionally need a *fitted*
    calibration - unlike ``Calibration.installable_reason()``, which
    intentionally does not gate on ``fitted_at`` (a channel is allowed
    to hold an unfitted placeholder), a control mode that is about to
    convert mL/min or mL into duty needs a calibration that was
    actually produced by a fit, not just one whose numbers happen to be
    self-consistent. Past that gate, the remaining question - is this
    specific fitted line safe to drive the pump with - is delegated to
    ``installable_reason()`` rather than re-checked here, so the two
    functions cannot drift apart the way the install sites once did.

    """
    if unit is OutputUnit.duty:
        return None

    cal = channel.calibration
    if cal is None or not cal.is_fitted:
        return f"{unit} needs a fitted calibration on the channel"
    return cal.installable_reason()


class Dispenser:
    """Convert a demand into a duty value and account for what was pumped."""

    def __init__(
        self,
        unit: OutputUnit,
        channel: Channel,
        control_period: float = DEFAULT_CONTROL_PERIOD,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        """Build a dispenser for one actuator channel.

        Parameters
        ----------
        unit:
            Unit the controller's demand is expressed in.
        channel:
            The actuator channel. The calibration is read through it on
            every call, so a refit is picked up with no rewiring.
        control_period:
            Seconds between control decisions.
        clock:
            Monotonic clock, injectable so dose timing is testable.

        Raises
        ------
        ValueError
            If ``control_period`` is not positive. A zero or negative
            period would silently disable the volume-mode re-trigger
            guard (every ``now - self._last_decision`` gap satisfies
            ``< control_period``), which is exactly the regression the
            guard exists to prevent.

        """
        if control_period <= 0:
            error_message = (
                f"control_period must be positive, got {control_period}"
            )
            raise ValueError(error_message)

        self.unit = unit
        self.channel = channel
        self.control_period = control_period
        self.total_volume = 0.0

        self._clock = clock
        self._current_duty = 0.0
        self._since = clock()
        self._dose_until: float | None = None
        self._last_decision = float("-inf")

    def __repr__(self) -> str:
        """Print the unit and how much has been delivered."""
        return f"Dispenser({self.unit}, {self.total_volume:.3f} mL)"

    def duty(self, demand: float, *, pid: bool = False) -> float:
        """Duty counts realising ``demand``, and the new pump state.

        A non-finite demand (``inf``, ``-inf`` or ``nan``) is rejected
        here, before the unit is even looked at, because each unit turns
        one into a different failure and none of them is survivable:
        volume computes a deadline ``tick()``'s ``now <
        self._dose_until`` can never reach, stranding the pump ON; flow
        inverts it to a non-finite duty (``nan < min_duty`` and ``min(nan,
        max_duty)`` both keep the ``nan``); and duty passes it straight
        through. The last two end at ``int(value)`` in
        ``PlcActuator.write``, which raises ``ValueError`` on a ``nan``
        out of the sampling loop and takes every reactor on the server
        down. Rejecting once at the single door every demand comes
        through is what keeps the three units from needing three
        separate guards that can drift apart.
        """
        now = self._clock()
        if not math.isfinite(demand):
            _logger.warning(
                "Rejecting non-finite %s demand %s; leaving pump off",
                self.unit,
                demand,
            )
            self._dose_until = None
            return self._apply(0.0, now)
        if self.unit is OutputUnit.duty:
            return self._apply(demand, now)
        if self.unit is OutputUnit.flow:
            return self._apply(self._duty_for_flow(demand), now)
        return self._start_dose(demand, now, pid=pid)

    def tick(self) -> float | None:
        """Advance the delivery of a demand already accepted.

        Returns
        -------
        float or None
            A duty value to write, or ``None`` when nothing changes. Volume
            accrual happens either way.

        """
        now = self._clock()
        if self._dose_until is None or now < self._dose_until:
            self._accrue(now)
            return None
        self._dose_until = None
        return self._apply(0.0, now)

    def demand_limits(self, *, pid: bool = False) -> tuple[float, float]:
        """Range a controller may demand, in this dispenser's unit."""
        if self.unit is OutputUnit.duty:
            return (0.0, MAX_OUTPUT)
        cal = self.channel.calibration
        if self.unit is OutputUnit.flow:
            return (0.0, cal.flow_at(cal.max_duty))
        return (0.0, self.dose_policy(pid=pid).max_volume)

    def dose_policy(self, *, pid: bool = False) -> DosePolicy:
        """Return duty, flow, duration, and volume for a volume decision.

        PID decisions use the uncertainty-qualified ``max_duty`` for at
        most one sampling period. Other methods use ``dispense_duty`` for
        at most one hour. The PID volume is moved one representable float
        toward zero so a clamped request cannot round to a duration just
        beyond its period.
        """
        cal = self.channel.calibration
        if pid:
            duty = cal.max_duty
            max_duration = self.control_period
        else:
            duty = min(cal.dispense_duty, cal.max_duty)
            max_duration = MAX_NON_PID_DOSE_SECONDS
        flow = cal.flow_at(duty)
        max_volume = flow * max_duration / _SECONDS_PER_MINUTE
        if pid and max_volume > 0.0:
            max_volume = math.nextafter(max_volume, 0.0)
        return DosePolicy(duty, flow, max_duration, max_volume)

    def plan_dose(self, volume: float, *, pid: bool) -> DosePlan:
        """Choose the duty and duration for a requested volume.

        Non-PID requests keep the configured fixed dispense duty. PID
        requests use duty as the coarse control, target the midpoint between
        one second and the live sampling period, quantize to integer counts,
        then recompute duration from the achieved flow.

        Parameters
        ----------
        volume:
            Requested volume in mL.
        pid:
            Whether the request came from a PID controller.

        Returns
        -------
        DosePlan
            Quantized duty, duration and predicted delivery.

        Raises
        ------
        ValueError
            If ``volume`` is negative or non-finite.

        """
        if not math.isfinite(volume) or volume < 0:
            error_message = f"requested volume must be finite and non-negative, got {volume}"
            raise ValueError(error_message)
        if volume == 0:
            return DosePlan(0, 0.0, 0.0, 0.0, 0.0, False)

        cal = self.channel.calibration
        if not pid:
            policy = self.dose_policy(pid=False)
            effective = min(volume, policy.max_volume)
            duration = _SECONDS_PER_MINUTE * effective / policy.flow
            return DosePlan(
                policy.duty,
                duration,
                policy.flow,
                effective,
                volume - effective,
                effective != volume,
            )

        minimum_duration = 1.0
        maximum_duration = self.control_period
        if maximum_duration < minimum_duration:
            error_message = (
                "PID dose planning requires a control period of at least "
                f"one second, got {maximum_duration}"
            )
            raise ValueError(error_message)
        target_duration = (minimum_duration + maximum_duration) / 2.0
        wanted_flow = _SECONDS_PER_MINUTE * volume / target_duration
        minimum_duty = math.ceil(cal.min_duty)
        maximum_duty = math.floor(cal.max_duty)
        minimum_flow = cal.flow_at(minimum_duty)
        if minimum_flow <= 0.0 and minimum_duty < maximum_duty:
            minimum_duty = min(
                maximum_duty,
                max(
                    minimum_duty + 1,
                    math.ceil(cal.duty_for(MIN_DISPENSE_FLOW)),
                ),
            )
            minimum_flow = cal.flow_at(minimum_duty)
        maximum_flow = cal.flow_at(maximum_duty)
        if wanted_flow <= minimum_flow:
            duty = minimum_duty
        elif wanted_flow >= maximum_flow:
            duty = maximum_duty
        else:
            duty = min(
                maximum_duty,
                max(minimum_duty, round(cal.duty_for(wanted_flow))),
            )
        achieved_flow = cal.flow_at(duty)
        unconstrained_duration = _SECONDS_PER_MINUTE * volume / achieved_flow
        duration = min(
            maximum_duration,
            max(minimum_duration, unconstrained_duration),
        )
        delivered = achieved_flow * duration / _SECONDS_PER_MINUTE
        residual = volume - delivered
        saturated = not math.isclose(duration, unconstrained_duration, rel_tol=1e-12, abs_tol=1e-12)
        if duration == minimum_duration and delivered > volume:
            _logger.warning(
                "Requested PID dose %.6g mL is below the one-second minimum; predicted volume is %.6g mL",
                volume,
                delivered,
            )
        return DosePlan(duty, duration, achieved_flow, delivered, residual, saturated)

    def reset(self) -> None:
        """Forget any delivery in flight. Totals are kept.

        Returns nothing, unlike every other off-path here, which returns
        ``0.0`` for the caller to write through to the PLC. The caller
        must write 0 to the actuator itself after calling this - no
        later ``tick()`` will do it, since there is no dose left to
        expire.
        """
        self._accrue(self._clock())
        self._dose_until = None
        self._current_duty = 0.0
        self._last_decision = float("-inf")

    def _duty_for_flow(self, demand: float) -> float:
        """Invert the calibration line, respecting the pump's usable band."""
        if demand <= 0:
            return 0.0
        cal = self.channel.calibration
        duty = cal.duty_for(demand)
        if duty < cal.min_duty:
            _logger.debug(
                "Demand %s mL/min is below the stall floor of %s counts",
                demand,
                cal.min_duty,
            )
            duty = cal.min_duty
        return min(duty, cal.max_duty)

    def _start_dose(self, demand: float, now: float, *, pid: bool) -> float:
        """Accept a volume demand, unless the guard is still holding.

        A dose is an event, but ``write_output()`` is called every
        ``UNPAIRED_PERIOD`` for an unpaired actuator. Rate-limiting
        decisions to ``control_period`` is what stops a standing manual
        demand from being dispensed twenty times a second, and it makes
        paired and unpaired actuators behave identically.

        A non-finite demand never reaches here - ``duty()`` rejects one
        for every unit before dispatching, since an infinite deadline
        would never come due and would strand the pump ON.
        Finite demands are capped by ``dose_policy``. Normal manual,
        timer, and boundary decisions may run for at most one hour at the
        configured dispense duty. PID decisions may run for at most one
        period at the uncertainty-qualified maximum duty.

        The duty written is capped at ``max_duty``, mirroring what
        ``_duty_for_flow`` does with a converted duty. For every
        calibration the package installs this is a no-op -
        ``Calibration.installable_reason()`` refuses any calibration
        whose ``dispense_duty`` falls outside ``[min_duty, max_duty]``,
        and that is where the decision belongs. It is kept here as
        defense in depth for a calibration object mutated in place after
        it was installed, where the raw field would otherwise go to the
        pin unclamped. The deadline is computed from the same capped
        duty, so the time the pump runs always matches the duty it is
        actually run at.
        """
        if demand <= 0:
            self._dose_until = None
            return self._apply(0.0, now)
        if now - self._last_decision < self.control_period:
            return self._current_duty
        self._last_decision = now

        plan = self.plan_dose(demand, pid=pid)
        self._dose_until = now + plan.time_s
        _logger.debug(
            "Dispensing requested dose %s mL, effective %s mL over %.3fs",
            demand,
            plan.delivered,
            plan.time_s,
        )
        return self._apply(plan.duty, now)

    def _apply(self, value: float, now: float) -> float:
        """Account for the duty that was running, then take the new one."""
        self._accrue(now)
        self._current_duty = value
        return value

    def _accrue(self, now: float) -> None:
        """Add what the pump delivered since the last accounting point.

        Integrating the *actual* duty over the *actual* elapsed time, rather
        than summing demanded volumes, keeps the total right when a
        delivery is superseded, clipped or interrupted.

        The accrual never goes negative. ``flow_at()`` reads negative
        below the line's zero crossing, and ``duty`` mode - the default
        unit on every calibrated pump - has no floor stopping a
        controller from sitting there: ``flow`` mode raises a converted
        duty to ``min_duty`` and ``volume`` mode only ever writes a
        ``dispense_duty`` that ``installable_reason()`` has checked
        delivers positive flow, but a manual duty of 100 on a pump that
        stalls below 500 is an ordinary thing to command. The pump is
        not turning there, so it delivers nothing; a meter that counted
        down would be worse than one that stalled, since it hides
        earlier real dosing.
        """
        cal = self.channel.calibration
        if cal is not None and cal.is_fitted and self._current_duty > 0:
            flow = cal.flow_at(self._current_duty)
            if self._current_duty < cal.min_duty or flow < 0:
                flow = 0.0
            elapsed = (now - self._since) / _SECONDS_PER_MINUTE
            self.total_volume += flow * elapsed
        self._since = now
