"""Define the reactor class."""

from __future__ import annotations

import platform
from typing import TYPE_CHECKING

from reactors_czlab.core.utils import Timer

if TYPE_CHECKING:
    from reactors_czlab.core.actuator import Actuator
    from reactors_czlab.core.sensor import Sensor

if platform.machine().startswith("aarch64"):
    from librpiplc import rpiplc

    rpiplc.init("RPIPLC_V6", "RPIPLC_58")

IN_RASPBERRYPI = platform.machine().startswith("aarch64")


class Reactor:
    """Class representation of the reactors."""

    def __init__(
        self,
        identifier: str,
        volume: float,
        sensors: list[Sensor],
        actuators: list[Actuator],
        timer: Timer,
    ) -> None:
        """Initialize the reactor.

        Parameters
        ----------
        identifier:
            A unique identifier for the reactor.
        volume:
            The initial volume of the reactor.
        sensors:
            A list containig the Sensor instances.
        actuators:
            A list cotaining the Actuator instances.

        """
        self.id = identifier
        self.sensors = sensors
        self.volume = volume
        self.actuators = actuators
        self.base_timer = timer

        # Create timers and pass them to the sensors
        self.timers = {timer.interval: timer}
        for sensor in sensors:
            interval = sensor.sensor_info.sample_interval
            new_timer = self.timers.get(interval, None)
            if new_timer is None:
                new_timer = Timer(interval)
                self.timers.update({interval: new_timer})
            sensor.base_timer = new_timer
            sensor.timer = new_timer

        # Pass the base timer to the actuators
        for actuator in self.actuators.values():
            actuator.sensors = sensors
            actuator.base_timer = self.base_timer
            actuator.timer = self.base_timer

    @property
    def sensors(self) -> dict[str, Sensor]:
        """Get the sensors dict."""
        return self._sensors

    @sensors.setter
    def sensors(self, sensors: list[Sensor]) -> None:
        """Set the sensors as a dict."""
        if not isinstance(sensors, list):
            raise TypeError
        self._sensors = {s.id: s for s in sensors}

    @property
    def actuators(self) -> dict[str, Actuator]:
        """Get the actuators dict."""
        return self._actuators

    @actuators.setter
    def actuators(self, actuators: list[Actuator]) -> None:
        """Set the actuators as a dict."""
        self._actuators = {a.id: a for a in actuators}

    def update_sensors(self) -> None:
        """Read all the sensors."""
        for sensor in self.sensors.values():
            sensor.read()

    def update_actuators(self) -> None:
        """Write the outputs of all actuators."""
        for actuator in self.actuators.values():
            actuator.write_output()

    def update(self) -> None:
        """Call all timers and subscribers."""
        for timer in self.timers.values():
            timer.callback()
