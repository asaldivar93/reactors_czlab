"""Define the actuator class."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from reactors_czlab.core.control import ControlFactory, _Control
from reactors_czlab.core.data import ControlConfig, ControlMethod
from reactors_czlab.core.reactor import IN_RASPBERRYPI

if TYPE_CHECKING:
    from reactors_czlab.core.data import PhysicalInfo

if IN_RASPBERRYPI:
    from reactors_czlab.core.reactor import rpiplc

_logger = logging.getLogger("server.actuator")


# Missing a Modbus Actuator
# Missing an Actuator factory?
class Actuator(ABC):
    """Base Actuator class."""

    def __init__(
        self,
        identifier: str,
        config: PhysicalInfo,
    ) -> None:
        """Instance base actuator class.

        Parameters
        ----------
        identifier:
            A unique identifier for the actuator
        config:
            A data class with config parameters for the actuator

        """
        self.id = identifier
        self.info = config
        self.channel = config.channels[0]
        self.controller = ControlFactory().create_control(
            ControlConfig(method=ControlMethod.manual, value=0),
        )

    def __repr__(self) -> str:
        """Print sensor id."""
        return f"Actuator(id: {self.id})"

    def __eq__(self, other: object) -> bool:
        """Test equality by senor id."""
        this = self.id
        return this == other

    @property
    def controller(self) -> _Control:
        """Get controller."""
        return self._controller

    @controller.setter
    def controller(self, controller: _Control) -> None:
        if not isinstance(controller, _Control):
            raise TypeError
        self._controller = controller

    def write_output(self, sens_value: float) -> None:
        """Write the actuator values."""
        value = self.controller.get_value(sens_value)
        old_value = self.channel.old_value
        if value != old_value:
            self.channel.old_value = value
            self.write(value)
            _logger.debug(f"Write {value} to {self.id}: {self.controller}")

    def set_control_config(self, config: ControlConfig) -> None:
        """Change the current configuration of the actuator outputs.

        Inputs:
        -------
        config: ControlConfig
            A dataclass with the parameters of the new controller

        """
        current_controller = self.controller
        try:
            new_controller = ControlFactory().create_control(config)
            # sets the new config only if it is different from the old config
            if current_controller != new_controller:
                self.controller = new_controller
                _logger.info(
                    f"Control config update - {self.id}:{self.controller}",
                )

        except TypeError:
            # Each control class checks that the values
            # passed are of the correct type
            _logger.exception(f"Wrong attributes in {self.id}:{config}")

    @abstractmethod
    def write(self, value: float) -> None:
        """Write actuator method."""


class RandomActuator(Actuator):
    """Class for testing."""

    def __init__(
        self,
        identifier: str,
        config: PhysicalInfo,
    ) -> None:
        """Instance base actuator class.

        Parameters
        ----------
        identifier:
            A unique identifier for the actuator
        config:
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
    ) -> None:
        """Interface a pin as an actuator class.

        Parameters
        ----------
        identifier:
            A unique identifier for the actuator
        config:
            A data class with config parameters for the actuator

        """
        super().__init__(identifier, config)
        if IN_RASPBERRYPI:
            chn = self.channel
            rpiplc.pin_mode(chn.pin, rpiplc.OUTPUT)
            mode = chn.type
            if mode == "pwm":
                rpiplc.analog_write_set_frequency(chn.pin, 100)

    def write(self, value: float) -> None:
        """Write to physical pin."""
        if IN_RASPBERRYPI:
            chn = self.channel
            rpiplc.analog_write(chn.pin, int(value))
            chn.value = value
            _logger.debug(f"Actuator {self.id} - {value}")


class ModbusActuator(Actuator):
    """Class writing to Modbus Channels."""
