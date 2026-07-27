"""Define the actuator class."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from reactors_czlab.core.control import ControlFactory, _Control
from reactors_czlab.core.data import ControlConfig, ControlMethod, PlcOutput
from reactors_czlab.core.hardware import IN_RASPBERRYPI, rpiplc

if TYPE_CHECKING:
    from reactors_czlab.core.data import PhysicalInfo

_logger = logging.getLogger("server.actuator")

PWM_FREQUENCY_HZ = 100


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
        """Print the actuator id."""
        return f"{type(self).__name__}(id: {self.id})"

    @property
    def controller(self) -> _Control:
        """Get controller."""
        return self._controller

    @controller.setter
    def controller(self, controller: _Control) -> None:
        if not isinstance(controller, _Control):
            error_message = f"Expected a _Control, got {type(controller)}"
            raise TypeError(error_message)
        self._controller = controller

    def write_output(self, sens_value: float) -> None:
        """Write the actuator value derived from a sensor reading."""
        value = self.controller.get_value(sens_value)
        if value != self.channel.old_value:
            self.channel.old_value = value
            self.write(value)
            _logger.debug("Write %s to %s: %s", value, self.id, self.controller)

    def set_control_config(self, config: ControlConfig) -> None:
        """Change the current configuration of the actuator outputs.

        Parameters
        ----------
        config:
            A dataclass with the parameters of the new controller

        """
        try:
            new_controller = ControlFactory().create_control(config)
        except TypeError:
            # Each control class checks that the values
            # passed are of the correct type
            _logger.exception("Wrong attributes in %s: %s", self.id, config)
            return

        # Replace the controller only if the configuration actually changed,
        # so an unrelated OPC write does not reset a running timer or PID.
        if self.controller != new_controller:
            self.controller = new_controller
            _logger.info(
                "Control config update - %s: %s",
                self.id,
                self.controller,
            )

    @abstractmethod
    def write(self, value: float) -> None:
        """Write actuator method."""


class RandomActuator(Actuator):
    """Class for testing without hardware."""

    def write(self, value: float) -> None:
        """Record the value in the channel."""
        self.channel.value = value


class PlcActuator(Actuator):
    """Class to interface with the RaspberryPi PLC pins."""

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
            if chn.type == PlcOutput.pwm:
                rpiplc.analog_write_set_frequency(chn.pin, PWM_FREQUENCY_HZ)

    def write(self, value: float) -> None:
        """Write to physical pin."""
        if IN_RASPBERRYPI:
            chn = self.channel
            rpiplc.analog_write(chn.pin, int(value))
            chn.value = value
            _logger.debug("Actuator %s - %s", self.id, value)
