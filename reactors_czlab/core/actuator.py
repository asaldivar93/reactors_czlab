"""Define the actuator class."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from reactors_czlab.core.dictlist import DictList
from reactors_czlab.core.sensor import Sensor

# We could find a better way to format and pass the debug statements
_logger = logging.getLogger("server.actuator")

valid_control_methods = ["manual", "timer", "on_boundaries", "pid"]


class Actuator:
    """Actuator class."""

    def __init__(self, identifier: str, address: str | int) -> None:
        """Instance the actuator class.

        Inputs
        -------
        -identifier: a unique identifier for the actuator
        -address: the Modbus address or the gpio pin
        """
        self.id = identifier
        self.address = address
        self.controller = _ManualControl(0)

    @property
    def sensors(self) -> DictList:
        """Return a DictList of Sensors."""
        return self._sensors

    @sensors.setter
    def sensors(self, sensors: DictList) -> None:
        """Set available sensors."""
        if not isinstance(sensors, DictList):
            raise TypeError

        self._sensors = sensors

    @property
    def reference_sensor(self) -> Sensor:
        """Sensor instance used as a reference."""
        return self._reference_sensor

    @reference_sensor.setter
    def reference_sensor(self, sensor: Sensor) -> None:
        """Set reference sensor."""
        if not isinstance(sensor, Sensor):
            raise TypeError

        self._reference_sensor = sensor

    def set_reference_sensor(self, sensor: str | Sensor) -> None:
        """Set reference sensor."""
        if isinstance(sensor, Sensor):
            self.reference_sensor = sensor
        else:
            reference = self.sensors.get_by_id(sensor)
            self.reference_sensor = reference

    def write_output(self) -> None:
        """Write the actuator values."""
        try:
            # Pattern matching against the controller class, each class
            # has its own methods to calculate the output value
            match self.controller:
                case _ManualControl() as control:
                    self._write(control.value)

                case _TimerControl() as control:
                    self._write(control.get_value())

                case _OnBoundariesControl() as control:
                    variable = self.reference_sensor.value
                    self._write(control.get_value(variable))
                case _PidControl() as control:
                    variable = self.reference_sensor.value
                    self._write(control.get_value(variable))
        except AttributeError:
            # Catch an exception when the user hasn't set a reference sensor
            # before setting on_boundaries or pid control classes
            _logger.exception(f"reference sensor in {self.id} not set")
            _logger.warning(f"Setting output in {self.id} = 0")
            self._write(0)

    def _write(self, value: int) -> int:
        return value

    def set_control_config(self, control_config: dict) -> None:
        """Change the current configuration of the actuator outputs.

        Inputs:
        -------
        -control_config: dict
            A dictionary with the parameters of the new configuration

        Example:
        -------
        - {"method": "manual", "value": 255}
        - {"method": "timer", "value": 255, "time_on": 5, "time_off": 10}
        - {"method": "on_boundaries", "value": 255,
           "lower_bound": 1.52, "upper_bound": 5.45}
        - {"method": "pid", "setpoint": 35}

        """
        current_method = self.controller
        try:
            # Pattern matching to the new control config and sets the new config
            # only if it is different from the old config
            match control_config:
                case {"method": "manual", "value": value}:
                    new_method = _ManualControl(value)
                    if new_method != current_method:
                        self.controller = new_method
                        _logger.info(f"Control config update - {self.id}:{new_method}")

                case {
                    "method": "timer",
                    "value": value,
                    "time_on": time_on,
                    "time_off": time_off,
                }:
                    new_method = _TimerControl(time_on, time_off, value)
                    if new_method != current_method:
                        self.controller = new_method
                        _logger.info(f"Control config update - {self.id}:{new_method}")

                case {"method": "pid", "setpoint": setpoint}:
                    new_method = _PidControl(setpoint)
                    if new_method != current_method:
                        self.controller = new_method
                        _logger.info(f"Control config update - {self.id}:{new_method}")

                case {
                    "method": "on_boundaries",
                    "lower_bound": lb,
                    "upper_bound": ub,
                    "value": value,
                }:
                    new_method = _OnBoundariesControl(lb, ub, value)
                    if new_method != current_method:
                        self.controller = new_method
                        _logger.info(f"Control config update - {self.id}:{new_method}")

                case _:
                    _logger.error(
                        f"Case not found for control_config - {self.id}:{new_method}"
                    )

        except TypeError:
            # Each control class checks that the values passed are of the correct
            # type, we want to avoid passing a string where we expect a float or int
            _logger.exception(f"Wrong attributes passed in: {control_config}")


class _ManualControl:
    """ManualControl class sets the output value based on user input."""

    def __init__(self, value: int) -> None:
        self.method = valid_control_methods[0]
        self.value = value

    def __repr__(self) -> str:
        return f"_ManualControl({self.value!r})"

    @property
    def value(self) -> int:
        return self._value

    @value.setter
    def value(self, value: int) -> None:
        # Most controllers use pwm which can only take integer values
        if not isinstance(value, int):
            raise TypeError
        self._value = value

    def __eq__(self, other: None) -> bool:
        this = [self.method, self.value]
        return this == other


class _TimerControl:
    """TimerControl class sets the output based on time intervals."""

    def __init__(self, time_on: float, time_off: float, value: int) -> None:
        self.method = valid_control_methods[1]
        self.time_on = time_on
        self.time_off = time_off
        self.value = value
        self.value_on = value

        self._current_timer = time_on
        self._is_on = True
        self._last_time = datetime.now(tz=timezone.utc)

    def __repr__(self) -> str:
        return f"_TimerControl(on: {self.time_on!r}s, off: {self.time_off!r}s, {self.value!r})"

    @property
    def value(self) -> int:
        return self._value

    @value.setter
    def value(self, value: int) -> None:
        if not isinstance(value, int):
            raise TypeError
        self._value = value

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

    def __eq__(self, other: None) -> bool:
        this = [self.method, self.time_on, self.time_off, self.value]
        return this == other

    def get_value(self) -> int:
        # Change the datetime library with the time library
        this_time = datetime.now(tz=timezone.utc)
        self._delta_time = this_time - self._last_time
        dt = self._delta_time.total_seconds()
        if dt > self._current_timer:
            _logger.debug(f"Delta Time: {dt}")
            _logger.debug(f"Current Timer: {self._current_timer}")
            _logger.debug(f"Current is_on: {self._is_on}")
            self._last_time = this_time
            # We could simply invert the values of _is_on
            if self._is_on:
                self._current_timer = self.time_off
                self._is_on = False
            else:
                self._current_timer = self.time_on
                self._is_on = True
            _logger.debug(f"New Timer: {self._current_timer}")
            _logger.debug(f"New is_on: {self._is_on}")

        # Maybe we don't need this assignation every function evaluation?
        if self._is_on:
            self.value = self.value_on
        else:
            self.value = 0

        return self.value


class _OnBoundariesControl:
    """OnBoundariesControl class.

    Sets the output only when the reference variable crosses upper or lower thresholds.
    """

    def __init__(
        self, lower_bound: int, upper_bound: int, value: int, backwards: bool = False
    ) -> None:
        """Initialize the controller.

        Inputs:
        -------
        -lower_bound: after the reference sensor crosses this threshold the ouput is on
        -upper_bound: after the reference sensor crosses this threshold the ouput is off
        -value: the value used in the on state
        -backwards: if backwards=True the output is reversed, if the reference sensor
        crosses the lower_bound the ouput is off and viceversa
        """
        self.method = valid_control_methods[2]
        self.lower_bound = lower_bound
        self.upper_bound = upper_bound
        self.value_on = value
        self.backwards = backwards
        if backwards:
            self.value = value
        else:
            self.value = 0

    def __repr__(self) -> str:
        return (
            f"_OnBoundariesControl({self.lower_bound, self.upper_bound, self.value_on})"
        )

    @property
    def value(self) -> int:
        return self._value

    @value.setter
    def value(self, value: int) -> None:
        if not isinstance(value, int):
            raise TypeError
        self._value = value

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

    def __eq__(self, other: None) -> bool:
        this = [self.method, self.lower_bound, self.upper_bound, self.value]
        return this == other

    def get_value(self, variable: float) -> int:
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
        print(f"lb: {self.lower_bound}, ub: {self.upper_bound}, var: {variable}")
        print(f"value: {self.value}, reversed: {self.backwards}")
        return self.value


class _PidControl:
    """PidControl class uses the PID algorithm to calculate the output."""

    def __init__(
        self,
        setpoint: float,
        gains: set[float] = (100, 0.01, 0),
        limits: set[int] = (0, 255),
    ) -> None:
        self.method = valid_control_methods[3]
        self.setpoint = setpoint
        self.set_gains(gains)
        self.set_limits(limits)

        self._last_time = datetime.now(tz=timezone.utc)
        self._last_error = 0
        self._integral_sum = 0

    def __repr__(self) -> str:
        return f"_PidControl(setpoint: {self.setpoint!r})"

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
    def kp(self, kp: float) -> float:
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

    def set_gains(self, gains: set[float]) -> None:
        self.kp = gains[0]
        self.ki = gains[1]
        self.kd = gains[2]

    @property
    def max_val(self) -> int:
        return self._max_val

    @max_val.setter
    def max_val(self, max_val: int) -> None:
        if not isinstance(max_val, int):
            raise TypeError
        self._max_val = max_val

    @property
    def min_val(self) -> int:
        return self._min_val

    @min_val.setter
    def min_val(self, min_val: int) -> None:
        if not isinstance(min_val, int):
            raise TypeError
        self._min_val = min_val

    def set_limits(self, limits: set[float]) -> None:
        self.min_val = limits[0]
        self.max_val = limits[1]

    def __eq__(self, other: None) -> bool:
        this = [self.setpoint]
        return this == other

    def get_value(self, variable: float) -> int:
        # Get the elapsed time since last evaluation
        now = datetime.now(tz=timezone.utc)
        time_delta = now - self._last_time
        dt = time_delta.total_seconds()
        _logger.debug(f"kp: {self.kp}, ki: {self.ki}, kd: {self.ki}")
        _logger.debug(f"lb: {self.min_val}, ub: {self.max_val}")
        # Get error
        error = self.setpoint - variable
        d_error = error - self._last_error

        # Get PID terms
        p_term = self.kp * error
        i_term = self.ki * error * dt
        d_term = self.kd * d_error / dt

        self._integral_sum += i_term
        # Anti-windup
        self._integral_sum = max(self.min_val, min(self._integral_sum, self.max_val))
        # Sum all the PID terms
        output = int(p_term + self._integral_sum + d_term)
        # Constraint the output to the allowable range
        self.value = max(self.min_val, min(output, self.max_val))
        _logger.debug(
            f"error: {error}, p_term: {p_term}, i_term: {i_term}, d_term: {d_term}, _integral_sum: {self._integral_sum}, value: {self.value}"
        )

        self._last_error = error
        self._last_time = now

        return self.value
