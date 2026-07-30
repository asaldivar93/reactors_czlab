"""Tests for the set_pairing/unpair OPC methods.

These exercise the method bodies directly. Only asyncua is needed (it is a
base dependency), not a running server: init_pairing_methods() is handed a
stub node that captures the callables instead of registering them.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from asyncua import ua

from reactors_czlab.opcua.reactor import ReactorOpc


async def _call(method, *args):
    """Invoke a captured @uamethod callback the way the server would.

    @uamethod unwraps ua.Variant arguments and re-wraps the return value, so
    a direct call has to supply that shape and unpack the result.
    """
    result = await method(ua.NodeId(), *(ua.Variant(a) for a in args))
    return result[0].Value


class _CapturingNode:
    """Stand-in for an asyncua node that records added methods."""

    def __init__(self) -> None:
        self.methods: dict[str, object] = {}

    async def add_method(self, idx, name, callback, *args, **kwargs) -> None:
        """Capture the callback under its bare method name."""
        self.methods[name.split(":")[-1]] = callback


@pytest.fixture
async def paired(make_sensor, make_actuator):
    """A ReactorOpc with its pairing methods captured."""
    reactor_opc = ReactorOpc(
        "R0",
        volume=5,
        sensors=[make_sensor("R0:ph")],
        actuators=[make_actuator("R0:pwm0"), make_actuator("R0:pwm1")],
        period=10,
    )
    node = _CapturingNode()
    reactor_opc.node = node
    await reactor_opc.init_pairing_methods(2)
    return reactor_opc, node.methods["set_pairing"], node.methods["unpair"]


async def test_set_pairing_succeeds(paired) -> None:
    """A valid pairing is recorded and the actuator leaves the unpaired loop.

    Regression: the validation required the actuator to be in the paired
    *and* the unpaired list at once, which is impossible, so set_pairing
    always returned False.
    """
    reactor_opc, set_pairing, _ = paired

    assert await _call(set_pairing, "R0:ph", "R0:pwm0", 0) is True
    assert dict(reactor_opc.reactor.sampling.pairings) == {
        "R0:ph": [("R0:pwm0", 0)],
    }
    assert reactor_opc.reactor.unpaired.actuators == ["R0:pwm1"]


async def test_set_pairing_rejects_unknown_sensor(paired) -> None:
    """A sensor id from another reactor is refused."""
    _, set_pairing, _ = paired
    assert await _call(set_pairing, "R9:ph", "R0:pwm0", 0) is False


async def test_set_pairing_rejects_unknown_actuator(paired) -> None:
    """An actuator id from another reactor is refused."""
    _, set_pairing, _ = paired
    assert await _call(set_pairing, "R0:ph", "R9:pwm0", 0) is False


async def test_set_pairing_rejects_double_pairing(paired) -> None:
    """An actuator can only follow one sensor channel."""
    _, set_pairing, _ = paired
    assert await _call(set_pairing, "R0:ph", "R0:pwm0", 0) is True
    assert await _call(set_pairing, "R0:ph", "R0:pwm0", 1) is False


async def test_unpair_returns_the_actuator(paired) -> None:
    """Unpairing removes the pairing and restores the unpaired loop.

    Regression: unpair logged an undefined name and raised NameError after
    it had already mutated the pairings.
    """
    reactor_opc, set_pairing, unpair = paired

    await _call(set_pairing, "R0:ph", "R0:pwm0", 0)
    assert await _call(unpair, "R0:ph", "R0:pwm0", 0) is True

    assert reactor_opc.reactor.sampling.pairings["R0:ph"] == []
    assert set(reactor_opc.reactor.unpaired.actuators) == {
        "R0:pwm0",
        "R0:pwm1",
    }


async def test_unpair_rejects_a_pairing_that_does_not_exist(paired) -> None:
    """Unpairing something that was never paired is refused, not fatal."""
    _, _, unpair = paired
    assert await _call(unpair, "R0:ph", "R0:pwm1", 0) is False


async def test_reactor_opc_does_not_duplicate_state(paired) -> None:
    """ReactorOpc reads through to the Reactor rather than copying it.

    Regression: it kept its own sensors/actuators dicts alongside the
    Reactor's, so the two could drift apart.
    """
    reactor_opc, _, _ = paired
    assert reactor_opc.sensors is reactor_opc.reactor.sensors
    assert reactor_opc.actuators is reactor_opc.reactor.actuators


class _StubVariable:
    """An asyncua variable that just holds a value."""

    def __init__(self, value: object) -> None:
        self.value = value

    async def write_value(self, value: object) -> None:
        """Store a written value."""
        self.value = value


@pytest.fixture
async def published(make_sensor, make_actuator):
    """A ReactorOpc whose pairings variable is captured."""
    reactor_opc = ReactorOpc(
        "R0",
        volume=5,
        sensors=[make_sensor("R0:ph")],
        actuators=[make_actuator("R0:pwm0"), make_actuator("R0:pwm1")],
        period=10,
    )
    node = _CapturingNode()
    reactor_opc.node = node
    reactor_opc.pairings_var = _StubVariable("[]")
    await reactor_opc.init_pairing_methods(2)
    return reactor_opc, node.methods["set_pairing"], node.methods["unpair"]


async def test_pairing_is_published_as_json(published) -> None:
    """The GUI cannot read reactor.sampling.pairings; it reads this.

    Regression: pairings lived only as server-side Python state and the
    methods returned a bare bool, so no client could show what was
    paired or recover the picture after a restart.
    """
    reactor_opc, set_pairing, _ = published

    await _call(set_pairing, "R0:ph", "R0:pwm0", 0)

    assert json.loads(reactor_opc.pairings_var.value) == [
        {"sensor": "R0:ph", "actuator": "R0:pwm0", "channel": 0},
    ]


async def test_unpairing_is_published_too(published) -> None:
    """Removing a pairing updates the variable."""
    reactor_opc, set_pairing, unpair = published

    await _call(set_pairing, "R0:ph", "R0:pwm0", 0)
    await _call(unpair, "R0:ph", "R0:pwm0", 0)

    assert json.loads(reactor_opc.pairings_var.value) == []


async def test_a_refused_pairing_does_not_republish(published) -> None:
    """A rejected call leaves the published picture alone."""
    reactor_opc, set_pairing, _ = published
    reactor_opc.pairings_var.value = "sentinel"

    assert await _call(set_pairing, "R9:ph", "R0:pwm0", 0) is False
    assert reactor_opc.pairings_var.value == "sentinel"


class _SlowVariable:
    """An asyncua variable whose first write lands slower than the rest.

    Real network I/O has no fixed latency ordering. This fakes that by
    making the first call to ``write_value`` yield to the event loop
    more times before storing than later calls do, so a slower *first*
    publish and a faster *second* publish are actually in flight at
    the same time, the way two overlapping OPC calls would be.
    """

    def __init__(self, value: object) -> None:
        self.value = value
        self._writes = 0

    async def write_value(self, value: object) -> None:
        """Store a written value, after yielding control a few times."""
        self._writes += 1
        delay = 5 if self._writes == 1 else 1
        for _ in range(delay):
            await asyncio.sleep(0)
        self.value = value


@pytest.fixture
async def racing(make_sensor, make_actuator):
    """A ReactorOpc with two independent sensor/actuator pairs to race."""
    reactor_opc = ReactorOpc(
        "R0",
        volume=5,
        sensors=[make_sensor("R0:ph"), make_sensor("R0:do")],
        actuators=[make_actuator("R0:pwm0"), make_actuator("R0:pwm1")],
        period=10,
    )
    node = _CapturingNode()
    reactor_opc.node = node
    reactor_opc.pairings_var = _SlowVariable("[]")
    await reactor_opc.init_pairing_methods(2)
    return reactor_opc, node.methods["set_pairing"]


async def test_concurrent_publishes_do_not_lose_a_pairing(racing) -> None:
    """A slower earlier write must not clobber a faster later one.

    Regression: publish_pairings() built its JSON snapshot and wrote it
    with no lock spanning the two, so the snapshot and the write were
    not serialised with each other. Two overlapping set_pairing calls
    could interleave so that an earlier call's snapshot (taken before
    the later call's pairing existed) was still the write that landed
    last, silently dropping the later pairing from the published table
    until the next successful pairing change.
    """
    reactor_opc, set_pairing = racing

    await asyncio.gather(
        _call(set_pairing, "R0:ph", "R0:pwm0", 0),
        _call(set_pairing, "R0:do", "R0:pwm1", 0),
    )

    published = json.loads(reactor_opc.pairings_var.value)
    assert {(row["sensor"], row["actuator"]) for row in published} == {
        ("R0:ph", "R0:pwm0"),
        ("R0:do", "R0:pwm1"),
    }
