"""Dataclasses."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto


@dataclass
class PhysicalInfo:
    """Class holding info for the sensors/actuators."""

    model: str
    address: int
    sample_interval: float
    channels: list[Channel]


class PlcOutput(StrEnum):
    """Available Outputs in PLC.

    Parameters
    ----------
    pwm, analog, digital

    """

    pwm = auto()
    analog = auto()
    digital = auto()


@dataclass
class Channel:
    """Class holding config info for sensor/actuator channels."""

    units: str
    description: str = "none"
    register: str = "none"
    pin: str = "none"
    type: PlcOutput = PlcOutput.pwm
    value: float = -0.111
    calibration: Calibration | None = None

    def __eq__(self, other: object) -> bool:
        return self.units == other.units


@dataclass
class Calibration:
    """Class holding linear regression parameters y = a*x + b."""

    file: str
    a: float
    b: float


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
        float | None (default: None)
    time_off:
        float | None (default: None)
    lb:
        float | None (default: None)
    up:
        float | None (default: None)
    setpoint:
        float | None (default: None)
    value:
        float | None (default: None)

    """

    method: ControlMethod
    time_on: float = 0.0
    time_off: float = 0.0
    lb: float = 0.0
    ub: float = 0.0
    setpoint: float = 0.0
    value: float = 0.0
