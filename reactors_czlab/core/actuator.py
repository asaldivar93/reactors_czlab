"""Define the actuator class."""

from __future__ import annotations

import logging
import platform
from abc import ABCMeta, abstractmethod
from datetime import datetime, timezone

from reactors_czlab.core.dictlist import DictList
from reactors_czlab.core.sensor import Sensor

if platform.machine().startswith("arm"):
    from librpiplc import rpiplc as rp

_logger = logging.getLogger("server.actuator")

IN_RASPBERRYPI = platform.machine().startswith("arm")
CONTROL_METHODS = ["manual", "timer", "on_boundaries", "pid"]

# Missing a Modbus Actuator, to do after Modbus FIFO queue
# Missing an Actuator factory?


class BaseActuator:
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

    @property
    def controller(self) -> _Control:
        """Get controller."""
        return self._controller

    @controller.setter
    def controller(self, controller: _Control) -> None:
        if not isinstance(controller, _Control):
            raise TypeError
        self._controller = controller

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
            variable = self.reference_sensor.channels[0]["value"]
            self._write(self.controller.get_value(variable))
        except AttributeError:
            # Catch an exception when the user hasn't set a reference sensor
            # before setting _OnBoundaries or _PidControl classes
            _logger.exception(f"reference sensor in {self.id} not set")
            _logger.warning(f"Setting output in {self.id} = 0")
            self._write(0)

    def set_control_config(self, control_config: dict) -> None:
        """Change the current configuration of the actuator outputs.

        Inputs:
        -------
        control_config: dict
            A dictionary with the parameters of the new configuration

        Example:
        -------
        - {"method": "manual", "value": 255}
        - {"method": "timer", "value": 255, "time_on": 5, "time_off": 10}
        - {"method": "on_boundaries", "value": 255,
           "lower_bound": 1.52, "upper_bound": 5.45}
        - {"method": "pid", "setpoint": 35}

        """
        current_controller = self.controller
        try:
            # Instance a new controller class
            new_controller = _ControlFactory().create_control(control_config)
            # sets the new config only if it is different from the old config
            if current_controller != new_controller:
                self.controller = new_controller
            _logger.info(f"Control config update - {self.id}:{new_method}")

        except TypeError:
            # Each control class checks that the values
            # passed are of the correct type
            _logger.exception(f"Wrong attributes in {self.id}:{control_config}")

    @abstractmethod
    def _write(self, value: float) -> float:
        """Write actuator method."""


class AnalogActuator(BaseActuator):
    """Class which writes to the RaspberryPi pins."""

    def __init__(self, identifier: str, address: str | int) -> None:
        super().__init__(identifier, address)
        if IN_RASPBERRYPI:
            rp.pin_mode(address, rp.OUTPUT)
            rp.analog_write_set_frequency(address, 24)

    def _write(self, value: float) -> float:
        if IN_RASPBERRYPI:
            rp.analog_write(self.address, int(value))
        return value


class _ControlFactory:
    """Factory of the different control classes."""

    # Pattern matching to the new control config
    def create_control(self, control_config: dict) -> _Control:
        match control_config:
            case {"method": "manual", "value": value}:
                return _ManualControl(value)

            case {
                "method": "timer",
                "value": value,
                "time_on": time_on,
                "time_off": time_off,
            }:
                return _TimerControl(time_on, time_off, value)

            case {
                "method": "on_boundaries",
                "lower_bound": lb,
                "upper_bound": ub,
                "value": value,
            }:
                return _OnBoundariesControl(lb, ub, value)

            case {"method": "pid", "setpoint": setpoint}:
                return _PidControl(setpoint)

            case _:
                raise TypeError


class _Control(metaclass=ABCMeta):
    """Metaclass for control methods."""

    @property
    def value(self) -> float:
        return self._value

    @value.setter
    def value(self, value: float) -> None:
        if not isinstance(value, float | int):
            raise TypeError
        self._value = value

    @abstractmethod
    def get_value(self, variable: float | None = None) -> float:
        """Calculate the actuator output value."""


