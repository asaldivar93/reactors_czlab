"""Control methods."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from reactors_czlab.core.data import ControlConfig, ControlMethod
from reactors_czlab.core.utils import Timer
from reactors_czlab.server_info import VERBOSE

if TYPE_CHECKING:
    from reactors_czlab.core.sensor import Sensor

_logger = logging.getLogger("server.control")


class ControlFactory:
    """Factory of the different control classes."""

    def create_control(self, config: ControlConfig) -> _Control:
        """Create a control class based on the control_config.

        Inputs:
        -------
        config: ControlConfig
            A dataclass with the parameters of the new configuration

        """
        # Pattern matching to the new control config
        match config:
            case ControlConfig("manual") as c:
                return _ManualControl(c.value)

            case ControlConfig("timer") as c:
                return _TimerControl(c.time_on, c.time_off, c.value)

            case ControlConfig("on_boundaries") as c:
                return _OnBoundariesControl(c.lb, c.ub, c.value)

            case ControlConfig("pid") as c:
                return _PidControl(c.setpoint)

            case _:
                raise TypeError


class _Control(ABC):
    """Metaclass for control methods."""

    @property
    def value(self) -> float:
        """Get the value of the actuator."""
        return self._value

    @value.setter
    def value(self, value: float) -> None:
        """Set the value of the actuator."""
        error_message = "Expected Float type in ControlConfig"
        if not isinstance(value, float | int):
            raise TypeError(error_message)
        self._value = value

    @property
    def max_val(self) -> float:
        return self._max_val

    @max_val.setter
    def max_val(self, max_val: float) -> None:
        if not isinstance(max_val, float | int):
            raise TypeError
        self._max_val = max_val

    @property
    def min_val(self) -> float:
        return self._min_val

    @min_val.setter
    def min_val(self, min_val: float) -> None:
        if not isinstance(min_val, float | int):
            raise TypeError
        self._min_val = min_val

    def set_limits(self, limits: list[float]) -> None:
        self.min_val = limits[0]
        self.max_val = limits[1]

    @abstractmethod
    def get_value(self, sensor: Sensor | None = None) -> float:
        """Calculate the actuator output value."""


class _ManualControl(_Control):
    """ManualControl class sets the output value based on user input."""

    def __init__(self, value: float, limits: list[float] | None = None) -> None:
        self.method = ControlMethod.manual
        self.value = value
        if limits is None:
            self.set_limits([0, 4095])
        else:
            self.set_limits(limits)

    def __repr__(self) -> str:
        return f"_ManualControl({self.value!r})"

    def __eq__(self, other: object) -> bool:
        this = [self.method, self.value]
        return this == other

    def get_value(self, sensor: Sensor | None = None) -> float:
        return self.value


class _TimerControl(_Control):
    """TimerControl class sets the output based on time intervals."""

    def __init__(
        self,
        time_on: float,
        time_off: float,
        value_on: float,
        limits: list[float] | None = None,
    ) -> None:
        self.method = ControlMethod.timer
        self.time_on = time_on
        self.time_off = time_off
        self.value_on = value_on
        self.value = value_on

        if limits is None:
            self.set_limits([0, 4095])
        else:
            self.set_limits(limits)

        # create a timer instance
        self.timer = Timer(time_on)
        self.timer.add_suscriber(self)
        self._is_on = False
        self._sampling_event = True

    def __repr__(self) -> str:
        return f"_TimerControl(on: {self.time_on!r}s, off: {self.time_off!r}s, {self.value!r})"

    def __eq__(self, other: object) -> bool:
        this = [self.method, self.time_on, self.time_off, self.value]
        return this == other

    @property
    def time_on(self) -> float:
        return self._time_on

    @time_on.setter
    def time_on(self, time_on: float) -> None:
        if not isinstance(time_on, float | int):
            raise TypeError
        self._time_on = time_on

    @property
    def time_off(self) -> float:
        return self._time_off

    @time_off.setter
    def time_off(self, time_off: float) -> None:
        if not isinstance(time_off, float | int):
            raise TypeError
        self._time_off = time_off

    def get_value(self, sensor: Sensor | None = None) -> float:
        self.timer.callback()
        if self._sampling_event:
            self._sampling_event = False
            if self._is_on:
                # Set the new timer
                self.timer.interval = self.time_off
                # Invert the state
                self.value = 0
                self._is_on = False
            else:
                # Set the new timer
                self.timer.interval = self.time_on
                # Invert the state
                self._is_on = True
                self.value = self.value_on
            _logger.debug(f"value:{self.value}, is_on:{self._is_on}")
        return self.value


class _OnBoundariesControl(_Control):
    """OnBoundariesControl class.

    Sets the output only when the reference variable crosses
    upper or lower thresholds.
    """

    def __init__(
        self,
        lower_bound: float,
        upper_bound: float,
        value: float,
        limits: list[float] | None = None,
        backwards: bool = False,
    ) -> None:
        """Initialize OnBoundaries controller.

        Inputs:
        -------
        lower_bound: float | int
            After the reference sensor crosses this threshold the ouput is on
        upper_bound: float | int
            After the reference sensor crosses this threshold the ouput is off
        value: float | int
            The value used in the on state
        backwards: bool
            if backwards=True the output is reversed, if the reference sensor
            crosses the lower_bound the ouput is off and viceversa
        """
        self.method = ControlMethod.on_boundaries
        self.lower_bound = lower_bound
        self.upper_bound = upper_bound
        self.value_on = value
        self.backwards = backwards
        if backwards:
            self.value = value
        else:
            self.value = 0
        if limits is None:
            self.set_limits([0, 4095])
        else:
            self.set_limits(limits)

    def __repr__(self) -> str:
        return f"_OnBoundariesControl({self.lower_bound, self.upper_bound, self.value_on})"

    def __eq__(self, other: object) -> bool:
        this = [self.method, self.lower_bound, self.upper_bound, self.value_on]
        return this == other

    @property
    def lower_bound(self) -> float:
        return self._lower_bound

    @lower_bound.setter
    def lower_bound(self, lower_bound: float) -> None:
        if not isinstance(lower_bound, float | int):
            raise TypeError
        self._lower_bound = lower_bound

    @property
    def upper_bound(self) -> float:
        return self._upper_bound

    @upper_bound.setter
    def upper_bound(self, upper_bound: float) -> None:
        if not isinstance(upper_bound, float | int):
            raise TypeError
        self._upper_bound = upper_bound

    def get_value(self, sensor: Sensor | None = None) -> float:
        if sensor is None:
            raise AttributeError

        variable = sensor.channels[0].value
        if variable < self.lower_bound:
            if self.backwards:
                self.value = 0
            else:
                self.value = self.value_on
        elif variable > self.upper_bound:
            if self.backwards:
                self.value = self.value_on
            else:
                self.value = 0

        if VERBOSE:
            _logger.debug(f"lb: {self.lower_bound}, ub: {self.upper_bound}")
            _logger.debug(f"var: {variable}, value: {self.value}")

        return self.value


class _PidControl(_Control):
    """PidControl class uses the PID algorithm to calculate the output."""

    def __init__(
        self,
        setpoint: float,
        gains: list[float] | None = None,
        limits: list[float] | None = None,
    ) -> None:
        self.method = ControlMethod.pid
        self.setpoint = setpoint
        if gains is None:
            self.set_gains([100, 0.01, 0])
        else:
            self.set_gains(gains)

        if limits is None:
            self.set_limits([0, 4095])
        else:
            self.set_limits(limits)

        self.value = 0
        self._last_error = 0
        self._integral_sum = 0

    def __repr__(self) -> str:
        return f"_PidControl(setpoint: {self.setpoint!r})"

    def __eq__(self, other: object) -> bool:
        this = [self.setpoint]
        return this == other

    @property
    def setpoint(self) -> float:
        return self._setpoint

    @setpoint.setter
    def setpoint(self, setpoint: float) -> None:
        if not isinstance(setpoint, float | int):
            raise TypeError
        self._setpoint = setpoint

    @property
    def kp(self) -> float:
        return self._kp

    @kp.setter
    def kp(self, kp: float) -> None:
        if not isinstance(kp, float | int):
            raise TypeError
        self._kp = kp

    @property
    def ki(self) -> float:
        return self._ki

    @ki.setter
    def ki(self, ki: float) -> None:
        if not isinstance(ki, float | int):
            raise TypeError
        self._ki = ki

    @property
    def kd(self) -> float:
        return self._kd

    @kd.setter
    def kd(self, kd: float) -> None:
        if not isinstance(kd, float | int):
            raise TypeError
        self._kd = kd

    def set_gains(self, gains: list[float]) -> None:
        self.kp = gains[0]
        self.ki = gains[1]
        self.kd = gains[2]

    def get_value(self, sensor: Sensor | None = None) -> float:
        if sensor is None:
            raise AttributeError
        if sensor.timer is None:
            raise AttributeError

        self._sampling_event = False
        variable = sensor.channels[0].value
        dt = sensor.timer.elapsed_time

        # Get error
        error = self.setpoint - variable
        d_error = error - self._last_error
        self._last_error = error

        # Get PID terms
        p_term = self.kp * error
        i_term = self.ki * error * dt
        d_term = self.kd * d_error / dt
        self._integral_sum += i_term

        # Anti-windup
        self._integral_sum = max(
            self.min_val,
            min(self._integral_sum, self.max_val),
        )
        # Sum all the PID terms
        output = p_term + self._integral_sum + d_term
        # Constraint the output to the allowable range
        self.value = max(self.min_val, min(output, self.max_val))
        if VERBOSE:
            _logger.debug(f"elapsed_time: {dt}, var: {variable}")
            _logger.debug(
                f"p_term: {p_term}, i_term: {i_term}, d_term: {d_term}",
            )
            _logger.debug(
                f"error: {error}, _integral_sum: {self._integral_sum}, value: {self.value}",
            )

        return self.value
