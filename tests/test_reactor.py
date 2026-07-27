"""Tests for the Reactor loops and the sensor/actuator pairing."""

from __future__ import annotations

import asyncio

import pytest

from reactors_czlab.core.data import ControlConfig, ControlMethod
from reactors_czlab.core.reactor import Reactor


@pytest.fixture
def reactor(make_sensor, make_actuator) -> Reactor:
    """A reactor with one sensor and two actuators."""
    return Reactor(
        "R0",
        volume=5,
        sensors=[make_sensor("R0:ph", value=7.0)],
        actuators=[make_actuator("R0:pwm0"), make_actuator("R0:pwm1")],
        period=0.01,
    )


def test_collections_are_keyed_by_id(reactor: Reactor) -> None:
    """Sensors and actuators are exposed as dicts keyed by id."""
    assert set(reactor.sensors) == {"R0:ph"}
    assert set(reactor.actuators) == {"R0:pwm0", "R0:pwm1"}


def test_every_actuator_starts_unpaired(reactor: Reactor) -> None:
    """Nothing is paired until set_pairing is called."""
    assert reactor.sampling.pairings == {}
    assert set(reactor.unpaired.actuators) == {"R0:pwm0", "R0:pwm1"}
    assert set(reactor.sampling.actuators) == {"R0:pwm0", "R0:pwm1"}


def test_sensors_setter_rejects_non_lists() -> None:
    """Passing a dict instead of a list fails loudly."""
    with pytest.raises(TypeError):
        Reactor("R0", 5, {}, [], 1.0)


def test_pairings_accepts_a_new_sensor_key(reactor: Reactor) -> None:
    """Appending to pairings must not need the key to exist first.

    Regression: pairings was a plain dict, so the first set_pairing call
    raised KeyError.
    """
    reactor.sampling.pairings["R0:ph"].append(("R0:pwm0", 0))
    assert reactor.sampling.pairings["R0:ph"] == [("R0:pwm0", 0)]


def test_paired_actuator_follows_the_sensor(reactor: Reactor) -> None:
    """A paired actuator is driven by its reference sensor channel."""
    actuator = reactor.actuators["R0:pwm0"]
    actuator.set_control_config(
        ControlConfig(ControlMethod.on_boundaries, lb=6.0, ub=8.0, value=255),
    )
    reactor.sampling.pairings["R0:ph"].append(("R0:pwm0", 0))

    # pH 5.0 is below the lower bound, so the actuator turns on.
    reactor.sensors["R0:ph"].channels[0].value = 5.0
    reactor.update_paired_actuators()

    assert actuator.channel.value == 255


def test_bad_channel_index_is_survivable(reactor: Reactor) -> None:
    """Pairing to a channel the sensor does not have is logged, not fatal."""
    reactor.sampling.pairings["R0:ph"].append(("R0:pwm0", 99))
    reactor.update_paired_actuators()  # must not raise


async def test_sampling_loop_reads_and_signals(
    make_sensor,
    make_actuator,
) -> None:
    """The loop reads every sensor and sets the sample_ready event."""
    reactor = Reactor(
        "R0",
        volume=5,
        sensors=[make_sensor("R0:ph"), make_sensor("R0:do")],
        actuators=[make_actuator("R0:pwm0")],
        period=0.01,
    )
    sample_ready = asyncio.Event()

    task = asyncio.create_task(reactor.sampling_loop(sample_ready))
    try:
        await asyncio.wait_for(sample_ready.wait(), timeout=1.0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert reactor.sensors["R0:ph"].reads >= 1
    assert reactor.sensors["R0:do"].reads >= 1


async def test_unpaired_loop_drives_unpaired_actuators(
    make_sensor,
    make_actuator,
) -> None:
    """Actuators with no reference sensor still get their manual value."""
    reactor = Reactor(
        "R0",
        volume=5,
        sensors=[make_sensor()],
        actuators=[make_actuator("R0:pwm0")],
        period=0.01,
    )
    actuator = reactor.actuators["R0:pwm0"]
    actuator.set_control_config(
        ControlConfig(ControlMethod.manual, value=200),
    )

    task = asyncio.create_task(reactor.unpaired_loop())
    try:
        await asyncio.sleep(0.06)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert actuator.channel.value == 200


def test_stop_zeroes_every_actuator(reactor: Reactor) -> None:
    """stop() writes 0 to the hardware regardless of the controller."""
    for actuator in reactor.actuators.values():
        actuator.set_control_config(
            ControlConfig(ControlMethod.manual, value=255),
        )
        actuator.write_output(0)

    reactor.stop()

    for actuator in reactor.actuators.values():
        assert actuator.channel.value == 0
