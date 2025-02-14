"""Define the actuator class."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reactors_czlab import Sensor

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())
logging.basicConfig(
    filename="record.log",
    encoding="utf-8",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)

valid_control_modes = ["manual", "timer", "on_boundaries", "pid"]


class Actuator:
    """Actuator class."""

    def __init__(self, identifier: str, channel: str, control_dict: dict) -> None:
        """Instance the actuator class."""
        self.id = identifier
        self.channel = channel
        self.set_control_method(control_dict)

    def set_reference_sensor(self, ref_sensor: Sensor) -> None:
        """Set reference sensor."""
        self.reference_sensor = ref_sensor

    def write_output(self) -> None:
        """Write the actuator values."""
        control_method = self.control_method
        match control_method:
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

    def _write(self, value: int) -> None:
        print(value)

    def set_control_method(self, control_dict: dict) -> None:
        """Change the current configuration of the actuator outputs.

        Inputs:
        -------
            control_dict: a dictionary with the parameters of the new configuration

        Example:
        -------
        {"method": "manual", "value": 255}
        {"method": "timer", "value": 255, "time_on": 5, "time_off": 10}
        {"method": "on_boundaries", "value": 255,
         "lower_bound": 1.52, "upper_bound": 5.45}
        {"method": "pid", "setpoint": 35}

        """
        current_method = self.control_method
        match control_dict:
            case {"method": "manual", "value": value}:
                try:
                    new_method = _ManualControl(value)
                except TypeError as e:
                    logger.warning(e)
                    logger.warning(control_dict)
                else:
                    if new_method != current_method:
                        self.control_method = new_method

            case {
                "method": "timer",
                "value": value,
                "time_on": time_on,
                "time_off": time_off,
            }:
                try:
                    new_method = _TimerControl(time_on, time_off, value)
                except TypeError as e:
                    logger.warning(e)
                    logger.warning(control_dict)
                else:
                    if new_method != current_method:
                        self.control_method = new_method

            case {"method": "pid", "setpoint": setpoint}:
                try:
                    new_method = _PidControl(setpoint)
                except TypeError as e:
                    logger.warning(e)
                    logger.warning(control_dict)
                else:
                    if new_method != current_method:
                        self.control_method = new_method

            case {
                "method": "on_boundaries",
                "lower_bound": lb,
                "upper_bound": ub,
                "value": value,
            }:
                try:
                    new_method = _OnBoundariesControl(lb, ub, value)
                except TypeError as e:
                    logger.warning(e)
                    logger.warning(control_dict)
                else:
                    if new_method != current_method:
                        self.control_method = new_method

            case _:
                logger.warning(control_dict)


class _ManualControl:
    def __init__(self, value: int) -> None:
        self.type = valid_control_modes[0]
        self.value = value

    @property
    def value(self) -> int:
        return self._value

    @value.setter
    def value(self, value: int) -> None:
        if not isinstance(value, int):
            raise TypeError
        self._value = value

    def __eq__(self, other: None) -> bool:
        this = [self.type, self.value]
        return this == other


class _TimerControl(_ManualControl):
    def __init__(self, time_on: float, time_off: float, value: int) -> None:
        super().__init__(value)
        self.type = valid_control_modes[1]
        self.time_on = time_on
        self.time_off = time_off

        self._current_timer = 0
        self._is_on = True
        self._last_time = datetime.now(tz=timezone.utc)

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
        this = [self.type, self.time_on, self.time_off, self.value]
        return this == other

    def get_value(self) -> int:
        delta_time = datetime.now(tz=timezone.utc) - self._last_time
        if delta_time.total_seconds() > self._current_timer:
            self._last_time = datetime.now(tz=timezone.utc)
            if self._is_on:
                self._current_timer = self.time_off
                self._is_on = False
            else:
                self._current_timer = self.time_on
                self._is_on = True

        if self._is_on:
            return self.value
        else:
            return 0


class _OnBoundariesControl(_ManualControl):
    def __init__(
        self, lower_bound: int, upper_bound: int, value: int, backwards: bool = False
    ) -> None:
        super().__init__(value)
        self.type = valid_control_modes[2]
        self.lower_bound = lower_bound
        self.upper_bound = upper_bound
        self.backwards = backwards
        if backwards:
            self._active_value = 150
        else:
            self._active_value = 0

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
        this = [self.type, self.lower_bound, self.upper_bound, self.backwards]
        return this == other

    def get_value(self, variable: float) -> int:
        if variable < self.lower_bound:
            if self.backwards:
                self._active_value = 0
            else:
                self._active_value = self.value
        elif variable > self.upper_bound:
            if self.backwards:
                self._active_value = self.value
            else:
                self._active_value = 0
        return self._active_value


class _PidControl:
    def __init__(
        self,
        setpoint: float,
        gains: set[float] = (100, 0.01, 0),
        limits: set[int] = (0, 255),
    ) -> None:
        self.type = valid_control_modes[3]
        self.setpoint = setpoint
        self.set_gains(gains)
        self.set_limits(limits)

        self._last_time = datetime.now(tz=timezone.utc)
        self._last_error = 0
        self._integral_sum = 0

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
        now = datetime.now(tz=timezone.utc)
        time_delta = now - self._last_time
        dt = time_delta.total_seconds()
        error = self.setpoint - variable
        d_error = error - self._last_error

        p_term = self.kp * error
        i_term = self.ki * error * dt
        d_term = self.kd * d_error / dt

        self._integral_sum += i_term
        self._integral_sum = max(self.min_val, min(self._integral_sum, self.max_val))
        self.value = int(p_term + self._integral_sum + d_term)

        self._last_error = error
        self._last_time = now

        return max(self.min_val, min(self.value, self.max_val))
