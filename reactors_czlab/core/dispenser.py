"""Turn a controller's demand into a duty value for a pump.

A controller answers *what should I demand?*; this module answers *how do I
deliver that?*. Keeping the two apart is what lets the existing PID and
on-boundaries strategies command a pump in mL/min or mL without knowing that
pumps or calibrations exist.

Standard library only - this runs on the Pi.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from time import perf_counter
from typing import TYPE_CHECKING

from reactors_czlab.core.data import MAX_OUTPUT, OutputUnit

if TYPE_CHECKING:
    from reactors_czlab.core.data import Channel

_logger = logging.getLogger("server.dispenser")

#: Fallback decision period for an actuator that no Reactor owns - in tests
#: or on the bench. Deliberately non-zero: a zero period would disable the
#: volume-mode re-trigger guard entirely.
DEFAULT_CONTROL_PERIOD = 10.0

#: Seconds in a minute. Flow is mL/min, every clock here is seconds.
_SECONDS_PER_MINUTE = 60.0


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

    """
    if unit is OutputUnit.duty:
        return None

    cal = channel.calibration
    if cal is None or not cal.is_fitted:
        return f"{unit} needs a fitted calibration on the channel"
    if unit is OutputUnit.volume and cal.flow_at(cal.dispense_duty) <= 0:
        return (
            f"dispense duty {cal.dispense_duty} produces no flow, so a "
            "bolus would never finish"
        )
    return None


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
            Monotonic clock, injectable so bolus timing is testable.

        """
        self.unit = unit
        self.channel = channel
        self.control_period = control_period
        self.total_volume = 0.0

        self._clock = clock
        self._current_duty = 0.0
        self._since = clock()
        self._bolus_until: float | None = None
        self._last_decision = float("-inf")

    def __repr__(self) -> str:
        """Print the unit and how much has been delivered."""
        return f"Dispenser({self.unit}, {self.total_volume:.3f} mL)"

    def duty(self, demand: float) -> float:
        """Duty counts realising ``demand``, and the new pump state."""
        now = self._clock()
        if self.unit is OutputUnit.duty:
            return self._apply(demand, now)
        if self.unit is OutputUnit.flow:
            return self._apply(self._duty_for_flow(demand), now)
        return self._start_bolus(demand, now)

    def tick(self) -> float | None:
        """Advance the delivery of a demand already accepted.

        Returns
        -------
        float or None
            A duty value to write, or ``None`` when nothing changes. Volume
            accrual happens either way.

        """
        now = self._clock()
        if self._bolus_until is None or now < self._bolus_until:
            self._accrue(now)
            return None
        self._bolus_until = None
        return self._apply(0.0, now)

    def demand_limits(self) -> tuple[float, float]:
        """Range a controller may demand, in this dispenser's unit."""
        if self.unit is OutputUnit.duty:
            return (0.0, MAX_OUTPUT)
        cal = self.channel.calibration
        if self.unit is OutputUnit.flow:
            return (0.0, cal.flow_at(cal.max_duty))
        per_period = (
            cal.flow_at(cal.dispense_duty)
            * self.control_period
            / _SECONDS_PER_MINUTE
        )
        return (0.0, per_period)

    def reset(self) -> None:
        """Forget any delivery in flight. Totals are kept."""
        self._accrue(self._clock())
        self._bolus_until = None
        self._current_duty = 0.0

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

    def _start_bolus(self, demand: float, now: float) -> float:
        """Accept a volume demand, unless the guard is still holding.

        A bolus is an event, but ``write_output()`` is called every
        ``UNPAIRED_PERIOD`` for an unpaired actuator. Rate-limiting
        decisions to ``control_period`` is what stops a standing manual
        demand from being dispensed twenty times a second, and it makes
        paired and unpaired actuators behave identically.
        """
        if now - self._last_decision < self.control_period:
            return self._current_duty
        self._last_decision = now

        if demand <= 0:
            self._bolus_until = None
            return self._apply(0.0, now)

        cal = self.channel.calibration
        seconds = _SECONDS_PER_MINUTE * demand / cal.flow_at(cal.dispense_duty)
        self._bolus_until = now + seconds
        _logger.debug("Dispensing %s mL over %.3fs", demand, seconds)
        return self._apply(cal.dispense_duty, now)

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
        """
        cal = self.channel.calibration
        if cal is not None and cal.is_fitted and self._current_duty > 0:
            elapsed = (now - self._since) / _SECONDS_PER_MINUTE
            self.total_volume += cal.flow_at(self._current_duty) * elapsed
        self._since = now
