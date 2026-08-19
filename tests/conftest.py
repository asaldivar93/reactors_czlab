"""Shared fixtures.

These tests use a small sensor duck type so generic fixtures stay lightweight.
FakeSensor implements the same interface the
Reactor loops rely on, so the control and pairing logic can be tested with
nothing installed but pytest.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from reactors_czlab.core.actuator import RandomActuator
from reactors_czlab.core.calibration import Calibration
from reactors_czlab.core.data import (
    Channel,
    PhysicalInfo,
    PlcOutput,
)


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


class FakeClock:
    """Monotonic clock the tests drive by hand.

    Dose timing is measured in seconds; sleeping through it would make the
    suite slow and flaky, so the dispenser takes its clock as a parameter.
    """

    def __init__(self) -> None:
        """Start at zero."""
        self.now = 0.0

    def __call__(self) -> float:
        """Read the clock, matching the perf_counter signature."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        self.now += seconds


def _build_calibration(name: str = "R0_pwm0") -> Calibration:
    """A fitted pump line with round numbers.

    flow = 0.01 * duty, so the dispense duty of 2000 gives 20 mL/min and a
    1 mL dose takes exactly 3 s.
    """
    return Calibration(
        name,
        a=0.01,
        b=0.0,
        min_duty=400.0,
        max_duty=4000.0,
        dispense_duty=2000.0,
        points=[(500.0, 5.0), (2500.0, 25.0)],
        fitted_at="2026-07-27T10:00:00+00:00",
        r2=1.0,
    )


def _build_calibrated_actuator(
    identifier: str = "R0:pwm0",
    *,
    fitted: bool = True,
) -> RandomActuator:
    """An actuator whose channel carries a pump calibration."""
    calibration = _build_calibration() if fitted else Calibration("R0_pwm0")
    info = PhysicalInfo(
        model="pwm",
        address=0,
        type=PlcOutput.pwm,
        channels=[
            Channel("pwm0", "pwm", pin="Q2.7", calibration=calibration),
        ],
    )
    return RandomActuator(identifier, info)


@pytest.fixture
def clock() -> FakeClock:
    """A hand-driven clock."""
    return FakeClock()


@pytest.fixture
def calibration() -> Calibration:
    """A fitted pump calibration."""
    return _build_calibration()


@pytest.fixture
def make_calibrated_actuator() -> Callable[..., RandomActuator]:
    """Factory for actuators with a calibrated pump channel."""
    return _build_calibrated_actuator
