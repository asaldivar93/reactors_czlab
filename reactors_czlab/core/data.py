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

        Returns
        -------
        str or None
            ``None`` when ``self`` may be installed. Otherwise a
            human-readable reason, safe to return to the operator
            verbatim.

        """
        if self.a <= 0:
            return f"slope {self.a:.6g} is not positive; it cannot be inverted"
        if self.min_duty > self.max_duty:
            return (
                f"min duty {self.min_duty:.0f} is above max duty "
                f"{self.max_duty:.0f}; there is no usable band"
            )
        if self.dispense_duty < self.min_duty:
            return (
                f"dispense duty {self.dispense_duty:.0f} is below the "
                f"stall floor {self.min_duty:.0f}; a bolus at that "
                "duty would never finish"
            )
        if self.flow_at(self.dispense_duty) <= 0:
            return (
                f"dispense duty {self.dispense_duty:.0f} produces no "
                "flow; a bolus at that duty would never finish"
            )
        if self.flow_at(self.max_duty) <= 0:
            return (
                "this calibration produces no flow anywhere in its "
                f"usable band (zero or negative at max duty "
                f"{self.max_duty:.0f})"
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

    """

    method: ControlMethod
    time_on: float = 0.0
    time_off: float = 0.0
    lb: float = 0.0
    ub: float = 0.0
    setpoint: float = 0.0
    value: float = 0.0
    output_unit: OutputUnit = OutputUnit.duty
