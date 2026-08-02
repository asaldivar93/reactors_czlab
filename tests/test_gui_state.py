"""Tests for the shared connection state.

No server: AppState is driven with a fake OpcClient, because what is
worth covering here is the lifecycle around the connection - the lock,
the never-raises contract, and the read-through helpers - not asyncua.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import ClassVar

import pytest
from asyncua.client.ua_client import UaClientState

from reactors_czlab.gui import state as state_module
from reactors_czlab.gui.state import AppState


class FakeOpcClient:
    """The slice of OpcClient that AppState touches."""

    #: Every client built, so a test can assert how many were.
    instances: ClassVar[list[FakeOpcClient]] = []

    def __init__(self, endpoint: str, timeout: float = 5.0) -> None:
        """Record the instance so a test can count how many were built."""
        self.endpoint = endpoint
        self.state = UaClientState.DISCONNECTED
        self.recording = False
        self.variables: dict[str, dict] = {}
        self.sensor_vars: dict[str, dict] = {}
        self.actuator_vars: dict[str, dict] = {}
        self.methods: dict[str, dict] = {}
        self.experiment_tags: dict[str, str] = {}
        self.disconnected = False
        self.writes: list[tuple[str, object]] = []
        self.calls: list[tuple[str, tuple]] = []
        self.slow_calls: list[tuple[str, tuple, float]] = []
        self.fail_on_connect: Exception | None = None
        FakeOpcClient.instances.append(self)

    async def connect(self) -> None:
        """Connect, or fail the way a server that is down would."""
        if self.fail_on_connect is not None:
            raise self.fail_on_connect
        self.state = UaClientState.CONNECTED

    async def init_subscriptions(self) -> None:
        """Nothing to subscribe to in a fake."""

    async def disconnect(self) -> None:
        """Record the teardown."""
        self.disconnected = True
        self.state = UaClientState.DISCONNECTED

    async def write(self, nodeid: str, value: object) -> bool:
        """Record a write."""
        self.writes.append((nodeid, value))
        return True

    async def call_method(self, nodeid: str, *args: object) -> object:
        """Record a call."""
        self.calls.append((nodeid, args))
        return "ok"

    async def call_slow_method(
        self,
        nodeid: str,
        *args: object,
        timeout: float,
    ) -> object:
        """Record a call made on a separate short-lived session."""
        self.slow_calls.append((nodeid, args, timeout))
        return "ok"

    async def start_recording(self) -> None:
        """Flip the flag the header reads."""
        self.recording = True

    async def stop_recording(self) -> None:
        """Flip it back."""
        self.recording = False


@pytest.fixture(autouse=True)
def _fake_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build FakeOpcClient wherever AppState builds an OpcClient."""
    FakeOpcClient.instances.clear()
    monkeypatch.setattr(state_module, "OpcClient", FakeOpcClient)


@pytest.fixture
def app() -> AppState:
    """A disconnected AppState."""
    return AppState("opc.tcp://localhost:4840/", period=10.0)


