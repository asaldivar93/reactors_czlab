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


async def test_every_published_variable_is_subscribed(
    client_module: Any,
) -> None:
    """The GUI reads live values off the subscription, so it needs all.

    The archived set is unchanged - the filter moved from subscribe time
    to enqueue time (see test_only_the_two_series_are_archived). Without
    this the GUI could not show a control config written by another OPC
    client, nor the calibration a refit had just installed.
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
        "R0:pwm0:cal_a",
        "R0:pwm0:cal_b",
        "R0:pwm0:cal_r2",
    }


def test_only_the_two_series_are_archived(client_module: Any) -> None:
    """The archived set is enforced at enqueue, and is unchanged.

    Regression: this used to be a subscription filter that named
    curr_value alone, so every new pump variable was published and
    archived by nothing, and run_plots.py filtering on total_volume
    found an empty table. The cal_* variables stay unarchived on
    purpose: they move only on a refit, so at the 500 ms publishing
    interval they would fill the table with constants.
    """
    opc = _client_with(
        client_module,
        ["curr_value", "total_volume", "cal_a", "cal_b", "cal_r2"],
    )

    archived = {
        nodeid
        for nodeid, info in opc.actuator_vars.items()
        if opc.archives(nodeid, info)
    }

    assert archived == {"R0:pwm0:curr_value", "R0:pwm0:total_volume"}


class _NotifyingNode:
    """A node shaped the way datachange_notification reads one.

    ``_StubNode.nodeid`` is a bare string, which is enough for the
    subscription assertions but not here: the callback calls
    ``node.nodeid.to_string()``.
    """

    def __init__(self, nodeid: str) -> None:
        self.nodeid = types.SimpleNamespace(to_string=lambda: nodeid)


async def test_values_update_with_recording_off(client_module: Any) -> None:
    """The GUI reads live values whether or not anything is archiving.

    Regression: datachange_notification enqueued unconditionally. With
    no archiver draining it, the 1000 slot queue filled and then logged
    an error every sample forever - latent until the GUI subscribed
    with recording off.
    """
    opc = _client_with(client_module, ["curr_value"])
    opc.variables = {**opc.sensor_vars, **opc.actuator_vars}

    for _ in range(client_module.QUEUE_MAXSIZE + 10):
        await opc.datachange_notification(
            _NotifyingNode("R0:ph:pH"),
            7.5,
            None,
        )

    assert opc.variables["R0:ph:pH"]["value"] == 7.5
    assert opc._queue.qsize() == 0


async def test_rows_are_tagged_with_the_reactor_experiment(
    client_module: Any,
) -> None:
    """A reactor in a running experiment tags every row it produces."""
    opc = _client_with(client_module, ["curr_value"])
    opc.variables = {**opc.sensor_vars, **opc.actuator_vars}
    opc.experiment_tags = {"R0": "growth-curve-1"}
    opc._db_task = object()  # pretend the archiver is running

    await opc.datachange_notification(_NotifyingNode("R0:ph:pH"), 7.5, None)

    _, info = opc._queue.get_nowait()
    assert info["experiment_name"] == "growth-curve-1"


async def test_untagged_rows_carry_none(client_module: Any) -> None:
    """Recording outside an experiment is allowed and leaves it NULL."""
    opc = _client_with(client_module, ["curr_value"])
    opc.variables = {**opc.sensor_vars, **opc.actuator_vars}
    opc._db_task = object()

    await opc.datachange_notification(_NotifyingNode("R0:ph:pH"), 7.5, None)

    _, info = opc._queue.get_nowait()
    assert info["experiment_name"] is None


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
    """Every sensor channel is subscribed alongside every actuator one.

    Superseded in spirit by test_every_published_variable_is_subscribed
    now that the subscription is unfiltered, but kept to pin that the
    sensor tree specifically (not just the actuator one) is walked and
    subscribed - previously ``cal_a`` would have been the only
    actuator channel excluded; now nothing is.
    """
    opc = _client_with(client_module, ["cal_a"])
    opc.sensor_vars["R0:spectral:nm415"] = {
        "reactor": "R0",
        "name": "spectral",
        "channel": "nm415",
    }

    await opc.init_subscriptions()

    watched = {node.nodeid for node in opc.client.subscription.subscribed}
    assert watched == {"R0:ph:pH", "R0:spectral:nm415", "R0:pwm0:cal_a"}
