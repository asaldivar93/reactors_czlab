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


def test_a_volume_demand_starts_a_bolus(channel: Channel, clock) -> None:
    """1 mL at the 2000 count dispense duty runs the pump at that duty."""
    disp = Dispenser(OutputUnit.volume, channel, clock=clock)

    assert disp.duty(1.0) == 2000.0


def test_the_bolus_ends_on_time(channel: Channel, clock) -> None:
    """2000 counts is 20 mL/min, so 1 mL is exactly 3 s of running."""
    disp = Dispenser(OutputUnit.volume, channel, clock=clock)
    disp.duty(1.0)

    clock.advance(2.9)
    assert disp.tick() is None

    clock.advance(0.2)
    assert disp.tick() == 0.0


def test_the_bolus_ends_only_once(channel: Channel, clock) -> None:
    """After it has stopped the pump, the fast loop has nothing to say."""
    disp = Dispenser(OutputUnit.volume, channel, clock=clock)
    disp.duty(1.0)
    clock.advance(3.1)
    disp.tick()

    clock.advance(1.0)

    assert disp.tick() is None


def test_a_repeated_demand_is_ignored_within_the_control_period(
    channel: Channel,
    clock,
) -> None:
    """The re-trigger guard.

    Regression: write_output() is called every 50 ms by the loop that drives
    unpaired actuators. Without this guard a standing 2 mL demand would be
    dispensed forty times a second.
    """
    disp = Dispenser(
        OutputUnit.volume,
        channel,
        control_period=10.0,
        clock=clock,
    )
    assert disp.duty(1.0) == 2000.0

    clock.advance(3.1)
    disp.tick()  # bolus finished, pump off

    for _ in range(20):
        clock.advance(0.05)
        assert disp.duty(1.0) == 0.0  # guard holds it off


def test_a_new_decision_is_accepted_after_the_control_period(
    channel: Channel,
    clock,
) -> None:
    """on_boundaries must be able to dose again on the next cycle."""
    disp = Dispenser(
        OutputUnit.volume,
        channel,
        control_period=10.0,
        clock=clock,
    )
    disp.duty(1.0)
    clock.advance(10.1)

    assert disp.duty(1.0) == 2000.0


def test_a_new_decision_supersedes_a_bolus_in_flight(
    channel: Channel,
    clock,
) -> None:
    """A longer dose re-arms the deadline rather than queueing."""
    disp = Dispenser(
        OutputUnit.volume,
        channel,
        control_period=1.0,
        clock=clock,
    )
    disp.duty(1.0)  # 3 s of running

    clock.advance(2.0)
    assert disp.duty(1.0) == 2000.0  # re-armed for another 3 s

    clock.advance(2.0)
    assert disp.tick() is None  # the original deadline no longer applies

    clock.advance(1.1)
    assert disp.tick() == 0.0


def test_a_zero_volume_demand_stops_the_pump(channel: Channel, clock) -> None:
    """Back inside the band, on_boundaries demands nothing."""
    disp = Dispenser(
        OutputUnit.volume,
        channel,
        control_period=1.0,
        clock=clock,
    )
    disp.duty(1.0)
    clock.advance(1.1)

    assert disp.duty(0.0) == 0.0


def test_volume_totals_survive_a_superseded_bolus(
    channel: Channel,
    clock,
) -> None:
    """Totals come from actual runtime, not from demanded volumes."""
    disp = Dispenser(
        OutputUnit.volume,
        channel,
        control_period=1.0,
        clock=clock,
    )
    disp.duty(1.0)
    clock.advance(1.5)
    disp.duty(1.0)  # superseded after only 1.5 s of the first 3 s
    clock.advance(3.0)
    disp.tick()

    # 4.5 s at 20 mL/min = 1.5 mL, not the 2 mL demanded.
    assert disp.total_volume == pytest.approx(1.5)


def test_volume_demand_limits_use_the_control_period(
    channel: Channel,
    clock,
) -> None:
    """A PID cannot usefully ask for more than one period can deliver."""
    disp = Dispenser(
        OutputUnit.volume,
        channel,
        control_period=10.0,
        clock=clock,
    )

    # 20 mL/min for 10 s = 3.3333 mL.
    assert disp.demand_limits()[1] == pytest.approx(20.0 * 10.0 / 60.0)


def test_reset_cancels_a_bolus(channel: Channel, clock) -> None:
    """Reactor.stop() must not leave a dose to resume."""
    disp = Dispenser(
        OutputUnit.volume,
        channel,
        control_period=1.0,
        clock=clock,
    )
    disp.duty(1.0)

    disp.reset()
    clock.advance(10.0)

    assert disp.tick() is None


def test_check_unit_rejects_a_dispense_duty_that_does_not_pump() -> None:
    """A bolus needs a positive flow at the dispense duty or it never ends."""
    dead = Calibration(
        "R0_pwm0",
        a=0.01,
        b=-50.0,
        dispense_duty=1000.0,
        fitted_at="2026-07-27T10:00:00+00:00",
    )
    channel = Channel("pwm0", "pwm", calibration=dead)

    reason = check_unit(OutputUnit.volume, channel)

    assert reason is not None
    assert "dispense" in reason