class TestConnect:
    """Connecting, and failing to."""

    async def test_connects_and_indexes(self, app: AppState) -> None:
        """A successful connect leaves a client and an address book."""
        await app.connect()

        assert app.connected
        assert app.book is not None
        assert app.connection_error is None

    async def test_a_server_that_is_down_never_raises(
        self,
        app: AppState,
    ) -> None:
        """A server not up yet is the normal state on boot.

        Raising here would take the GUI process down on every reboot
        where the Pi comes up after the PC.
        """
        FakeOpcClient.instances.clear()
        original = state_module.OpcClient

        def failing(endpoint: str, timeout: float = 5.0) -> FakeOpcClient:
            client = original(endpoint, timeout)
            client.fail_on_connect = OSError("connection refused")
            return client

        state_module.OpcClient = failing
        try:
            await app.connect()
        finally:
            state_module.OpcClient = original

        assert not app.connected
        assert app.connection_error is not None
        assert "connection refused" in app.connection_error

    async def test_a_failed_connect_tears_its_client_down(
        self,
        app: AppState,
    ) -> None:
        """Otherwise the half-open session is never closed."""
        original = state_module.OpcClient

        def failing(endpoint: str, timeout: float = 5.0) -> FakeOpcClient:
            client = original(endpoint, timeout)
            client.fail_on_connect = OSError("refused")
            return client

        state_module.OpcClient = failing
        try:
            await app.connect()
        finally:
            state_module.OpcClient = original

        assert FakeOpcClient.instances[-1].disconnected

    async def test_overlapping_connects_build_one_client(
        self,
        app: AppState,
    ) -> None:
        """Regression: a double-clicked Retry orphaned a session.

        Two overlapping calls each built an OpcClient, and whichever
        assigned self.client last silently orphaned the other's session
        and subscription - no disconnect ever ran on it, so it held a
        server session for the life of the process.
        """
        await asyncio.gather(app.connect(), app.connect(), app.connect())

        assert len(FakeOpcClient.instances) == 1

    async def test_retry_does_not_disturb_a_reconnecting_client(
        self,
        app: AppState,
    ) -> None:
        """asyncua is already recovering; tearing it down would undo that."""
        await app.connect()
        app.client.state = UaClientState.RECONNECTING

        await app.connect()

        assert len(FakeOpcClient.instances) == 1
        assert not app.client.disconnected


class TestConnectionState:
    """What the header shows."""

    async def test_reconnecting_is_not_disconnected(
        self,
        app: AppState,
    ) -> None:
        """An operator answers "disconnected" by hitting Retry.

        Which is exactly what must not happen while asyncua is
        recovering on its own, so the two states are shown apart.
        """
        await app.connect()
        app.client.state = UaClientState.RECONNECTING

        assert not app.connected
        assert app.reconnecting

    def test_a_never_connected_state_is_neither(
        self,
        app: AppState,
    ) -> None:
        """Before the first connect there is nothing to report."""
        assert not app.connected
        assert not app.reconnecting


class TestReadThrough:
    """Reading values without pages knowing about node ids."""

    async def test_reading_returns_value_and_timestamp(
        self,
        app: AppState,
    ) -> None:
        """The read model is OpcClient.variables, read directly."""
        await app.connect()
        app.book.variables[("R0", "ph", "pH")] = "ns=2;i=10"
        stamp = datetime(2026, 8, 2, 12, 0)  # noqa: DTZ001
        app.client.variables["ns=2;i=10"] = {"value": 7.1, "timestamp": stamp}

        assert app.reading("R0", "ph", "pH") == (7.1, stamp)

    async def test_an_unknown_variable_reads_as_nothing(
        self,
        app: AppState,
    ) -> None:
        """A page renders "--", it does not raise."""
        await app.connect()
        assert app.reading("R0", "ph", "nonsense") == (None, None)

    def test_reading_while_disconnected_is_nothing(
        self,
        app: AppState,
    ) -> None:
        """Pages poll on a timer, including before the first connect."""
        assert app.reading("R0", "ph", "pH") == (None, None)


class TestWriteAndCall:
    """Reaching the server by name rather than by node id."""

    async def test_write_resolves_the_node(self, app: AppState) -> None:
        """Pages name a variable; the book knows where it is."""
        await app.connect()
        app.book.variables[("R0", "pwm0", "kp")] = "ns=2;i=23"

        assert await app.write_variable("R0", "pwm0", "kp", 120.0)
        assert app.client.writes == [("ns=2;i=23", 120.0)]

    async def test_writing_an_unknown_variable_fails_softly(
        self,
        app: AppState,
    ) -> None:
        """False, so a form can report it without an exception path."""
        await app.connect()
        assert not await app.write_variable("R0", "pwm0", "nope", 1.0)

    async def test_calling_an_unknown_method_is_a_bug_not_a_failure(
        self,
        app: AppState,
    ) -> None:
        """A page calling a method the server lacks is a coding error.

        Folding it into a status string would hide it; pages check
        has_method() when a control is genuinely optional.
        """
        await app.connect()
        with pytest.raises(LookupError):
            await app.call("R0", "ph", "no_such_method")

    async def test_calling_while_disconnected_raises(
        self,
        app: AppState,
    ) -> None:
        """There is no node id to resolve without a connection."""
        with pytest.raises(LookupError, match="not connected"):
            await app.call("R0", None, "set_pairing")


