"""Define the reactor class."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reactors_czlab.core.actuator import Actuator
    from reactors_czlab.core.sensor import Sensor


class Reactor:
    """Class representation of the reactors."""

    def __init__(
        self,
        identifier: str,
        volume: float,
        sensors: list[Sensor],
        actuators: list[Actuator],
    ) -> None:
        """Initialize the reactor.

        Inputs:
        -------
        -identifier: a unique identifier for the reactor.
        -volume: the initial volume of the reactor.
        -sensors: a list containig the Sensor instances.
        -actuator: a list cotaining the Actuator instances.
        """
        self.id = identifier
        self.sensors = sensors
        self.volume = volume
        self.actuators = actuators
        # Pass the available sensors to the actuators
        for actuator in self.actuators.values():
            actuator.sensors = self.sensors

    @property
    def sensors(self) -> dict:
        """Get the sensors dict."""
        return self._sensors

    @sensors.setter
    def sensors(self, sensors: list[Sensor]) -> None:
        """Set the sensors as a dict."""
        if not isinstance(sensors, list):
            raise TypeError
        self._sensors = {s.id: s for s in sensors}

    @property
    def actuators(self) -> dict:
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
