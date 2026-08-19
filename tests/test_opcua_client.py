"""Tests for what the archiving client subscribes to.

``reactors_czlab.opcua.client`` imports ``reactors_czlab.sql.operations``,
which needs polars and psycopg - the PC-side ``client`` extra, which this
environment deliberately does not install (the same reason ``conftest``
avoids importing concrete sensor drivers). The module is stubbed below
when it cannot be imported, so the subscription filter is testable with
nothing installed but pytest, and the real one is used when it is there.
The client module is imported through a fixture rather than at the top of
this file, so the stub is in place before the import runs.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from datetime import datetime, timedelta
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


class _TreeNode:
    """A browsable node used to exercise explicit global discovery."""

    def __init__(
        self,
        name: str,
        node_class: str,
        *children: _TreeNode,
    ) -> None:
        self.name = name
        self.node_class = node_class
        self.children = list(children)
        self.nodeid = types.SimpleNamespace(to_string=lambda: f"id:{name}")

    async def read_browse_name(self) -> object:
        """Return the browse name."""
        return types.SimpleNamespace(Name=self.name)

    async def read_node_class(self) -> object:
        """Return a node-class-shaped object."""
        return types.SimpleNamespace(name=self.node_class)

    async def get_children(self) -> list[_TreeNode]:
        """Return this node's direct children."""
        return self.children


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
        self.attribute_reads: list[tuple[list[str], object]] = []
        self.descriptions: dict[str, str] = {}
        self.values: dict[str, object] = {}

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

    async def read_attributes(
        self,
        nodes: list[_StubNode],
        attribute: object,
    ) -> list[object]:
        """Return LocalizedText-shaped description values."""
        self.attribute_reads.append(([node.nodeid for node in nodes], attribute))
        return [
            types.SimpleNamespace(
                Value=types.SimpleNamespace(
                    Value=types.SimpleNamespace(
                        Text=self.descriptions[node.nodeid],
                    ),
                ),
            )
            for node in nodes
        ]

    async def read_values(self, nodes: list[_StubNode]) -> list[object]:
        """Return values for a batch of nodes."""
        return [self.values[node.nodeid] for node in nodes]


def _client_with(
    module: Any,
    actuator_channels: list[str],
    *,
    history_seconds: float = 0.0,
) -> Any:
    """An OpcClient whose browse has already found these variables."""
    opc = module.OpcClient(
        "opc.tcp://localhost:4840/",
        history_seconds=history_seconds,
    )
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


async def test_the_subscribed_set_is_exactly_the_archived_set(
    client_module: Any,
) -> None:
    """Subscribe what is archived; read everything else on demand."""
    opc = _client_with(
        client_module,
        ["curr_value", "total_volume", "cal_a", "kp", "setpoint"],
    )

    await opc.init_subscriptions()

    watched = {node.nodeid for node in opc.client.subscription.subscribed}
    archived = {
        nodeid
        for nodeid, info in opc.variables.items()
        if opc.archives(nodeid, info)
    }
    assert watched == archived
    assert "R0:pwm0:kp" not in watched


async def test_read_many_uses_one_client_batch(client_module: Any) -> None:
    """On-demand panels do not turn into one round trip per variable."""
    opc = _client_with(client_module, [])
    opc.client.values = {"n1": 1.0, "n2": 2.0}

    assert await opc.read_many(["n1", "n2"]) == [1.0, 2.0]


async def test_server_configuration_is_discovered_explicitly_and_separately(
    client_module: Any,
) -> None:
    """The global value can never enter device subscriptions or archives."""
    config = _TreeNode(
        "ServerConfig",
        "Object",
        _TreeNode("ServerConfig:sampling_period", "Variable"),
        _TreeNode("ServerConfig:set_sampling_period", "Method"),
    )
    objects = _TreeNode("Objects", "Object", config)
    opc = client_module.OpcClient("opc.tcp://localhost:4840/")
    opc.client = types.SimpleNamespace(
        nodes=types.SimpleNamespace(objects=objects),
    )

    variables = await opc.get_server_config_vars()
    methods = await opc.get_server_config_methods()

    assert variables == {
        "id:ServerConfig:sampling_period": {"name": "sampling_period"},
    }
    assert methods == {
        "id:ServerConfig:set_sampling_period": {
            "name": "set_sampling_period",
        },
    }
    opc.server_config_vars = variables
    opc.server_config_methods = methods
    assert "id:ServerConfig:sampling_period" not in opc.variables
    assert not opc.archives(
        "id:ServerConfig:sampling_period",
        variables["id:ServerConfig:sampling_period"],
    )


