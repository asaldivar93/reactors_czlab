"""Tests for the Reactor loops and the sensor/actuator pairing."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from reactors_czlab.core.data import ControlConfig, ControlMethod, OutputUnit
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


async def test_sampling_feeds_autotune_before_unrelated_pairings(
    make_sensor,
    make_actuator,
    monkeypatch,
) -> None:
    """The fresh pH reaches the run before normal controllers are decided."""
    sensor = make_sensor("R0:ph", value=7.0)
    paired = make_actuator("R0:other")
    paired.set_control_config(
        ControlConfig(ControlMethod.on_boundaries, lb=6.0, ub=8.0, value=200),
    )
    reactor = Reactor("R0", 5, [sensor], [paired], 0.01)
    reactor.sampling.pairings[sensor.id].append((paired.id, 0))
    reactor.unpaired.actuators.remove(paired.id)
    order: list[tuple[str, float]] = []

    async def read() -> None:
        sensor.channels[0].value = 5.5
        order.append(("read", 5.5))

    class Run:
        is_active = True
        sensor_id = sensor.id
        base_id = "R0:base"
        acid_id = "R0:acid"

        def sample(self, value: float) -> None:
            order.append(("sample", value))

    run = Run()
    reactor.autotune = SimpleNamespace(run=run)
    monkeypatch.setattr(sensor, "read", read)
    original = paired.write_output

    def write_output(value: float) -> None:
        order.append(("paired", value))
        original(value)

    monkeypatch.setattr(paired, "write_output", write_output)
    sample_ready = asyncio.Event()
    task = asyncio.create_task(reactor.sampling_loop(sample_ready))
    try:
        await asyncio.wait_for(sample_ready.wait(), timeout=1.0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert order[:3] == [("read", 5.5), ("sample", 5.5), ("paired", 5.5)]
    assert paired.channel.value == 200


async def test_actuator_loop_drives_unpaired_actuators(
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

    task = asyncio.create_task(reactor.actuator_loop())
    try:
        await asyncio.sleep(0.06)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert actuator.channel.value == 200


async def test_actuator_loop_routes_selected_ticks_through_active_run(
    make_sensor,
    make_actuator,
    monkeypatch,
) -> None:
    """Selected interlocked deliveries advance only through owner APIs."""
    selected = make_actuator("R0:base")
    unrelated = make_actuator("R0:other")
    reactor = Reactor("R0", 5, [make_sensor()], [selected, unrelated], 1.0)
    counts = {"run": 0, "selected": 0, "unrelated": 0}

    class Run:
        is_active = True
        sensor_id = "R0:ph"
        base_id = selected.id
        acid_id = "R0:acid"

        def tick(self) -> None:
            counts["run"] += 1

    reactor.autotune = SimpleNamespace(run=Run())
    monkeypatch.setattr(
        selected,
        "tick",
        lambda: counts.__setitem__("selected", counts["selected"] + 1),
    )
    monkeypatch.setattr(
        unrelated,
        "tick",
        lambda: counts.__setitem__("unrelated", counts["unrelated"] + 1),
    )

    task = asyncio.create_task(reactor.actuator_loop())
    try:
        await asyncio.sleep(0.12)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert counts["run"] >= 2
    assert counts["selected"] == 0
    assert counts["unrelated"] >= 2


async def test_actuator_loop_ticks_paired_actuators(
    make_sensor,
    make_calibrated_actuator,
) -> None:
    """A bolus on a paired pump is ended by the fast loop, not the sampler.

    Regression: paired actuators are only refreshed once per sampling
    period. A dose timed at that granularity would overrun by seconds.
    """
    reactor = Reactor(
        "R0",
        volume=5,
        sensors=[make_sensor()],
        actuators=[make_calibrated_actuator("R0:pwm0")],
        period=10,
    )
    actuator = reactor.actuators["R0:pwm0"]
    actuator.set_control_config(
        ControlConfig(
            ControlMethod.manual,
            value=0.005,  # 0.005 mL at 20 mL/min = 15 ms
            output_unit=OutputUnit.volume,
        ),
    )
    actuator.write_output(0)
    assert actuator.channel.value == 2000

    task = asyncio.create_task(reactor.actuator_loop())
    try:
        await asyncio.sleep(0.2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert actuator.channel.value == 0


async def test_actuator_loop_ticks_a_truly_paired_actuator(
    make_sensor,
    make_calibrated_actuator,
) -> None:
    """A bolus still ends once the actuator has actually left unpaired.

    ``test_actuator_loop_ticks_paired_actuators`` above never calls the
    pairing mechanism, so its actuator is still sitting in
    ``reactor.unpaired.actuators`` for the whole test - it would pass even
    if ``actuator_loop`` ticked only that list. Reproduce what
    ``ReactorOpc.set_pairing`` actually does (remove the id from
    ``unpaired.actuators``, add it to ``sampling.pairings``) so this test
    would fail if the tick were narrowed to unpaired actuators only.
    """
    sensor = make_sensor()
    reactor = Reactor(
        "R0",
        volume=5,
        sensors=[sensor],
        actuators=[make_calibrated_actuator("R0:pwm0")],
        period=10,
    )
    actuator = reactor.actuators["R0:pwm0"]
    reactor.unpaired.actuators.remove("R0:pwm0")
    reactor.sampling.pairings[sensor.id].append(("R0:pwm0", 0))

    actuator.set_control_config(
        ControlConfig(
            ControlMethod.manual,
            value=0.005,  # 0.005 mL at 20 mL/min = 15 ms
            output_unit=OutputUnit.volume,
        ),
    )
    actuator.write_output(0)
    assert actuator.channel.value == 2000

    task = asyncio.create_task(reactor.actuator_loop())
    try:
        await asyncio.sleep(0.2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert actuator.channel.value == 0


def test_the_reactor_stamps_its_period_on_its_actuators(
    make_calibrated_actuator,
    make_sensor,
) -> None:
    """The volume guard has to know how often decisions arrive."""
    reactor = Reactor(
        "R0",
        volume=5,
        sensors=[make_sensor()],
        actuators=[make_calibrated_actuator("R0:pwm0")],
        period=7.5,
    )

    assert reactor.actuators["R0:pwm0"].control_period == 7.5


def test_stop_cancels_a_bolus(make_calibrated_actuator, make_sensor) -> None:
    """A restart must not resume a dose that was in flight."""
    reactor = Reactor(
        "R0",
        volume=5,
        sensors=[make_sensor()],
        actuators=[make_calibrated_actuator("R0:pwm0")],
        period=10,
    )
    actuator = reactor.actuators["R0:pwm0"]
    actuator.set_control_config(
        ControlConfig(
            ControlMethod.manual,
            value=1.0,
            output_unit=OutputUnit.volume,
        ),
    )
    actuator.write_output(0)

    reactor.stop()

    assert actuator.channel.value == 0
    assert actuator.dispenser.tick() is None


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
