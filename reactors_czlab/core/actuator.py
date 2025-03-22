"""Define the actuator class."""

from __future__ import annotations

import logging
from abc import abstractmethod

from reactors_czlab.core.control import ControlFactory, _Control
from reactors_czlab.core.reactor import IN_RASPBERRYPI
from reactors_czlab.core.sensor import Sensor

if IN_RASPBERRYPI:
    from reactors_czlab.core.reactor import rpiplc

_logger = logging.getLogger("server.actuator")


# Missing a Modbus Actuator
# Missing an Actuator factory?
class Actuator:
    """Base Actuator class."""

    def __init__(self, identifier: str, config: dict) -> None:
        """Instance the actuator class.

        Inputs
        -------
        -identifier: a unique identifier for the actuator
        -address: the Modbus address or the gpio pin
        """
        self.id = identifier
        self.address = config["address"]
        self.model = config["model"]
        self.channels = config["channels"]
        self.controller = ControlFactory().create_control(
            {"method": "manual", "value": 0},
        )
        self.reference_sensor = None

    @property
    def sensors(self) -> dict:
        """Return a Dict of Sensors."""
        return self._sensors

    @sensors.setter
    def sensors(self, sensors: dict) -> None:
        """Set available sensors."""
        if not isinstance(sensors, dict):
            raise TypeError
        self._sensors = sensors

    @property
    def reference_sensor(self) -> Sensor | None:
        """Sensor instance used as a reference."""
        return self._reference_sensor

    @reference_sensor.setter
    def reference_sensor(self, sensor: Sensor | None) -> None:
        """Set reference sensor."""
        if not isinstance(sensor, Sensor | None):
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
            reference = self.sensors[sensor]
            self.reference_sensor = reference
        self.reference_sensor.timer.add_suscriber(self.controller)

    def write_output(self) -> None:
        """Write the actuator values."""
        try:
            self._write(self.controller.get_value(self.reference_sensor))
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

        """
        current_controller = self.controller
        try:
            # Instance a new controller class
            new_controller = ControlFactory().create_control(control_config)
            # sets the new config only if it is different from the old config
            if current_controller != new_controller:
                if self.reference_sensor is not None:
                    # remove old controller form the timer sub
                    self.reference_sensor.timer.remove_suscriber(
                        self.controller,
                    )
                    # Add the new controller to the time subscription
                    self.reference_sensor.timer.add_suscriber(new_controller)
                self.controller = new_controller
                _logger.info(
                    f"Control config update - {self.id}:{new_controller}",
                )

        except TypeError:
            # Each control class checks that the values
            # passed are of the correct type
            _logger.exception(f"Wrong attributes in {self.id}:{control_config}")

        except AttributeError:
            # Catch an exception when the user hasn't set a reference sensor
            # before setting _OnBoundaries or _PidControl classes
            _logger.exception(f"reference sensor in {self.id} not set")

    @abstractmethod
    def _write(self, value: float) -> float:
        """Write actuator method."""


class AnalogActuator(Actuator):
    """Class writing to the RaspberryPi pins."""

    def __init__(self, identifier: str, config: dict) -> None:
        super().__init__(identifier, config)
        if IN_RASPBERRYPI:
            for chn in self.channels:
                rpiplc.pin_mode(chn["pin"], rpiplc.OUTPUT)
                rpiplc.analog_write_set_frequency(chn["pin"], 24)

    def _write(self, value: float) -> None:
        if IN_RASPBERRYPI:
            for chn in self.channels:
                rpiplc.analog_write(chn["pin"], value)


class ModbusActuator(Actuator):
    """Class writing to Modbus Channels."""
