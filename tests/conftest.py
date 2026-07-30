"""Shared fixtures.

These tests deliberately avoid importing ``reactors_czlab.core.sensor``,
which pulls in pymodbus. FakeSensor implements the same duck type the
Reactor loops rely on, so the control and pairing logic can be tested with
nothing installed but pytest.
"""

from __future__ import annotations

import types
from typing import Callable

import pytest

from reactors_czlab.core.actuator import RandomActuator
from reactors_czlab.core.data import (
    Calibration,
    Channel,
    PhysicalInfo,
    PlcOutput,
)
from reactors_czlab.gui.address import AddressBook
from reactors_czlab.gui.state import STATE as GUI_STATE


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

    Bolus timing is measured in seconds; sleeping through it would make the
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
    1 mL bolus takes exactly 3 s.
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


#: A browsed address space, in the shape OpcClient produces.
GUI_SENSOR_VARS = {
    "ns=2;i=10": {"reactor": "R0", "name": "ph", "channel": "pH"},
    "ns=2;i=11": {"reactor": "R0", "name": "ph", "channel": "oC"},
    "ns=2;i=12": {"reactor": "R1", "name": "do", "channel": "ppm"},
}

GUI_ACTUATOR_VARS = {
    "ns=2;i=20": {"reactor": "R0", "name": "pwm0", "channel": "curr_value"},
    "ns=2;i=21": {"reactor": "R0", "name": "pwm0", "channel": "total_volume"},
    "ns=2;i=22": {"reactor": "R0", "name": "pwm0", "channel": "cal_a"},
    "ns=2;i=23": {"reactor": "R0", "name": "pwm0", "channel": "cal_b"},
    "ns=2;i=24": {"reactor": "R0", "name": "pwm0", "channel": "cal_r2"},
    "ns=2;i=25": {"reactor": "R0", "name": "pwm0", "channel": "setpoint"},
    "ns=2;i=26": {"reactor": "R0", "name": "pwm1", "channel": "curr_value"},
}

GUI_METHODS = {
    "ns=2;i=30": {"reactor": "R0", "name": ["set_pairing"]},
    "ns=2;i=31": {"reactor": "R0", "name": ["unpair"]},
    "ns=2;i=32": {"reactor": "R0", "name": ["pwm0", "get_calibration"]},
    "ns=2;i=33": {"reactor": "R0", "name": ["pwm1", "get_calibration"]},
}


@pytest.fixture
def gui_state(monkeypatch: pytest.MonkeyPatch):
    """The GUI's AppState as if it had connected and browsed.

    Returned so a test can stub further - reading(), write_variable()
    and call() are what the page suites replace.
    """
    monkeypatch.setattr(
        GUI_STATE,
        "book",
        AddressBook.build(
            GUI_SENSOR_VARS,
            GUI_ACTUATOR_VARS,
            GUI_METHODS,
        ),
    )
    monkeypatch.setattr(
        GUI_STATE,
        "client",
        types.SimpleNamespace(recording=False),
    )
    return GUI_STATE
