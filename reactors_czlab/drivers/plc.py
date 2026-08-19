"""Raspberry Pi PLC actuator adapter."""

from __future__ import annotations

import logging

from reactors_czlab.core import hardware
from reactors_czlab.core.actuator import Actuator
from reactors_czlab.core.data import PhysicalInfo, PlcOutput

_logger = logging.getLogger("server.actuator")

PWM_FREQUENCY_HZ = 24


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
        if hardware.IN_RASPBERRYPI:
            chn = self.channel
            hardware.rpiplc.pin_mode(chn.pin, hardware.rpiplc.OUTPUT)
            if chn.type == PlcOutput.pwm:
                hardware.rpiplc.analog_write_set_frequency(chn.pin, PWM_FREQUENCY_HZ)

    def write(self, value: float) -> None:
        """Write to physical pin."""
        if hardware.IN_RASPBERRYPI:
            chn = self.channel
            hardware.rpiplc.analog_write(chn.pin, int(value))
            chn.value = value
            _logger.debug("Actuator %s - %s", self.id, value)
