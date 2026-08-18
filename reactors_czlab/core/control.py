"""Control methods.

Each controller is a dataclass: the fields that make up its *configuration*
take part in equality, the fields that change while it runs do not. That is
what lets Actuator.set_control_config() tell "the user changed the setpoint"
apart from "the timer toggled since the last comparison".
"""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, fields
from time import perf_counter
from typing import ClassVar

from reactors_czlab.core.data import (
    MAX_OUTPUT,
    ControlConfig,
    ControlMethod,
)
from reactors_czlab.server_info import VERBOSE

_logger = logging.getLogger("server.control")


def _as_float(name: str, value: object) -> float:
    """Validate that a config value is a real number and widen it to float.

    ``inf`` and ``nan`` are not real numbers either, and they do not
    fail loudly further down - every comparison against a ``nan`` is
    false, so it disables the check it appears in instead of tripping
    it. A ``time_on`` of ``nan`` leaves ``elapsed > self._interval``
    false forever, so ``_TimerControl`` never leaves the ON phase it
    starts in and the pump it drives never turns off. All of these
    arrive as an OPC ``Float`` an operator types into a generic client.

    Raises
    ------
    TypeError
        If ``value`` is not a number, or is not finite. The type is
        deliberate rather than ``ValueError``:
        ``Actuator.set_control_config()`` already catches ``TypeError``
        from ``ControlFactory``, logs the offending config and keeps the
        controller that is running, which is exactly the handling a
        rejected config needs.

    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        error_message = (
            f"{name} must be a number, got {type(value).__name__}: {value!r}"
        )
        raise TypeError(error_message)
    number = float(value)
    if not math.isfinite(number):
        error_message = f"{name} must be a finite number, got {number}"
        raise TypeError(error_message)
    return number


@dataclass(kw_only=True)
class _Control(ABC):
    """Base class for the control strategies.

    ``value`` is the *current* output. Subclasses whose output is derived
    from the sensor reading exclude it from equality; _ManualControl, where
    the output is the configuration, puts it back in.
    """

    method: ClassVar[ControlMethod]

    min_val: float = 0.0
    max_val: float = MAX_OUTPUT
    value: float = field(default=0.0, compare=False)

    def __post_init__(self) -> None:
        """Validate the numeric fields shared by every controller."""
        self.min_val = _as_float("min_val", self.min_val)
        self.max_val = _as_float("max_val", self.max_val)
        self.value = _as_float("value", self.value)

    def clamp(self, value: float) -> float:
        """Constrain a value to the output range of the actuator."""
        return max(self.min_val, min(value, self.max_val))

    def refresh_derived_limits(self) -> None:
        """Re-derive any state a subclass computed from the clamp range.

        A no-op here. A caller that mutates ``min_val``/``max_val`` in
        place - e.g. ``Actuator.refresh_controller_limits()`` after a
        pump recalibration - must call this afterwards so a controller
        that derived extra state from the old range (currently only
        ``_PidControl``'s anti-windup band) picks up the new one too.
        """

    def adopt_config(self, other: _Control) -> None:
        """Copy ``other``'s configuration onto this live controller.

        Every field that takes part in equality (``compare=True``) is a
        configuration field; every ``compare=False`` field is runtime
        state. Copying only the former lets an OPC write change the
        setpoint, gains, bounds or ``backwards`` flag of a *running*
        controller without resetting its integral, its timer phase or
        its current output - the state that a full rebuild would zero.
        ``Actuator.set_control_config()`` uses this whenever the control
        method is unchanged; see ``refresh_controller_limits()`` for the
        same in-place-mutation idea applied to the clamp range alone.

        ``other`` must be the same class as ``self`` (same method), which
        is what the caller guarantees. ``_ManualControl`` re-declares
        ``value`` as ``compare=True``, so its output - which *is* its
        configuration - is copied here, unlike every other class.
        """
        for spec in fields(self):
            if spec.compare:
                setattr(self, spec.name, getattr(other, spec.name))

    @abstractmethod
    def get_value(self, sens_value: float) -> float:
        """Calculate the actuator output value."""


@dataclass(kw_only=True)
class _ManualControl(_Control):
    """Set the output value directly from user input."""

    method: ClassVar[ControlMethod] = ControlMethod.manual

    # The output *is* the configuration here, so it is compared.
    value: float = 0.0

    def __repr__(self) -> str:
        """Print the configured output."""
        return f"_ManualControl({self.value!r})"

    def get_value(self, sens_value: float) -> float:
        """Return the value set by the user, ignoring the sensor."""
        return self.value


@dataclass(kw_only=True)
class _TimerControl(_Control):
    """Toggle the output between ``value_on`` and 0 on a fixed schedule."""

    method: ClassVar[ControlMethod] = ControlMethod.timer

    time_on: float = 0.0
    time_off: float = 0.0
    value_on: float = 0.0

    _is_on: bool = field(default=True, init=False, repr=False, compare=False)
    _interval: float = field(
        default=0.0, init=False, repr=False, compare=False
    )
    _last_time: float = field(
        default_factory=perf_counter,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        """Validate the timings and start the first ON phase."""
        super().__post_init__()
        self.time_on = _as_float("time_on", self.time_on)
        self.time_off = _as_float("time_off", self.time_off)
        self.value_on = _as_float("value_on", self.value_on)
        # Start in the ON phase so the first full period is time_on, not
        # 2 * time_on as it was when _is_on started out False.
        self.value = self.value_on
        self._is_on = True
        self._interval = self.time_on

    def __repr__(self) -> str:
        """Print the configured schedule."""
        return (
            f"_TimerControl(on: {self.time_on!r}s, "
            f"off: {self.time_off!r}s, {self.value_on!r})"
        )

    def get_value(self, sens_value: float) -> float:
        """Flip the output when the current phase has elapsed."""
        this_time = perf_counter()
        elapsed_time = this_time - self._last_time
        if elapsed_time > self._interval:
            self._last_time = this_time
            self._is_on = not self._is_on
            if self._is_on:
                self._interval = self.time_on
                self.value = self.value_on
            else:
                self._interval = self.time_off
                self.value = 0.0
            _logger.debug(
                "Timer output %s after %.3fs",
                self.value,
                elapsed_time,
            )
        return self.value


@dataclass(kw_only=True)
class _OnBoundariesControl(_Control):
    """Switch the output when the reference sensor crosses a threshold.

    Parameters
    ----------
    lower_bound:
        After the reference sensor crosses this threshold the output is on
    upper_bound:
        After the reference sensor crosses this threshold the output is off
    value_on:
        The value used in the on state
    backwards:
        If True the output is reversed: crossing the lower bound turns the
        output off and crossing the upper bound turns it on

    """

    method: ClassVar[ControlMethod] = ControlMethod.on_boundaries

    lower_bound: float = 0.0
    upper_bound: float = 0.0
    value_on: float = 0.0
    backwards: bool = False

    def __post_init__(self) -> None:
        """Validate the thresholds and set the initial output."""
        super().__post_init__()
        self.lower_bound = _as_float("lower_bound", self.lower_bound)
        self.upper_bound = _as_float("upper_bound", self.upper_bound)
        self.value_on = _as_float("value_on", self.value_on)
        self.value = self.value_on if self.backwards else 0.0

    def __repr__(self) -> str:
        """Print the configured thresholds."""
        return (
            f"_OnBoundariesControl({self.lower_bound!r}, "
            f"{self.upper_bound!r}, {self.value_on!r})"
        )

    def get_value(self, sens_value: float) -> float:
        """Update the output from the reference sensor reading."""
        if sens_value < self.lower_bound:
            self.value = 0.0 if self.backwards else self.value_on
        elif sens_value > self.upper_bound:
            self.value = self.value_on if self.backwards else 0.0

        if VERBOSE:
            _logger.debug(
                "OnBoundaries lb: %s, ub: %s, var: %s, value: %s",
                self.lower_bound,
                self.upper_bound,
                sens_value,
                self.value,
            )
        return self.value


@dataclass(kw_only=True)
class _PidControl(_Control):
    """Drive the output towards ``setpoint`` with a PID algorithm.

    Parameters
    ----------
    backwards:
        If False (default) the output is positive while the reading is
        below ``setpoint`` and clamps to ``min_val`` (0 by default) once
        it rises above it. If True the sense is reversed - positive above
        the setpoint, clamped below it - by flipping the sign of the
        error and of the derivative term. Two actuators paired to the
        same sensor, one normal and one ``backwards``, drive opposing
        pumps (acid/base, heater/cooler) around a single setpoint.
    """

    method: ClassVar[ControlMethod] = ControlMethod.pid

    setpoint: float = 0.0
    kp: float = 100.0
    ki: float = 0.01
    kd: float = 0.0
    backwards: bool = False
    # Anti-windup band for the integral term. Defaults to the output range
    # but is a separate knob, because the two limits are unrelated.
    min_integral: float | None = None
    max_integral: float | None = None

    _integral_sum: float = field(
        default=0.0,
        init=False,
        repr=False,
        compare=False,
    )
    _last_measurement: float | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _last_time: float = field(
        default_factory=perf_counter,
        init=False,
        repr=False,
        compare=False,
    )
    # True when neither min_integral nor max_integral was configured
    # explicitly, so refresh_derived_limits() knows it may re-derive the
    # band from min_val/max_val without clobbering an operator's choice.
    # ControlFactory never passes either, so this is always True today,
    # but the sentinel (None) is consumed below and cannot be told apart
    # from an explicit value after __post_init__ runs - so the fact of
    # "was it defaulted" has to be recorded somewhere, and this is it.
    _integral_band_is_default: bool = field(
        default=True,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        """Validate the gains and default the anti-windup band."""
        super().__post_init__()
        self.setpoint = _as_float("setpoint", self.setpoint)
        self.kp = _as_float("kp", self.kp)
        self.ki = _as_float("ki", self.ki)
        self.kd = _as_float("kd", self.kd)
        self._integral_band_is_default = (
            self.min_integral is None and self.max_integral is None
        )
        if self.min_integral is None:
            self.min_integral = self.min_val
        if self.max_integral is None:
            self.max_integral = self.max_val
        self.min_integral = _as_float("min_integral", self.min_integral)
        self.max_integral = _as_float("max_integral", self.max_integral)
        self._cap_integral_band()

    def refresh_derived_limits(self) -> None:
        """Track the anti-windup band to ``min_val``/``max_val``.

        An automatic band tracks the whole output range. An explicit band
        keeps the operator's lower values but is still capped to the live
        output maximum: anti-windup state above what the actuator can dose
        is not useful or safe.

        The stored ``_integral_sum`` is reclamped immediately rather
        than left for the next ``get_value()`` step: a refit that
        lowers the achievable output (e.g. the pump now measures
        slower) would otherwise leave the sum pinned arbitrarily far
        above what the output can express until the next step catches
        up, and until then the controller sits saturated - exactly the
        sustained overdosing anti-windup exists to prevent.
        """
        if self._integral_band_is_default:
            self.min_integral = self.min_val
            self.max_integral = self.max_val
        else:
            self._cap_integral_band()
        self._integral_sum = max(
            self.min_integral,
            min(self._integral_sum, self.max_integral),
        )

    def _cap_integral_band(self) -> None:
        """Cap explicit anti-windup bounds to the achievable output."""
        self.min_integral = min(self.min_integral, self.max_val)
        self.max_integral = min(self.max_integral, self.max_val)
        if self.min_integral > self.max_integral:
            error_message = (
                f"min_integral {self.min_integral} is above max_integral "
                f"{self.max_integral}"
            )
            raise TypeError(error_message)

    def adopt_config(self, other: _Control) -> None:
        """Adopt config in place, keeping the integral and last reading.

        Two additions to the base copy. ``_integral_band_is_default`` is a
        ``compare=False`` flag recording whether the anti-windup band was
        set explicitly; it is configuration in spirit, so it must follow
        the band or an operator's explicit band would be silently
        re-derived on the next recalibration. And ``_integral_sum`` is
        reclamped into the freshly adopted band at once - for the same
        reason ``refresh_derived_limits`` reclamps it, a band that just
        narrowed must not leave the sum pinned above the new maximum.
        """
        super().adopt_config(other)
        self._integral_band_is_default = other._integral_band_is_default
        self._integral_sum = max(
            self.min_integral,
            min(self._integral_sum, self.max_integral),
        )

    def __repr__(self) -> str:
        """Print the setpoint and gains."""
        return (
            f"_PidControl(SP: {self.setpoint!r}, "
            f"kp: {self.kp!r}, ki: {self.ki!r}, kd: {self.kd!r})"
        )

    def set_gains(self, gains: list[float]) -> None:
        """Set [kp, ki, kd] in one call."""
        self.kp = _as_float("kp", gains[0])
        self.ki = _as_float("ki", gains[1])
        self.kd = _as_float("kd", gains[2])

    def get_value(self, sens_value: float) -> float:
        """Run one PID step using the time actually elapsed since the last."""
        this_time = perf_counter()
        dt = this_time - self._last_time
        if dt <= 0.0:
            # Two calls inside the clock resolution: nothing to integrate.
            return self.value
        self._last_time = this_time

        # backwards flips the whole loop's sign: the error, and with it
        # every term derived from it. Above the setpoint the error is now
        # positive (demanding output), below it negative (clamped away).
        direction = -1.0 if self.backwards else 1.0
        error = direction * (self.setpoint - sens_value)

        p_term = self.kp * error

        i_term = self.ki * error * dt
        self._integral_sum = max(
            self.min_integral,
            min(self._integral_sum + i_term, self.max_integral),
        )

        # Derivative on measurement rather than on error, so a setpoint
        # change does not produce a derivative kick. Its sign follows
        # ``direction`` too, so it still opposes the controlled motion.
        if self._last_measurement is None:
            d_term = 0.0
        else:
            d_term = (
                -direction
                * self.kd
                * (sens_value - self._last_measurement)
                / dt
            )
        self._last_measurement = sens_value

        output = p_term + self._integral_sum + d_term
        self.value = self.clamp(output)

        if VERBOSE:
            _logger.debug(
                "PID dt: %.3f, var: %s, error: %s", dt, sens_value, error
            )
            _logger.debug(
                "PID p: %s, i: %s, d: %s, sum: %s, value: %s",
                p_term,
                i_term,
                d_term,
                self._integral_sum,
                self.value,
            )
        return self.value


class ControlFactory:
    """Factory of the different control classes."""

    def create_control(
        self,
        config: ControlConfig,
        min_val: float = 0.0,
        max_val: float = MAX_OUTPUT,
    ) -> _Control:
        """Create a control class based on the control config.

        Parameters
        ----------
        config:
            A dataclass with the parameters of the new configuration
        min_val, max_val:
            Range the controller may demand, in the unit the config asks
            for. The defaults are the raw PLC output range, which is what
            duty-mode configs want.

        Raises
        ------
        TypeError
            If the method is unknown or a parameter is not a number.

        """
        limits = {"min_val": min_val, "max_val": max_val}
        match config.method:
            case ControlMethod.manual:
                return _ManualControl(value=config.value, **limits)

            case ControlMethod.timer:
                return _TimerControl(
                    time_on=config.time_on,
                    time_off=config.time_off,
                    value_on=config.value,
                    **limits,
                )

            case ControlMethod.on_boundaries:
                return _OnBoundariesControl(
                    lower_bound=config.lb,
                    upper_bound=config.ub,
                    value_on=config.value,
                    backwards=config.backwards,
                    **limits,
                )

            case ControlMethod.pid:
                # A None band tells _PidControl to auto-derive it from the
                # output range (the default). The explicit band from the
                # config is installed only when the operator opts in.
                min_integral = (
                    None
                    if config.auto_integral_band
                    else config.min_integral
                )
                max_integral = (
                    None
                    if config.auto_integral_band
                    else config.max_integral
                )
                return _PidControl(
                    setpoint=config.setpoint,
                    kp=config.kp,
                    ki=config.ki,
                    kd=config.kd,
                    backwards=config.backwards,
                    min_integral=min_integral,
                    max_integral=max_integral,
                    **limits,
                )

            case _:
                error_message = f"Unknown control method: {config.method!r}"
                raise TypeError(error_message)
