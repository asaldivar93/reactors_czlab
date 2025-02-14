"""Define the actuator class."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from reactors_czlab import Sensor

valid_control_modes = ["manual", "timer", "on_boundaries", "pid"]


class Actuator:
    """Actuator class."""

    def __init__(self, identifier: str, channel: str, control_dict: dict) -> None:
        """Instance the actuator class."""
        self.id = identifier
        self.channel = channel
        self.set_control_method(control_dict)

    def _set_reference_sensor(self, ref_sensor: Sensor) -> None:
        self._reference_sensor = ref_sensor

    def write_output(self) -> None:
        """Function to write the actuator values."""
        control_method = self.control_method
        match control_method:
            case _ManualControl() as control:
                self._write(control.value)
            case _

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
                new_method = _ManualControl(value)
                if new_method != current_method:
                    self.control_method = new_method

            case {
                "method": "timer",
                "value": value,
                "time_on": time_on,
                "time_off": time_off,
            }:
                new_method = _TimerControl(time_on, time_off, value)
                if new_method != current_method:
                    self.control_method = new_method

            case {"method": "pid", "setpoint": setpoint}:
                new_method = _PidControl(setpoint)
                if new_method != current_method:
                    self.control_method = new_method

            case {
                "method": "on_boundaries",
                "lower_bound": lb,
                "upper_bound": ub,
                "value": value,
            }:
                new_method = _OnBoundariesControl(lb, ub, value)
                if new_method != current_method:
                    self.control_method = new_method

            case _:
                print(f"{current_method}")



class _ManualControl(NamedTuple):
    type = valid_control_modes[0]
    value: int

    def get_args(self) -> None:
        return [self.control_type, *self]


class _TimerControl(NamedTuple):
    type = valid_control_modes[1]
    time_on: int
    time_off: int
    value: int

    def get_args(self) -> None:
        return [self.control_type, *self]


class _OnBoundariesControl(NamedTuple):
    type = valid_control_modes[2]
    lower_bound: float
    upper_bound: float
    value: int

    def get_args(self) -> None:
        return [self.control_type, *self]


class _PidControl(NamedTuple):
    type = valid_control_modes[3]
    setpoint: float

    def get_args(self) -> None:
        return [self.control_type, *self]
