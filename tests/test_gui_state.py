"""Tests for AppState's picture of the OPC connection.

Regression: ``connected`` used to be ``book is not None``, set once at
startup and cleared only by ``disconnect()`` (wired solely to app
shutdown) - so a server restart or a rebooted Pi left a green
"connected" badge over a dead session with no recovery short of
restarting the whole GUI process. It must instead track the client's
real, live state.
"""

from __future__ import annotations

from asyncua.client.ua_client import UaClientState

from reactors_czlab.gui.state import AppState


class _FakeClient:
    """Stands in for OpcClient - only ``.state`` matters here."""

    def __init__(self) -> None:
        self.state = UaClientState.DISCONNECTED


def test_connected_reflects_the_live_client_state() -> None:
    """``connected``/``reconnecting`` must track a flipped client
    state, not a flag latched once at startup.
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
