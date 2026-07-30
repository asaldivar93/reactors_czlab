"""Dataclasses shared by the server and the client."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, auto

#: Written to a channel when the underlying device could not be read.
#: It is a real float, so consumers must compare against this constant
#: instead of hardcoding the literal.
ERROR_VALUE = -0.111

#: Full scale of the PLC analog/PWM outputs (0 - 10 V).
MAX_OUTPUT = 4095.0

#: Dose the bolus-time bound below is expressed for, in mL. 1 mL is the
#: order of magnitude an operator types into a volume demand.
REFERENCE_BOLUS_ML = 1.0

#: Longest a single reference bolus may hold the pump ON, in seconds
#: (24 h). ``Dispenser._start_bolus`` turns a volume demand into a
#: deadline of ``60 * demand / flow_at(dispense_duty)`` seconds and runs
#: the pump until it passes, so an almost-flat calibration line makes
#: that deadline effectively never come due - the same "stranded ON"
#: hazard the non-finite demand rejection exists for, reached through
#: the calibration instead of through the demand.
MAX_BOLUS_SECONDS = 24 * 60 * 60.0

#: Slowest flow, in mL/min, a calibration may claim at its dispense
#: duty and still be installable: the flow that just delivers
#: ``REFERENCE_BOLUS_ML`` within ``MAX_BOLUS_SECONDS``, i.e. 1 mL/day.
#: Deliberately far below any pump this system can calibrate - the
#: calibration procedure caps a single point at 600 s, so a pump this
#: slow would deliver 7 uL in the longest run an operator can measure,
#: and could never have produced an honest fit in the first place.
MIN_DISPENSE_FLOW = 60.0 * REFERENCE_BOLUS_ML / MAX_BOLUS_SECONDS

#: ``float("inf")``, kept module level so ``_is_finite`` costs one
#: comparison chain. This module imports nothing outside ``dataclasses``
#: and ``enum`` - the Pi install and the PC install have disjoint
#: dependency sets and both carry this file - so ``math.isfinite`` is not
#: available here.
_INFINITY = float("inf")

#: Calibration fields the pump path does arithmetic with. A non-finite
#: value in any of them poisons every comparison downstream of it.
_DRIVING_FIELDS = ("a", "b", "min_duty", "max_duty", "dispense_duty")


def _is_finite(value: float) -> bool:
    """Whether ``value`` is a real number, not ``nan``/``inf``/``-inf``.

    ``math.isfinite`` without ``math`` (see ``_INFINITY``). The two
    infinities fail the chain outright; ``nan`` fails it because every
    comparison against ``nan`` is false, which is the same property that
    makes a ``nan`` invisible to an ordinary range check.
    """
    return -_INFINITY < value < _INFINITY


class PlcOutput(StrEnum):
    """Kind of device behind a PhysicalInfo/Channel.

    Parameters
    ----------
    pwm, analog, digital

    """

    pwm = auto()
    analog = auto()
    digital = auto()


@dataclass
class PhysicalInfo:
    """Class holding info for the sensors/actuators."""

    model: str
    address: int
    type: PlcOutput
    channels: list[Channel]


@dataclass
class Channel:
    """Class holding config info for sensor/actuator channels."""

    units: str
    description: str = "none"
    register: str = "none"
    pin: str = "none"
    type: PlcOutput = PlcOutput.pwm
    value: float = ERROR_VALUE
    old_value: float = ERROR_VALUE
    calibration: Calibration | None = None


@dataclass
class Calibration:
    """Linear calibration of a pump: ``flow = a * duty + b``.

    Flow is mL/min and duty is raw PLC counts. ``fitted_at`` empty means the
    calibration has never been fitted and must not be used to convert.

    Parameters
    ----------
    file:
        File stem the calibration is stored under, e.g. ``R0_pwm0``.
    a, b:
        Slope and intercept of the fitted line.
    min_duty:
        Stall floor. Below this the pump does not turn.
    max_duty:
        Highest duty the pump may be driven at.
    dispense_duty:
        Duty used for volume boluses.
    points:
        Measured ``(duty, flow)`` pairs the fit was built from.
    fitted_at:
        ISO timestamp of the fit, empty when unfitted.
    r2:
        Fit quality. Informational: it is trivially 1.0 for two points.

    """

    file: str
    a: float = 1.0
    b: float = 0.0
    min_duty: float = 0.0
    max_duty: float = MAX_OUTPUT
    dispense_duty: float = MAX_OUTPUT
    points: list[tuple[float, float]] = field(default_factory=list)
    fitted_at: str = ""
    r2: float = 0.0

    @property
    def is_fitted(self) -> bool:
        """Whether the calibration has ever been fitted."""
        return bool(self.fitted_at)

    def flow_at(self, duty: float) -> float:
        """Flow in mL/min produced at ``duty`` counts."""
        return self.a * duty + self.b

    def duty_for(self, flow: float) -> float:
        """Duty counts needed for ``flow`` mL/min.

        Raises
        ------
        ZeroDivisionError
            If the slope is zero. Loading and fitting both reject a
            non-positive slope, so this only happens on a hand-edited
            object.

        """
        return (flow - self.b) / self.a

    def installable_reason(self) -> str | None:
        """Why this calibration may not replace what is on a channel.

        The single authority for "is this calibration safe to
        install", called by every site that can put a ``Calibration``
        onto a ``Channel.calibration`` - ``CalibrationRun.fit()``,
        ``set_duties()``, ``reload()``, ``load_into()`` - and by
        ``core.dispenser.check_unit()`` for the same question asked at
        control-config time. Before this existed, each of those sites
        wrote its own arithmetic, and every round of review found a
        pair that disagreed: ``<`` where another used ``<=``, a
        stall-floor check dropped in favour of a flow check that could
        not see the same evidence, a guard gated behind ``is_fitted``
        that a hand-edited file could route around by leaving
        ``fitted_at`` empty. There is now exactly one place this logic
        lives.

        Deliberately NOT gated on ``is_fitted``: an unfitted
        calibration is only actually safe when its numbers happen to
        be self-consistent, which the placeholder
        ``server_info.py`` constructs for every pump always is (``a=1.0,
        min_duty=0.0, max_duty=dispense_duty=MAX_OUTPUT``) - it passes
        every check below on its own merits, not because it is
        exempted. A hand-edited file can set ``fitted_at`` to the empty
        string while leaving dangerous numbers in the rest of the
        fields, and ``Dispenser._start_bolus`` divides by
        ``flow_at(dispense_duty)`` without ever consulting
        ``is_fitted``. So the numbers are what get checked, regardless
        of the flag.

        What an installed calibration therefore guarantees to the code
        that drives a pump with it: every field is a finite number, ``0
        <= min_duty <= dispense_duty <= max_duty <= MAX_OUTPUT``, and a
        flow at the dispense duty of at least ``MIN_DISPENSE_FLOW``.
        That is what makes every duty ``Dispenser`` writes in-scale, and
        every bolus deadline it computes both finite and reachable.

        The finiteness check has to come first, and cannot be expressed
        as one more range comparison: ``nan`` satisfies none of the
        branches below (``nan <= 0`` is false, and so is every other
        comparison), so a ``nan`` slope walks through all ten of them and
        comes out *installable*. It then survives ``json.dumps`` as the
        literal ``NaN``, reloads at every boot, disables a PID's upper
        clamp (``min(value, nan)`` returns ``value``) and finally reaches
        ``int(nan)`` on the Pi, which raises out of the sampling loop and
        takes every reactor on the server down with it.

        Every branch states the consequence that actually follows from
        the input that triggered it. A guard whose refusal message
        describes a different failure than the one it caught sends the
        operator after the wrong number, so the branches are split by
        consequence rather than merged for brevity: a dispense duty
        under the stall floor delivers *nothing*, one at exactly zero
        flow gives a bolus that never finishes, one below the line's
        zero crossing gives a bolus that finishes instantly having
        delivered nothing, and one that is merely far too slow strands
        the pump ON for as long as the deadline says.

        Returns
        -------
        str or None
            ``None`` when ``self`` may be installed. Otherwise a
            human-readable reason, safe to return to the operator
            verbatim.

        """
        for name in _DRIVING_FIELDS:
            value = getattr(self, name)
            if not _is_finite(value):
                return (
                    f"{name} is {value}, not a finite number; no range "
                    "check here or clamp in a controller can catch it, "
                    "and the duty it produces cannot be written to a pin"
                )
        if not _is_finite(self.r2):
            return (
                f"fit quality r2 is {self.r2}, not a finite number; a "
                "fit that could not score itself is not one to drive a "
                "pump with"
            )
        if self.a <= 0:
            return (
                f"slope {self.a:.6g} is not positive; a higher duty "
                "would not mean more flow, so no flow or volume demand "
                "can be turned into a duty"
            )
        if self.min_duty < 0:
            return (
                f"stall floor {self.min_duty:.0f} is negative; the "
                "floor is what keeps a converted duty at or above zero"
            )
        if self.min_duty > self.max_duty:
            return (
                f"min duty {self.min_duty:.0f} is above max duty "
                f"{self.max_duty:.0f}; there is no usable band"
            )
        if self.max_duty > MAX_OUTPUT:
            return (
                f"max duty {self.max_duty:.0f} is above the "
                f"{MAX_OUTPUT:.0f} count full scale of the output"
            )
        if self.dispense_duty < self.min_duty:
            return (
                f"dispense duty {self.dispense_duty:.0f} is below the "
                f"stall floor {self.min_duty:.0f}; the pump was "
                "measured not to turn there, so a bolus would run the "
                "clock down while delivering nothing"
            )
        if self.dispense_duty > self.max_duty:
            return (
                f"dispense duty {self.dispense_duty:.0f} is above max "
                f"duty {self.max_duty:.0f}; a bolus is written at that "
                "duty, so it would drive the pump past its ceiling"
            )
        # Implied by the checks above plus the dispense-flow checks
        # below (the slope is positive and dispense_duty <= max_duty,
        # so flow at max_duty is the largest flow in the band), but
        # kept explicit: flow mode never touches dispense_duty, and
        # "nothing anywhere in the band" is the message that fits a
        # line whose whole usable range is dead.
        if self.flow_at(self.max_duty) <= 0:
            return (
                "this calibration produces no flow anywhere in its "
                f"usable band (zero or negative at max duty "
                f"{self.max_duty:.0f})"
            )
        flow = self.flow_at(self.dispense_duty)
        if flow == 0:
            return (
                f"dispense duty {self.dispense_duty:.0f} produces no "
                "flow; a bolus at that duty would never finish"
            )
        if flow < 0:
            return (
                f"dispense duty {self.dispense_duty:.0f} is below "
                f"where the line reaches zero flow ({flow:.6g} "
                "mL/min); a bolus there would end immediately having "
                "delivered nothing"
            )
        if flow < MIN_DISPENSE_FLOW:
            return (
                f"dispense duty {self.dispense_duty:.0f} delivers only "
                f"{flow:.6g} mL/min; {REFERENCE_BOLUS_ML:.0f} mL would "
                f"take {60.0 * REFERENCE_BOLUS_ML / flow:.0f} s, past "
                f"the {MAX_BOLUS_SECONDS:.0f} s a bolus may hold the "
                "pump on"
            )
        return None


class OutputUnit(StrEnum):
    """Unit a controller's demand is expressed in.

    Parameters
    ----------
    duty, flow, volume

    """

    duty = auto()
    flow = auto()
    volume = auto()


class ControlMethod(StrEnum):
    """Available control methods.

    Parameters
    ----------
    manual, timer, on_boundaries, pid

    """

    manual = auto()
    timer = auto()
    on_boundaries = auto()
    pid = auto()


@dataclass
class ControlConfig:
    """Class holding config for controllers.

    Parameters
    ----------
    method:
        ControlMethod
    time_on:
        float (default: 0.0)
    time_off:
        float (default: 0.0)
    lb:
        float (default: 0.0)
    ub:
        float (default: 0.0)
    setpoint:
        float (default: 0.0)
    value:
        float (default: 0.0)
    output_unit:
        OutputUnit (default: OutputUnit.duty)
    kp, ki, kd:
        PID gains (defaults: 100.0, 0.01, 0.0)
    backwards:
        bool (default: False). Reverses the sense of a PID or an
        on_boundaries controller (see the respective ``get_value``).
    min_integral, max_integral:
        Explicit PID anti-windup band, used only when
        ``auto_integral_band`` is False (defaults: 0.0, MAX_OUTPUT).
    auto_integral_band:
        bool (default: True). When True the PID derives its anti-windup
        band from the output range and ignores ``min_integral`` /
        ``max_integral``; set False to install the explicit band.

    """

    method: ControlMethod
    time_on: float = 0.0
    time_off: float = 0.0
    lb: float = 0.0
    ub: float = 0.0
    setpoint: float = 0.0
    value: float = 0.0
    output_unit: OutputUnit = OutputUnit.duty
    kp: float = 100.0
    ki: float = 0.01
    kd: float = 0.0
    backwards: bool = False
    min_integral: float = 0.0
    max_integral: float = MAX_OUTPUT
    auto_integral_band: bool = True