async def test_sensor_descriptions_are_batch_read(client_module: Any) -> None:
    """Every sensor description survives browse in one attribute call."""
    opc = _client_with(client_module, [])
    opc.sensor_vars = {
        "n1": {"reactor": "R0", "name": "do", "channel": "ppm"},
        "n2": {"reactor": "R0", "name": "ph", "channel": "pH"},
    }
    opc.client.descriptions = {
        "n1": "dissolved_oxygen",
        "n2": "acidity",
    }

    await opc._read_sensor_descriptions()

    assert len(opc.client.attribute_reads) == 1
    assert opc.sensor_vars["n1"]["description"] == "dissolved_oxygen"
    assert opc.sensor_vars["n2"]["description"] == "acidity"


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
        opc.recording_reactors.add("R0")

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
        opc.recording_reactors.add("R0")

        await _notify(opc, "R0:pwm0:kp", 120.0)

        assert _drain(opc) == []
        assert opc.variables["R0:pwm0:kp"]["value"] == 120.0

    async def test_another_reactor_is_not_queued_while_r0_records(
        self,
        client_module: Any,
    ) -> None:
        """Recording is selected before a row can enter the shared queue."""
        opc = _client_with(client_module, ["curr_value"])
        opc.sensor_vars["R1:ph:pH"] = {
            "reactor": "R1",
            "name": "ph",
            "channel": "pH",
        }
        opc.variables = {**opc.sensor_vars, **opc.actuator_vars}
        opc.recording_reactors.add("R0")

        await _notify(opc, "R1:ph:pH", 7.0)

        assert _drain(opc) == []
        assert opc.variables["R1:ph:pH"]["value"] == 7.0

    async def test_archiver_lives_while_any_reactor_records(
        self,
        client_module: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """One reactor pausing cannot stop another reactor's writer task."""
        opc = _client_with(client_module, [])
        parked = asyncio.Event()

        async def wait_forever() -> None:
            await parked.wait()

        monkeypatch.setattr(opc, "commit_to_db", wait_forever)
        await opc.start_recording("R0")
        task = opc._db_task
        await opc.start_recording("R1")

        await opc.stop_recording("R0")
        assert opc._db_task is task
        assert opc.recording_reactors == {"R1"}

        await opc.stop_recording("R1")
        assert opc._db_task is None


class TestMemoryHistory:
    """The GUI's optional recent-notification buffer."""

    async def test_grows_only_when_a_notification_arrives(
        self,
        client_module: Any,
    ) -> None:
        """A timer must never invent points between samples."""
        opc = _client_with(
            client_module,
            ["curr_value"],
            history_seconds=60.0,
        )
        assert opc.history_points() == []

        await _notify(opc, "R0:ph:pH", 7.0)

        [point] = opc.history_points()
        assert point[0] == "R0:ph:pH"
        assert point[5] == 7.0
        assert point[1].microsecond % 1000 == 0

    async def test_is_bounded_by_age(
        self,
        client_module: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Old readings leave each variable deque as new ones arrive."""
        opc = _client_with(
            client_module,
            ["curr_value"],
            history_seconds=10.0,
        )
        clock = type(
            "Clock",
            (),
            {"now": classmethod(lambda cls: cls.current)},
        )
        clock.current = datetime(2026, 8, 2, 12, 0)  # noqa: DTZ001
        monkeypatch.setattr(client_module, "datetime", clock)

        await _notify(opc, "R0:ph:pH", 7.0)
        clock.current += timedelta(seconds=5)
        await _notify(opc, "R0:ph:pH", 7.1)
        clock.current += timedelta(seconds=6)
        await _notify(opc, "R0:ph:pH", 7.2)

        points = opc.history_points(now=clock.current)
        assert [point[5] for point in points] == [7.1, 7.2]

    async def test_disabled_buffer_keeps_headless_footprint(
        self,
        client_module: Any,
    ) -> None:
        """The reactors-client default remains zero history."""
        opc = _client_with(client_module, ["curr_value"])
        await _notify(opc, "R0:ph:pH", 7.0)
        assert opc.history_points() == []


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
        opc.recording_reactors.add("R0")

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
        opc.recording_reactors.add("R0")

        await _notify(opc, "R0:ph:pH", 7.0)

        [(_, row)] = _drain(opc)
        assert row["experiment_name"] is None

    async def test_a_tagged_reactor_stamps_its_experiment(
        self,
        client_module: Any,
    ) -> None:
        """The tag is per reactor, so concurrent runs stay separable."""
        opc = _client_with(client_module, ["curr_value"])
        opc.recording_reactors.add("R0")
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
        opc.recording_reactors.add("R0")
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
