"""Define the actuator class."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from reactors_czlab.core.control import ControlFactory, _Control
from reactors_czlab.core.reactor import IN_RASPBERRYPI
from reactors_czlab.core.sensor import Sensor

if TYPE_CHECKING:
    from reactors_czlab.core.utils import PhysicalInfo

if IN_RASPBERRYPI:
    from reactors_czlab.core.reactor import rpiplc

_logger = logging.getLogger("server.actuator")


# Missing a Modbus Actuator
# Missing an Actuator factory?
class Actuator(ABC):
    """Base Actuator class."""

    def __init__(self, identifier: str, config: PhysicalInfo) -> None:
        """Instance base actuator class.

        Parameters
        ----------
        identifier: str
            A unique identifier for the actuator
        config: PhysicalInfo
            A data class with config parameters for the actuator

        """
        self.id = identifier
        self.info = config
        self.channel = config.channels[0]
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

    def write_output(self) -> None:
        """Write the actuator values."""
        try:
            self.write(self.controller.get_value(self.reference_sensor))
        except AttributeError:
            # Catch an exception when the user hasn't set a reference sensor
            # before setting _OnBoundaries or _PidControl classes
            _logger.exception(f"reference sensor in {self.id} not set")
            _logger.warning(f"Setting output in {self.id} = 0")
            self.write(0)

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
                self.controller = new_controller
                if self.reference_sensor is not None:
                    # remove old controller form the timer sub
                    self.reference_sensor.timer.remove_suscriber(
                        self.controller,
                    )
                    # Add the new controller to the time subscription
                    self.reference_sensor.timer.add_suscriber(new_controller)

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
    def write(self, value: float) -> None:
        """Write actuator method."""


class RandomActuator(Actuator):
    """Class for testing."""

    def __init__(self, identifier: str, config: PhysicalInfo) -> None:
        """Instance a random actuator class for testing.

        Parameters
        ----------
        identifier: str
            A unique identifier for the actuator
        config: PhysicalInfo
            A data class with config parameters for the actuator

        """
        super().__init__(identifier, config)

    def write(self, value: float) -> None:
        """Write value."""
        self.channel.value = value


class PlcActuator(Actuator):
    """Class to interface with the RaspberryPi PLC pins."""

    limits: ClassVar = {
        "lb": 0,
        "ub": 4095,
    }

    def __init__(
        self,
        identifier: str,
        config: PhysicalInfo,
        mode: str = "pwm",
    ) -> None:
        """Interface a pin as an actuator class.

        Parameters
        ----------
        identifier: str
            A unique identifier for the actuator
        config: PhysicalInfo
            A data class with config parameters for the actuator
        mode: str
            Sets the pin as PWM channel, else sets the pin as analog channel

        """
        super().__init__(identifier, config)
        if IN_RASPBERRYPI:
            chn = self.channel
            rpiplc.pin_mode(chn.pin, rpiplc.OUTPUT)
            if mode == "pwm":
                rpiplc.analog_write_set_frequency(chn.register, 24)

    def write(self, value: float) -> None:
        """Write to physical pin."""
        if IN_RASPBERRYPI:
            chn = self.channel
            rpiplc.analog_write(chn.register, value)
            chn.value = value


class ModbusActuator(Actuator):
    """Class writing to Modbus Channels."""
