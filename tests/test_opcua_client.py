"""Tests for what the archiving client subscribes to.

``reactors_czlab.opcua.client`` imports ``reactors_czlab.sql.operations``,
which needs polars and psycopg - the PC-side ``client`` extra, which this
environment deliberately does not install (the same reason ``conftest``
avoids ``core.sensor`` and its pymodbus). The module is stubbed below
when it cannot be imported, so the subscription filter is testable with
nothing installed but pytest, and the real one is used when it is there.
The client module is imported through a fixture rather than at the top of
this file, so the stub is in place before the import runs.
"""

from __future__ import annotations

import importlib
import sys
import types
from typing import Any

import pytest


@pytest.fixture
def client_module() -> Any:
    """The ``opcua.client`` module, with its sql import satisfied."""
    name = "reactors_czlab.sql.operations"
    if name not in sys.modules:
        try:
            importlib.import_module(name)
        except ImportError:
            stub = types.ModuleType(name)
            stub.SqlError = type("SqlError", (Exception,), {})
            stub.connect_to_db = None
            stub.store_data = None
            sys.modules[name] = stub
    return importlib.import_module("reactors_czlab.opcua.client")


class _StubNode:
    """An asyncua node that knows only its own id."""

    def __init__(self, nodeid: str) -> None:
        self.nodeid = nodeid

    async def read_browse_name(self) -> object:
        """Return something with a ``.Name``, as asyncua does."""
        return types.SimpleNamespace(Name=self.nodeid)


class _StubSubscription:
    """Records the nodes a subscription was asked to watch."""

    def __init__(self) -> None:
        self.subscribed: list[_StubNode] = []

    async def subscribe_data_change(self, nodes: list[_StubNode]) -> None:
        """Collect the nodes instead of talking to a server."""
        self.subscribed.extend(nodes)


class _StubClient:
    """The slice of asyncua's Client that init_subscriptions uses."""

    def __init__(self) -> None:
        self.subscription = _StubSubscription()

    async def create_subscription(
        self,
        params: object,
        handler: object,
    ) -> _StubSubscription:
        """Hand back the one recording subscription."""
        return self.subscription

    def get_node(self, nodeid: str) -> _StubNode:
        """Wrap a node id the way asyncua does."""
        return _StubNode(nodeid)


def _client_with(module: Any, actuator_channels: list[str]) -> Any:
    """An OpcClient whose browse has already found these variables."""
    opc = module.OpcClient("opc.tcp://localhost:4840/")
    opc.client = _StubClient()
    opc.sensor_vars = {
        "R0:ph:pH": {"reactor": "R0", "name": "ph", "channel": "pH"},
    }
    opc.actuator_vars = {
        f"R0:pwm0:{channel}": {
            "reactor": "R0",
            "name": "pwm0",
            "channel": channel,
        }
        for channel in actuator_channels
    }
    return opc


async def test_total_volume_is_subscribed_and_cal_fields_are_not(
    client_module: Any,
) -> None:
    """The spec's ``total_volume`` trace has to reach the ``data`` table.

    Regression: ``match_tree`` only builds the ``{nodeid: info}`` map;
    this filter decides what is actually watched, and it named
    ``curr_value`` alone. Every new pump variable published fine and was
    archived by nothing, so ``run_plots.py`` filtering on
    ``total_volume`` found an empty table. ``opcua/client.py`` is in
    neither the branch diff nor any task's files, which is why eleven
    per-task reviews could not have seen it.

    The ``cal_*`` variables stay unsubscribed on purpose: they move only
    on a refit, so at the 500 ms publishing interval they would fill the
    table with constants. They remain published and readable by any OPC
    client.
    """
    opc = _client_with(
        client_module,
        ["curr_value", "total_volume", "cal_a", "cal_b", "cal_r2"],
    )

    await opc.init_subscriptions()

    watched = {node.nodeid for node in opc.client.subscription.subscribed}
    assert watched == {
        "R0:ph:pH",
        "R0:pwm0:curr_value",
        "R0:pwm0:total_volume",
    }


def test_the_archived_channel_set_is_exactly_the_two_series(
    client_module: Any,
) -> None:
    """A named set, so adding a variable is a deliberate decision.

    Pinning the constant as well as the behaviour above means a future
    published variable cannot start being archived - or stop being -
    without this line changing with it.
    """
    assert client_module.ARCHIVED_ACTUATOR_CHANNELS == {
        "curr_value",
        "total_volume",
    }


async def test_every_sensor_channel_is_subscribed(
    client_module: Any,
) -> None:
    """The filter is actuator-only; sensors are archived wholesale."""
    opc = _client_with(client_module, ["cal_a"])
    opc.sensor_vars["R0:spectral:nm415"] = {
        "reactor": "R0",
        "name": "spectral",
        "channel": "nm415",
    }

    await opc.init_subscriptions()

    watched = {node.nodeid for node in opc.client.subscription.subscribed}
    assert watched == {"R0:ph:pH", "R0:spectral:nm415"}