class TestRecording:
    """The header toggle."""

    async def test_recording_needs_a_database(
        self,
        app: AppState,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The reason is raised so the page can show it."""
        await app.connect()
        monkeypatch.setattr(
            state_module.operations,
            "PSYCOPG_AVAILABLE",
            False,
        )

        with pytest.raises(state_module.operations.SqlError):
            await app.start_recording()

    async def test_starts_and_stops(
        self,
        app: AppState,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The flag the header reads follows the toggle."""
        await app.connect()
        monkeypatch.setattr(
            state_module.operations,
            "PSYCOPG_AVAILABLE",
            True,
        )

        await app.start_recording()
        assert app.recording

        await app.stop_recording()
        assert not app.recording


class TestAdoptRunningExperiments:
    """Picking tags back up after a restart."""

    async def test_adopts_tags_for_running_experiments(
        self,
        app: AppState,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A GUI restarted mid-run would otherwise archive untagged rows.

        Nothing later can fill that hole in: the tag is written with the
        row.
        """
        monkeypatch.setattr(
            state_module.operations,
            "PSYCOPG_AVAILABLE",
            True,
        )
        monkeypatch.setattr(
            state_module.operations,
            "active_experiments",
            lambda: {"R0": "fed-batch-3"},
        )

        await app.connect()

        assert app.client.experiment_tags == {"R0": "fed-batch-3"}

    async def test_a_database_error_does_not_stop_the_connect(
        self,
        app: AppState,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The GUI is still useful without the database."""

        def boom() -> dict:
            raise state_module.operations.SqlError("no database")

        monkeypatch.setattr(
            state_module.operations,
            "PSYCOPG_AVAILABLE",
            True,
        )
        monkeypatch.setattr(
            state_module.operations,
            "active_experiments",
            boom,
        )

        await app.connect()

        assert app.connected
        assert app.client.experiment_tags == {}


class TestSlowCalls:
    """Calls that outlive asyncua's reconnect watchdog."""

    async def test_a_slow_call_does_not_use_the_shared_connection(
        self,
        app: AppState,
    ) -> None:
        """Regression: a long call tears the shared session down.

        asyncua's reconnect supervisor probes every watchdog_intervall
        (1s) with a timeout of the same length, so a method call that
        outlasts the probe makes it conclude the link is dead and tear
        the session down - taking the subscription, and the whole live
        display, with it. Measured against a real server: with
        auto_reconnect a call of 4s or more kills the session whatever
        the timeout is set to.

        calibrate_point runs a pump for up to 600s, so it can never go
        through the shared connection.
        """
        await app.connect()
        app.book.methods[("R0", "pwm0", "calibrate_point")] = "ns=2;i=40"

        await app.call_slow(
            "R0",
            "pwm0",
            "calibrate_point",
            1000.0,
            60.0,
            timeout=90.0,
        )

        assert app.client.slow_calls == [("ns=2;i=40", (1000.0, 60.0), 90.0)]
        assert app.client.calls == []

    async def test_an_unknown_slow_method_raises(
        self,
        app: AppState,
    ) -> None:
        """Same contract as call(): a missing method is a bug."""
        await app.connect()
        with pytest.raises(LookupError):
            await app.call_slow("R0", "pwm0", "nope", timeout=10.0)
