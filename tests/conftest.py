"""Shared fixtures.

These tests deliberately avoid importing ``reactors_czlab.core.sensor``,
which pulls in pymodbus. FakeSensor implements the same duck type the
Reactor loops rely on, so the control and pairing logic can be tested with
nothing installed but pytest.
"""

from __future__ import annotations

from typing import Callable

import pytest

from reactors_czlab.core.actuator import RandomActuator
from reactors_czlab.core.data import Channel, PhysicalInfo, PlcOutput


class FakeSensor:
    """Minimal stand-in for reactors_czlab.core.sensor.Sensor."""

    def __init__(self, identifier: str, channels: list[Channel]) -> None:
        """Store the id and the channels the reactor will read."""
        self.id = identifier
        self.channels = channels
        self.reads = 0

    async def read(self) -> None:
        """Count the read; values are set directly by the tests."""
        self.reads += 1


def _build_sensor(identifier: str = "R0:ph", value: float = 7.0) -> FakeSensor:
    """Build a one channel fake sensor holding ``value``."""
    channel = Channel("pH", "pH", register="pmc1")
    channel.value = value
    return FakeSensor(identifier, [channel])


def _build_actuator(identifier: str = "R0:pwm0") -> RandomActuator:
    """Build an actuator that records writes instead of touching a pin."""
    info = PhysicalInfo(
        model="pwm",
        address=0,
        type=PlcOutput.pwm,
        channels=[Channel("pwm0", "pwm", pin="Q2.7")],
    )
    return RandomActuator(identifier, info)


@pytest.fixture
def make_sensor() -> Callable[..., FakeSensor]:
    """Factory for fake sensors."""
    return _build_sensor


@pytest.fixture
def make_actuator() -> Callable[..., RandomActuator]:
    """Factory for hardware-free actuators."""
    return _build_actuator


@pytest.fixture
def sensor() -> FakeSensor:
    """A fake pH sensor reading 7.0."""
    return _build_sensor()


@pytest.fixture
def actuator() -> RandomActuator:
    """A PWM actuator with the default manual controller."""
    return _build_actuator()
