"""Define the reactor class."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reactors_czlab.core.actuator import BaseActuator
    from reactors_czlab.core.sensor import Sensor

from reactors_czlab.core.dictlist import DictList


class Reactor:
    """Class representation of the reactors."""

    def __init__(
        self,
        identifier: str,
        volume: float,
        sensors: list[Sensor],
        actuators: list[BaseActuator],
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
        for actuator in self.actuators:
            actuator.sensors = self.sensors

    @property
    def sensors(self) -> DictList:
        """Get the sensors DictList."""
        return self._sensors

    @sensors.setter
    def sensors(self, sensors: list[Sensor]) -> None:
        """Set the sensors as a DictList."""
        self._sensors = DictList(sensors)

    @property
    def actuators(self) -> DictList:
        """Get the actuators DictList."""
        return self._actuators

    @actuators.setter
    def actuators(self, actuators: list[BaseActuator]) -> None:
        """Set the actuators as a DictList."""
        self._actuators = DictList(actuators)

    def update_sensors(self) -> None:
        """Read all the sensors."""
        for sensor in self.sensors:
            sensor.read()

    def update_actuators(self) -> None:
        """Write the outputs of all actuators."""
        for actuator in self.actuators:
            actuator.write_output()
