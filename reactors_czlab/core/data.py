"""Dataclasses shared by the server and the client."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reactors_czlab.core.calibration.models import Calibration

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
    #: PWM carrier frequency in Hz, configured per output. Required for a
    #: PWM channel and unused by any other output kind; ``PlcActuator``
    #: validates and installs it on the pin.
    pwm_frequency_hz: int | None = None


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
