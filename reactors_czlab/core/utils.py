"""Define the DictList class."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum, auto
from time import perf_counter

_logger = logging.getLogger("server.utils")


class Timer:
    """A simple timer.

    Parameters
    ----------
    interval: float
        The time interval required for the timer to activate
    last_time: float
        The time value since the timer was last active

    """

    def __init__(self, interval: float) -> None:
        """Initialize the timer."""
        self.interval = interval
        self.last_time = perf_counter()
        self._subscribers: list[object] = []
        self._sensors: list[object] = []
        self._actuators: list[object] = []
        # async subscribers
        self._as_subscribers: list[object] = []
        self._as_sensors: list[object] = []
        self._as_actuators: list[object] = []

    @property
    def interval(self) -> float:
        """Get the time interval."""
        return self._interval

    @interval.setter
    def interval(self, interval: float) -> None:
        """Set the time interval and reset the timer."""
        if not isinstance(interval, float | int):
            raise TypeError
        self.last_time = perf_counter()
        self._interval = interval
        _logger.debug(f"New interval: {self.interval}")

    def add_suscriber(self, subscriber: object) -> None:
        self._subscribers.append(subscriber)
        _logger.debug(f"Current subscribers: {self._subscribers}")

    def remove_suscriber(self, subscriber: object) -> None:
        try:
            self._subscribers.remove(subscriber)
        except ValueError:
            _logger.exception(f"{subscriber} not in self._subscribers")
        _logger.debug(f"Current subscribers: {self._subscribers}")

    def add_sensor(self, sensor: object) -> None:
        self._sensors.append(sensor)
        _logger.debug(f"Current sensors: {self._sensors}")

    def remove_sensor(self, sensor: object) -> None:
        try:
            self._sensors.remove(sensor)
        except ValueError:
            _logger.exception(f"{sensor} not in self._sensors")
        _logger.debug(f"Current sensors: {self._sensors}")

    def add_actuator(self, actuator: object) -> None:
        self._actuators.append(actuator)
        _logger.debug(f"Current actuators: {self._actuators}")

    def remove_actuator(self, actuator: object) -> None:
        try:
            self._actuators.remove(actuator)
        except ValueError:
            _logger.exception(f"{actuator} not in self._actuators")
        _logger.debug(f"Current actuators: {self._actuators}")

    def callback(self) -> None:
        """Evaluate if the elapsed time is higher than the interval."""
        this_time = perf_counter()
        self.elapsed_time = this_time - self.last_time
        if self.elapsed_time > self.interval:
            self.last_time = this_time
            for sensor in self._sensors:
                sensor.on_timer_callback()
            for actuator in self._actuators:
                actuator.on_timer_callback()
            for subscriber in self._subscribers:
                subscriber.on_timer_callback()

    def add_async_suscriber(self, subscriber: object) -> None:
        self._as_subscribers.append(subscriber)
        _logger.debug(f"Current subscribers: {self._as_subscribers}")

    def remove_async_suscriber(self, subscriber: object) -> None:
        try:
            self._as_subscribers.remove(subscriber)
        except ValueError:
            _logger.exception(f"{subscriber} not in self._as_subscribers")
        _logger.debug(f"Current subscribers: {self._as_subscribers}")

    def add_async_sensor(self, sensor: object) -> None:
        self._as_sensors.append(sensor)
        _logger.debug(f"Current sensors: {self._as_sensors}")

    def remove_async_sensor(self, sensor: object) -> None:
        try:
            self._as_sensors.remove(sensor)
        except ValueError:
            _logger.exception(f"{sensor} not in self._sensors")
        _logger.debug(f"Current sensors: {self._as_sensors}")

    def add_async_actuator(self, actuator: object) -> None:
        self._as_actuators.append(actuator)
        _logger.debug(f"Current actuators: {self._as_actuators}")

    def remove_async_actuator(self, actuator: object) -> None:
        try:
            self._as_actuators.remove(actuator)
        except ValueError:
            _logger.exception(f"{actuator} not in self._actuators")
        _logger.debug(f"Current actuators: {self._as_actuators}")

    async def async_callback(self) -> None:
        this_time = perf_counter()
        self.elapsed_time = this_time - self.last_time
        if self.elapsed_time > self.interval:
            self.last_time = this_time
            for sensor in self._sensors:
                await sensor.async_timer_callback()
            for actuator in self._actuators:
                await actuator.async_timer_callback()
            for subscriber in self._subscribers:
                await subscriber.async_timer_callback()


@dataclass
class PhysicalInfo:
    """Class holding info for the sensors/actuators."""

    model: str
    address: int
    sample_interval: float
    channels: list[Channel]


@dataclass
class Channel:
    """Class holding config info for sensor/actuator channels."""

    units: str
    description: str
    register: str = "none"
    pin: str = "none"
    value: float = -0.111
    calibration: Calibration | None = None


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
    method: ControlMethod,
    time_on: float | None = None, time_off: float | None = None
    lb: float | None = None, up: float | None = None
    setpoint: float | None = None, value: float | None = None

    """

    method: ControlMethod
    time_on: float = 0.0
    time_off: float = 0.0
    lb: float = 0.0
    ub: float = 0.0
    setpoint: float = 0.0
    value: float = 0.0
