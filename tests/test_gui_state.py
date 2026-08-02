"""Tests for AppState: the live connection status and connect()'s
concurrency guard.

Both target the same underlying problem - the GUI's picture of the OPC
session drifting from reality - from two different angles: Fix 1 makes
``connected`` track the client's real state instead of a flag latched
at startup, and Fix 2 stops two overlapping ``connect()`` calls from
each building their own session.
"""

from __future__ import annotations

import asyncio

from asyncua.client.ua_client import UaClientState

from reactors_czlab.gui import state as state_module
from reactors_czlab.gui.state import AppState


class _FakeClient:
    """Stands in for OpcClient - only ``.state`` matters for these
    tests, plus enough of connect()/init_subscriptions()/disconnect()
    for AppState.connect() to run against it without a real server.
    """

    def __init__(self, release: asyncio.Event | None = None) -> None:
        self.state = UaClientState.DISCONNECTED
        self.disconnected = False
        self.started = asyncio.Event()
        self._release = release

    async def connect(self) -> None:
        self.started.set()
        if self._release is not None:
            await self._release.wait()
        self.state = UaClientState.CONNECTED

    async def init_subscriptions(self) -> None:
        """No-op: nothing here talks to a real server."""

    async def disconnect(self) -> None:
        self.disconnected = True
        self.state = UaClientState.DISCONNECTED


def test_connected_reflects_the_live_client_state() -> None:
    """Regression: ``connected`` used to be ``book is not None``, set
    once at startup and cleared only by ``disconnect()`` (wired solely
    to app shutdown) - so a server restart or a rebooted Pi left a
    green "connected" badge over a dead session with no recovery short
    of restarting the whole GUI process. It must instead track the
    client's real, live state.
    """
    app = AppState()
    app.client = _FakeClient()
    app.book = object()  # anything non-None; only .client.state moves here

    app.client.state = UaClientState.CONNECTED
    assert app.connected is True
    assert app.reconnecting is False

    app.client.state = UaClientState.RECONNECTING
    assert app.connected is False
    assert app.reconnecting is True

    app.client.state = UaClientState.DISCONNECTED
    assert app.connected is False
    assert app.reconnecting is False


async def test_connect_guards_against_a_double_click(monkeypatch) -> None:
    """Regression: connect() built a fresh OpcClient, awaited connect()
    and init_subscriptions(), then overwrote self.client - so two
    overlapping calls (an operator double-clicking Retry, or two
    browser tabs retrying at once) each built their own client, and
    whichever assigned self.client last orphaned the other's session
    and subscription with no disconnect() ever called on it.
    """
    app = AppState()
    created: list[_FakeClient] = []
    release = asyncio.Event()

    def make_client(endpoint: str) -> _FakeClient:
        client = _FakeClient(release)
        created.append(client)
        return client

    monkeypatch.setattr(state_module, "OpcClient", make_client)
    monkeypatch.setattr(
        state_module.AddressBook,
        "from_client",
        staticmethod(lambda client: object()),
    )

    first = asyncio.create_task(app.connect())
    await asyncio.sleep(0)  # let the first call reach client.connect()
    assert len(created) == 1
    assert created[0].started.is_set()

    second = asyncio.create_task(app.connect())
    await asyncio.sleep(0)  # let the second call block on the lock

    release.set()
    await first
    await second

    # Only one OpcClient was ever built, and it is the one that ended
    # up installed - the second caller's early return means it never
    # even constructed one.
    assert len(created) == 1
    assert app.client is created[0]
    assert created[0].disconnected is False


async def test_a_failed_connect_cleans_up_and_allows_a_retry(
    monkeypatch,
) -> None:
    """The lock must not wedge a permanently-failed connect(): a
    genuine failure has to leave self.client None so a later Retry can
    try again, and the half-open client it created must still be
    disconnected - the one cleanup path the prescribed fix says must
    not regress.
    """
    app = AppState()
    created: list[object] = []

    class _FailingClient:
        def __init__(self) -> None:
            self.disconnected = False
            created.append(self)

        async def connect(self) -> None:
            error_message = "no server there"
            raise ConnectionError(error_message)

        async def init_subscriptions(self) -> None:
            pass

        async def disconnect(self) -> None:
            self.disconnected = True

    monkeypatch.setattr(
        state_module,
        "OpcClient",
        lambda endpoint: _FailingClient(),
    )

    await app.connect()

    assert app.client is None
    assert app.connection_error is not None
    assert created[0].disconnected is True

    # A retry after the failure must actually try again, not be
    # swallowed by the "already connected" early return.
    await app.connect()
    assert len(created) == 2
