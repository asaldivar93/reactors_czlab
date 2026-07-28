"""Tests for the demand-to-duty dispenser."""

from __future__ import annotations

import pytest

from reactors_czlab.core.data import (
    MAX_OUTPUT,
    Calibration,
    Channel,
    OutputUnit,
)
from reactors_czlab.core.dispenser import Dispenser, check_unit


@pytest.fixture
def channel(calibration: Calibration) -> Channel:
    """A pump channel with a fitted calibration."""
    return Channel("pwm0", "pwm", pin="Q2.7", calibration=calibration)


def test_duty_unit_is_a_passthrough(channel: Channel, clock) -> None:
    """The default unit must behave exactly as the code did before."""
    disp = Dispenser(OutputUnit.duty, channel, clock=clock)

    assert disp.duty(1234.0) == 1234.0
    assert disp.tick() is None


def test_flow_inverts_the_calibration(channel: Channel, clock) -> None:
    """20 mL/min on a 0.01 mL/min-per-count pump is 2000 counts."""
    disp = Dispenser(OutputUnit.flow, channel, clock=clock)

    assert disp.duty(20.0) == pytest.approx(2000.0)


def test_flow_is_a_level_not_an_event(channel: Channel, clock) -> None:
    """Flow mode never asks for a duty change from the fast loop."""
    disp = Dispenser(OutputUnit.flow, channel, clock=clock)
    disp.duty(20.0)

    clock.advance(60.0)

    assert disp.tick() is None


def test_zero_flow_turns_the_pump_off(channel: Channel, clock) -> None:
    """Off must be 0, not the stall floor."""
    disp = Dispenser(OutputUnit.flow, channel, clock=clock)

    assert disp.duty(0.0) == 0.0
    assert disp.duty(-5.0) == 0.0


def test_flow_below_the_stall_floor_is_raised(channel: Channel, clock) -> None:
    """A pump cannot turn slower than min_duty, so it over-delivers."""
    disp = Dispenser(OutputUnit.flow, channel, clock=clock)

    # 1 mL/min would be 100 counts, below the 400 count stall floor.
    assert disp.duty(1.0) == 400.0


def test_flow_is_capped_at_max_duty(channel: Channel, clock) -> None:
    """A demand beyond the pump's range saturates."""
    disp = Dispenser(OutputUnit.flow, channel, clock=clock)

    assert disp.duty(999.0) == 4000.0


def test_flow_accumulates_delivered_volume(channel: Channel, clock) -> None:
    """Running at 20 mL/min for 3 s delivers 1 mL."""
    disp = Dispenser(OutputUnit.flow, channel, clock=clock)
    disp.duty(20.0)

    clock.advance(3.0)
    disp.tick()

    assert disp.total_volume == pytest.approx(1.0)


def test_nothing_accumulates_while_the_pump_is_off(
    channel: Channel,
    clock,
) -> None:
    """An idle pump must not invent delivered volume."""
    disp = Dispenser(OutputUnit.flow, channel, clock=clock)
    disp.duty(0.0)

    clock.advance(600.0)
    disp.tick()

    assert disp.total_volume == 0.0


def test_demand_limits_per_unit(channel: Channel, clock) -> None:
    """Limits are handed to the controller in the config's own unit."""
    duty = Dispenser(OutputUnit.duty, channel, clock=clock)
    flow = Dispenser(OutputUnit.flow, channel, clock=clock)

    assert duty.demand_limits() == (0.0, MAX_OUTPUT)
    assert flow.demand_limits() == (0.0, 40.0)


def test_check_unit_allows_duty_without_a_calibration() -> None:
    """Raw duty control never needed a calibration and still does not."""
    assert check_unit(OutputUnit.duty, Channel("pwm1", "pwm")) is None


def test_check_unit_rejects_flow_without_a_calibration() -> None:
    """mL/min is meaningless on an uncalibrated pump."""
    reason = check_unit(OutputUnit.flow, Channel("pwm1", "pwm"))

    assert reason is not None
    assert "calibration" in reason


def test_check_unit_rejects_flow_on_an_unfitted_calibration() -> None:
    """A placeholder calibration is not a calibration."""
    channel = Channel("pwm0", "pwm", calibration=Calibration("R0_pwm0"))

    assert check_unit(OutputUnit.flow, channel) is not None
