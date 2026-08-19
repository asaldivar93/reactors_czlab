"""Stub-node tests for the server-wide sampling-period OPC contract."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from asyncua import ua

from reactors_czlab.core.reactor import Reactor
from reactors_czlab.opcua.server_config import ServerConfigOpc


class _Variable:
    """Writable read-back stub."""

    def __init__(self, value: float) -> None:
        self.value = value
        self.writes: list[float] = []

    async def write_value(self, value: object) -> None:
        """Capture the plain value carried by a possible Variant."""
        published = value.Value if isinstance(value, ua.Variant) else value
        self.value = published
        self.writes.append(published)


class _ObjectNode:
    """Capture variables and complete method declarations."""

    def __init__(self) -> None:
        self.variable: _Variable | None = None
        self.methods: dict[str, tuple[object, list, list]] = {}

    async def add_variable(
        self,
        idx: int,
        name: str,
        value: float,
        variant_type: object,
    ) -> _Variable:
        """Build the one read-back variable."""
        self.variable = _Variable(value)
        return self.variable

    async def add_method(
        self,
        idx: int,
        name: str,
        callback: object,
        inargs: list,
        outargs: list,
    ) -> None:
        """Capture the public method under its full browse name."""
        self.methods[name] = (callback, inargs, outargs)


class _Objects:
    """Capture the object created below the OPC Objects node."""

    def __init__(self) -> None:
        self.created_name = ""
        self.node = _ObjectNode()

    async def add_object(self, idx: int, name: str) -> _ObjectNode:
        """Return the one global configuration object."""
        self.created_name = name
        return self.node


async def _call(callback: object, period: float) -> tuple[bool, str]:
    """Invoke a captured @uamethod callback like asyncua."""
    result = await callback(ua.NodeId(), ua.Variant(period))
    return tuple(item.Value for item in result)


@pytest.fixture
def reactors(make_sensor, make_actuator) -> list[Reactor]:
    """Two reactors with two independently guarded actuators each."""
    return [
        Reactor(
            identifier,
            5.0,
            [make_sensor(f"{identifier}:ph")],
            [
                make_actuator(f"{identifier}:pwm0"),
                make_actuator(f"{identifier}:pwm1"),
            ],
            10.0,
        )
        for identifier in ("R0", "R1")
    ]


@pytest.fixture
async def config(reactors):
    """A fully declared ServerConfigOpc using capturing nodes."""
    objects = _Objects()
    server = SimpleNamespace(nodes=SimpleNamespace(objects=objects))
    config = ServerConfigOpc(reactors, 10.0)
    await config.init_node(server, 2)
    return config, objects


def test_method_declaration_matches_the_public_contract(config) -> None:
    """The browse names and argument types are stable for generic clients."""
    _, objects = config
    callback, inargs, outargs = objects.node.methods[
        "ServerConfig:set_sampling_period"
    ]

    assert callback is not None
    assert objects.created_name == "ServerConfig"
    assert [argument.Name for argument in inargs] == ["Seconds"]
    assert inargs[0].DataType.Identifier == ua.ObjectIds.Double
    assert [argument.Name for argument in outargs] == ["Accepted", "Message"]
    assert [argument.DataType.Identifier for argument in outargs] == [
        ua.ObjectIds.Boolean,
        ua.ObjectIds.String,
    ]


async def test_success_updates_every_reactor_and_publishes_readback(
    config,
    reactors,
) -> None:
    """A successful call synchronizes all loops and their guards."""
    server_config, objects = config
    callback = objects.node.methods["ServerConfig:set_sampling_period"][0]

    accepted, message = await _call(callback, 12.5)

    assert accepted is True
    assert "12.5" in message
    assert server_config.period == 12.5
    assert objects.node.variable.writes == [12.5]
    assert {reactor.period for reactor in reactors} == {12.5}
    assert {
        actuator.control_period
        for reactor in reactors
        for actuator in reactor.actuators.values()
    } == {12.5}


@pytest.mark.parametrize("period", [float("nan"), float("inf"), 0.5, 31.0])
async def test_invalid_period_is_rejected_without_publication(
    config,
    reactors,
    period: float,
) -> None:
    """Validation leaves the server-wide state untouched."""
    server_config, objects = config

    accepted, message = await server_config.set_sampling_period(period)

    assert accepted is False
    assert "between 1 and 30" in message
    assert server_config.period == 10.0
    assert objects.node.variable.writes == []
    assert {reactor.period for reactor in reactors} == {10.0}


async def test_only_an_accepted_period_triggers_checkpoint(config) -> None:
    """Persistence follows the same acceptance boundary as live mutation."""
    server_config, _ = config
    checkpoints: list[str] = []
    server_config.on_state_changed = lambda: checkpoints.append("saved")

    assert (await server_config.set_sampling_period(0.5))[0] is False
    assert checkpoints == []

    assert (await server_config.set_sampling_period(11.0))[0] is True
    assert checkpoints == ["saved"]

    assert (await server_config.set_sampling_period(11.0))[0] is True
    assert checkpoints == ["saved"]


async def test_active_autotune_rejects_the_global_change(
    config,
    reactors,
) -> None:
    """One active reactor run freezes the period for the whole server."""
    server_config, objects = config
    reactors[1].autotune = SimpleNamespace(
        run=SimpleNamespace(
            is_active=True,
            base_id="R1:pwm0",
            acid_id="R1:pwm1",
        ),
    )

    accepted, message = await server_config.set_sampling_period(15.0)

    assert accepted is False
    assert "autotuning" in message
    assert {reactor.period for reactor in reactors} == {10.0}
    assert objects.node.variable.writes == []


async def test_concurrent_changes_are_serialized(config, reactors) -> None:
    """A second call cannot mutate reactors while the first publishes."""
    server_config, objects = config
    publishing = asyncio.Event()
    release = asyncio.Event()
    writes: list[float] = []

    async def blocked_write(value: object) -> None:
        published = value.Value if isinstance(value, ua.Variant) else value
        publishing.set()
        await release.wait()
        writes.append(published)

    objects.node.variable.write_value = blocked_write
    first = asyncio.create_task(server_config.set_sampling_period(12.0))
    await publishing.wait()
    second = asyncio.create_task(server_config.set_sampling_period(14.0))
    await asyncio.sleep(0)

    assert second.done() is False
    assert {reactor.period for reactor in reactors} == {12.0}

    release.set()
    assert await first == (True, "sampling period set to 12 seconds")
    assert await second == (True, "sampling period set to 14 seconds")
    assert writes == [12.0, 14.0]
    assert {reactor.period for reactor in reactors} == {14.0}
