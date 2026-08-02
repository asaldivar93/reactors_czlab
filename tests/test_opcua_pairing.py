"""Tests for the set_pairing/unpair OPC methods.

These exercise the method bodies directly. Only asyncua is needed (it is a
base dependency), not a running server: init_pairing_methods() is handed a
stub node that captures the callables instead of registering them.
"""

from __future__ import annotations

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


class _CapturingVariable:
    """Stand-in for a variable node that records what was written."""

    def __init__(self) -> None:
        self.values: list[str] = []

    async def write_value(self, value: str) -> None:
        """Capture a published value."""
        self.values.append(value)


@pytest.fixture
async def published(make_sensor, make_actuator):
    """A ReactorOpc whose pairing table is published to a stub variable."""
    reactor_opc = ReactorOpc(
        "R0",
        volume=5,
        sensors=[make_sensor("R0:ph")],
        actuators=[make_actuator("R0:pwm0"), make_actuator("R0:pwm1")],
        period=10,
    )
    node = _CapturingNode()
    reactor_opc.node = node
    reactor_opc.pairings_node = _CapturingVariable()
    await reactor_opc.init_pairing_methods(2)
    return (
        reactor_opc,
        node.methods["set_pairing"],
        node.methods["unpair"],
        reactor_opc.pairings_node,
    )


async def test_pairings_are_published_on_pair(published) -> None:
    """The pairing table is readable, not just Python-side state.

    Without this a client can call set_pairing but has no way to find
    out what is currently paired - the table lives only in
    Reactor.sampling.pairings.
    """
    _, set_pairing, _, node = published

    await _call(set_pairing, "R0:ph", "R0:pwm0", 1)

    assert json.loads(node.values[-1]) == [
        {"sensor": "R0:ph", "actuator": "R0:pwm0", "channel": 1},
    ]


async def test_pairings_are_published_on_unpair(published) -> None:
    """Unpairing republishes, so the table never goes stale."""
    _, set_pairing, unpair, node = published

    await _call(set_pairing, "R0:ph", "R0:pwm0", 0)
    await _call(unpair, "R0:ph", "R0:pwm0", 0)

    assert json.loads(node.values[-1]) == []


async def test_a_refused_pairing_publishes_nothing(published) -> None:
    """A rejected call must not republish an unchanged table."""
    _, set_pairing, _, node = published

    await _call(set_pairing, "R9:ph", "R0:pwm0", 0)

    assert node.values == []


async def test_published_pairings_are_sorted(published) -> None:
    """A client can compare two reads without minding dict order."""
    _, set_pairing, _, node = published

    await _call(set_pairing, "R0:ph", "R0:pwm1", 0)
    await _call(set_pairing, "R0:ph", "R0:pwm0", 1)

    actuators = [row["actuator"] for row in json.loads(node.values[-1])]
    assert actuators == sorted(actuators)
