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
    opc.variables = {**opc.sensor_vars, **opc.actuator_vars}
    return opc


class _NotifyingNode:
    """A node that reports its id the way datachange_notification reads it."""

    def __init__(self, nodeid: str) -> None:
        self.nodeid = types.SimpleNamespace(to_string=lambda: nodeid)


async def test_total_volume_is_archived_and_cal_fields_are_not(
    client_module: Any,
) -> None:
    """The spec's ``total_volume`` trace has to reach the ``data`` table.

    Regression: ``match_tree`` only builds the ``{nodeid: info}`` map;
    something else has to decide what is actually stored, and that
    decision named ``curr_value`` alone. Every new pump variable
    published fine and was archived by nothing, so ``run_plots.py``
    filtering on ``total_volume`` found an empty table.

    The decision used to be taken at subscribe time and is now taken per
    notification by ``archives()``, because a user interface needs the
    ``cal_*`` and control-config variables live. What reaches the table
    is unchanged, which is what this pins. The ``cal_*`` variables stay
    out on purpose: they move only on a refit, so at the 500 ms
    publishing interval they would fill the table with constants.
    """
    opc = _client_with(
        client_module,
        ["curr_value", "total_volume", "cal_a", "cal_b", "cal_r2"],
    )

    archived = {
        nodeid
        for nodeid, info in opc.variables.items()
        if opc.archives(nodeid, info)
    }
    assert archived == {
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


def test_every_sensor_channel_is_archived(client_module: Any) -> None:
    """The filter is actuator-only; sensors are archived wholesale."""
    opc = _client_with(client_module, ["cal_a"])
    opc.sensor_vars["R0:spectral:nm415"] = {
        "reactor": "R0",
        "name": "spectral",
        "channel": "nm415",
    }
    opc.variables = {**opc.sensor_vars, **opc.actuator_vars}

    archived = {
        nodeid
        for nodeid, info in opc.variables.items()
        if opc.archives(nodeid, info)
    }
    assert archived == {"R0:ph:pH", "R0:spectral:nm415"}


async def test_display_only_variables_are_still_subscribed(
    client_module: Any,
) -> None:
    """The GUI reads control config and fitted lines off the subscription.

    They are published by the server but were never watched, so a client
    had no live view of them at all - only an explicit read would do,
    and nothing re-read them when an operator changed a gain.
    """
    opc = _client_with(
        client_module,
        ["curr_value", "total_volume", "cal_a", "kp", "setpoint"],
    )

    await opc.init_subscriptions()

    watched = {node.nodeid for node in opc.client.subscription.subscribed}
    assert watched == set(opc.variables)
    assert "R0:pwm0:kp" in watched


async def _notify(opc: Any, nodeid: str, value: float) -> None:
    """Deliver one data change the way asyncua would."""
    await opc.datachange_notification(_NotifyingNode(nodeid), value, None)


def _drain(opc: Any) -> list[tuple[str, dict]]:
    """Everything currently waiting to be written to the database."""
    rows = []
    while not opc._queue.empty():
        rows.append(opc._queue.get_nowait())
    return rows


class TestRecording:
    """Archiving is a thing an operator turns on and off."""

    async def test_nothing_is_queued_until_recording_starts(
        self,
        client_module: Any,
    ) -> None:
        """Regression: the queue filled whenever the archiver was stopped.

        datachange_notification used to enqueue unconditionally, so with
        no archiver draining it the 1000-slot queue filled and then
        logged a dropped-row error on every sample forever. That made
        start_psql/stop_psql unusable as a UI toggle.
        """
        opc = _client_with(client_module, ["curr_value"])

        for _ in range(20):
            await _notify(opc, "R0:ph:pH", 7.0)

        assert opc.recording is False
        assert _drain(opc) == []

    async def test_readings_stay_live_while_not_recording(
        self,
        client_module: Any,
    ) -> None:
        """The dashboard works with recording off - they are separate."""
        opc = _client_with(client_module, ["curr_value"])

        await _notify(opc, "R0:ph:pH", 7.25)

        assert opc.variables["R0:ph:pH"]["value"] == 7.25
        assert opc.variables["R0:ph:pH"]["timestamp"] is not None

    async def test_queues_once_recording(self, client_module: Any) -> None:
        """With recording on, an archivable reading is queued."""
        opc = _client_with(client_module, ["curr_value"])
        opc._recording = True

        await _notify(opc, "R0:ph:pH", 7.0)

        [(nodeid, row)] = _drain(opc)
        assert nodeid == "R0:ph:pH"
        assert row["value"] == 7.0

    async def test_display_only_variables_are_never_queued(
        self,
        client_module: Any,
    ) -> None:
        """Subscribed for the UI, but they must not reach the table."""
        opc = _client_with(client_module, ["curr_value", "kp"])
        opc._recording = True

        await _notify(opc, "R0:pwm0:kp", 120.0)

        assert _drain(opc) == []
        assert opc.variables["R0:pwm0:kp"]["value"] == 120.0


class TestErrorSentinel:
    """A failed device read is visible but never archived."""

    async def test_the_sentinel_is_recorded_for_display(
        self,
        client_module: Any,
    ) -> None:
        """A failing probe must be visible, not silently frozen.

        The callback used to return before touching the value, so the
        last good reading stayed on screen and an operator could not
        tell a working probe from a dead one.
        """
        from reactors_czlab.core.data import ERROR_VALUE

        opc = _client_with(client_module, ["curr_value"])

        await _notify(opc, "R0:ph:pH", ERROR_VALUE)

        assert opc.variables["R0:ph:pH"]["value"] == ERROR_VALUE

    async def test_the_sentinel_is_never_queued(
        self,
        client_module: Any,
    ) -> None:
        """-0.111 in the data table would read as a measurement."""
        from reactors_czlab.core.data import ERROR_VALUE

        opc = _client_with(client_module, ["curr_value"])
        opc._recording = True

        await _notify(opc, "R0:ph:pH", ERROR_VALUE)

        assert _drain(opc) == []


class TestExperimentTags:
    """Rows carry the experiment that was running when they were taken."""

    async def test_untagged_rows_record_no_experiment(
        self,
        client_module: Any,
    ) -> None:
        """Plain recording outside any experiment is still recording."""
        opc = _client_with(client_module, ["curr_value"])
        opc._recording = True

        await _notify(opc, "R0:ph:pH", 7.0)

        [(_, row)] = _drain(opc)
        assert row["experiment_name"] is None

    async def test_a_tagged_reactor_stamps_its_experiment(
        self,
        client_module: Any,
    ) -> None:
        """The tag is per reactor, so concurrent runs stay separable."""
        opc = _client_with(client_module, ["curr_value"])
        opc._recording = True
        opc.experiment_tags = {"R0": "fed-batch-3"}

        await _notify(opc, "R0:ph:pH", 7.0)

        [(_, row)] = _drain(opc)
        assert row["experiment_name"] == "fed-batch-3"

    async def test_other_reactors_are_unaffected(
        self,
        client_module: Any,
    ) -> None:
        """A reactor not in an experiment records without a name."""
        opc = _client_with(client_module, ["curr_value"])
        opc._recording = True
        opc.experiment_tags = {"R1": "fed-batch-3"}

        await _notify(opc, "R0:ph:pH", 7.0)

        [(_, row)] = _drain(opc)
        assert row["experiment_name"] is None


class TestConnectionState:
    """What a page polls to decide what to show."""

    def test_reports_disconnected_before_connecting(
        self,
        client_module: Any,
    ) -> None:
        """No client yet is a disconnected client, not a crash."""
        opc = client_module.OpcClient("opc.tcp://localhost:4840/")
        assert opc.state.value == "disconnected"