class _ManualControl(_Control):
    """ManualControl class sets the output value based on user input."""

    def __init__(self, value: int) -> None:
        self.method = CONTROL_METHODS[0]
        self.value = value

    def __repr__(self) -> str:
        return f"_ManualControl({self.value!r})"

    def __eq__(self, other: object) -> bool:
        this = [self.method, self.value]
        return this == other

    def get_value(self, variable: float | None = None) -> float:
        return self.value


class _TimerControl(_Control):
    """TimerControl class sets the output based on time intervals."""

    def __init__(self, time_on: float, time_off: float, value: int) -> None:
        self.method = CONTROL_METHODS[1]
        self.time_on = time_on
        self.time_off = time_off
        self.value = value
        self.value_on = value

        self._current_timer = time_on
        self._is_on = True
        self._last_time = datetime.now(tz=timezone.utc)

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

    def get_value(self, variable: float | None = None) -> float:
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


class _OnBoundariesControl(_Control):
    """OnBoundariesControl class.

    Sets the output only when the reference variable crosses upper or lower thresholds.
    """

    def __init__(
        self,
        lower_bound: float,
        upper_bound: float,
        value: float,
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
        self.method = CONTROL_METHODS[2]
        self.lower_bound = lower_bound
        self.upper_bound = upper_bound
        self.value_on = value
        self.backwards = backwards
        if backwards:
            self.value = value
        else:
            self.value = 0

    def __repr__(self) -> str:
        return f"_OnBoundariesControl({self.lower_bound, self.upper_bound, self.value_on})"

    def __eq__(self, other: object) -> bool:
        this = [self.method, self.lower_bound, self.upper_bound, self.value]
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

    def get_value(self, variable: float | None = None) -> float:
        if variable is None:
            raise AttributeError

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
        _logger.debug(f"lb: {self.lower_bound}, ub: {self.upper_bound}")
        _logger.debug(f"var: {variable}, value: {self.value}")

        return self.value


class _PidControl(_Control):
    """PidControl class uses the PID algorithm to calculate the output."""

    def __init__(
        self,
        setpoint: float,
        gains: tuple[float, float, float] = (100, 0.01, 0),
        limits: tuple[float, float] = (0, 255),
    ) -> None:
        self.method = CONTROL_METHODS[3]
        self.setpoint = setpoint
        self.set_gains(gains)
        self.set_limits(limits)

        self._last_time = datetime.now(tz=timezone.utc)
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

    def set_gains(self, gains: tuple[float, float, float]) -> None:
        self.kp = gains[0]
        self.ki = gains[1]
        self.kd = gains[2]

    @property
    def max_val(self) -> float:
        return self._max_val

    @max_val.setter
    def max_val(self, max_val: float) -> None:
        if not isinstance(max_val, float):
            raise TypeError
        self._max_val = max_val

    @property
    def min_val(self) -> float:
        return self._min_val

    @min_val.setter
    def min_val(self, min_val: float) -> None:
        if not isinstance(min_val, float):
            raise TypeError
        self._min_val = min_val

    def set_limits(self, limits: tuple[float, float]) -> None:
        self.min_val = limits[0]
        self.max_val = limits[1]

    def get_value(self, variable: float | None = None) -> float:
        if variable is None:
            raise AttributeError

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
        self._integral_sum = max(
            self.min_val, min(self._integral_sum, self.max_val)
        )
        # Sum all the PID terms
        output = p_term + self._integral_sum + d_term
        # Constraint the output to the allowable range
        self.value = max(self.min_val, min(output, self.max_val))
        _logger.debug(
            f"error: {error}, p_term: {p_term}, i_term: {i_term}, d_term: {d_term}, _integral_sum: {self._integral_sum}, value: {self.value}"
        )

        self._last_error = error
        self._last_time = now

        return self.value
