"""Raspberry Pi PLC actuator adapter."""

from __future__ import annotations

import logging

from reactors_czlab.core import hardware
from reactors_czlab.core.actuator import Actuator
from reactors_czlab.core.data import Channel, PhysicalInfo, PlcOutput

_logger = logging.getLogger("server.actuator")


def resolve_pwm_frequency(channel: Channel) -> int:
    """Return the validated PWM carrier frequency for ``channel``.

    Parameters
    ----------
    channel:
        A ``PlcOutput.pwm`` channel whose ``pwm_frequency_hz`` sets its
        carrier frequency.

    Returns
    -------
    int
        The positive-integer frequency in Hz.

    Raises
    ------
    ValueError
        If ``pwm_frequency_hz`` is missing or not a positive integer. A
        PWM output with no frequency, or a bad one, must fail loudly at
        construction rather than reach the pin.

    """
    frequency = channel.pwm_frequency_hz
    if isinstance(frequency, bool) or not isinstance(frequency, int) or frequency <= 0:
        error_message = f"pwm_frequency_hz must be a positive integer, got {frequency!r}"
        raise ValueError(error_message)
    return frequency


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

        Raises
        ------
        ValueError
            If a PWM channel does not carry a valid ``pwm_frequency_hz``.

        """
        super().__init__(identifier, config)
        chn = self.channel
        # Validate up front so a bad PWM frequency fails at construction on
        # any machine, not only when the pin is finally driven on the Pi.
        frequency = resolve_pwm_frequency(chn) if chn.type == PlcOutput.pwm else None
        if hardware.IN_RASPBERRYPI:
            hardware.rpiplc.pin_mode(chn.pin, hardware.rpiplc.OUTPUT)
            if frequency is not None:
                hardware.rpiplc.analog_write_set_frequency(chn.pin, frequency)

    def write(self, value: float) -> None:
        """Write to physical pin."""
        if hardware.IN_RASPBERRYPI:
            chn = self.channel
            hardware.rpiplc.analog_write(chn.pin, int(value))
            chn.value = value
            _logger.debug("Actuator %s - %s", self.id, value)
