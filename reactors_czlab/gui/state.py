"""The one connection and the one address book, shared by every page.

The GUI hosts the OPC client and the archiver in its own event loop, so
there is exactly one of each per process. ``OpcClient.variables`` - the
dict its subscription callback already maintains - is the read model;
pages poll it on a timer rather than being pushed to.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from asyncua.client.ua_client import UaClientState

from reactors_czlab.gui.address import AddressBook
from reactors_czlab.opcua.client import OpcClient
from reactors_czlab.sql import operations

if TYPE_CHECKING:
    from datetime import datetime

_logger = logging.getLogger("gui")

DEFAULT_ENDPOINT = "opc.tcp://10.10.10.20:55488/"

#: Assumed sampling period, for judging whether a reading is stale. The
#: server's SAMPLE_PERIOD is not published, so this is a setting.
DEFAULT_PERIOD = 10.0


class AppState:
    """Everything the pages share."""

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        period: float = DEFAULT_PERIOD,
    ) -> None:
        """Store the connection settings; nothing connects yet."""
        self.endpoint = endpoint
        self.period = period
        self.client: OpcClient | None = None
        self.book: AddressBook | None = None
        self.connection_error: str | None = None

    def __repr__(self) -> str:
        """Print the endpoint and whether it is connected."""
        return f"AppState({self.endpoint}, connected={self.connected})"

    @property
    def connected(self) -> bool:
        """Whether the OPC session is live right now.

        Reads the asyncua client's real connection state - a plain
        attribute read, not a round trip, so pages can poll it on a
        timer - rather than a flag latched at startup. A server
        restart or a rebooted Pi is then reflected the moment
        asyncua's auto-reconnect supervisor notices, not only after
        the GUI process itself is restarted.
        """
        return (
            self.client is not None
            and self.book is not None
            and self.client.state == UaClientState.CONNECTED
        )

    @property
    def reconnecting(self) -> bool:
        """Whether the link dropped and asyncua is retrying it.

        Distinct from ``connected`` so a page can show "the link is
        down but recovering on its own" rather than the flat
        "disconnected" that would otherwise be indistinguishable from
        a session that needs an operator to hit Retry.
        """
        return (
            self.client is not None
            and self.client.state == UaClientState.RECONNECTING
        )

    @property
    def database_available(self) -> bool:
        """Whether recording, experiments and plot history can work.

        Import-level, not connection-level: a missing psycopg disables
        those screens, and the reason is shown rather than raised.
        """
        return operations.PSYCOPG_AVAILABLE

    @property
    def recording(self) -> bool:
        """Whether the archiver is running."""
        return self.client is not None and self.client.recording

    async def connect(self) -> None:
        """Connect, browse and subscribe. Never raises.

        A server that is not up yet is the normal state on boot, so the
        failure is recorded for the UI to show and retried by the page's
        reconnect button rather than taking the process down.
        """
        client = OpcClient(self.endpoint)
        try:
            await client.connect()
            await client.init_subscriptions()
        except Exception as err:  # noqa: BLE001 - reported, not raised
            self.connection_error = f"{type(err).__name__}: {err}"
            _logger.warning(
                "Could not connect to %s: %s",
                self.endpoint,
                self.connection_error,
            )
            await client.disconnect()
            return

        self.client = client
        self.book = AddressBook.from_client(client)
        self.connection_error = None
        _logger.info("Connected to %s", self.endpoint)

    async def disconnect(self) -> None:
        """Drop the connection and the address book."""
        if self.client is not None:
            await self.client.disconnect()
        self.client = None
        self.book = None
        # gui.components.pairing caches the pairings variable's nodeid
        # across reconnects (node ids are only stable for the life of a
        # server process). Without this, a server restart leaves the
        # GUI holding a stale nodeid: the next read degrades to a
        # warning and an empty panel - indistinguishable from a
        # genuinely empty pairing table, the worst failure mode for
        # that screen. The import is local to avoid a state -> pairing
        # -> state cycle at module load.
        from reactors_czlab.gui.components import pairing

        pairing._PAIRINGS_NODES.clear()

    def reading(
        self,
        reactor: str,
        name: str,
        channel: str,
    ) -> tuple[float | None, datetime | None]:
        """The last published value of one variable, and when it arrived."""
        if self.client is None or self.book is None:
            return (None, None)
        nodeid = self.book.variable(reactor, name, channel)
        if nodeid is None:
            return (None, None)
        info = self.client.variables.get(nodeid, {})
        return (info.get("value"), info.get("timestamp"))

    async def write_variable(
        self,
        reactor: str,
        name: str,
        channel: str,
        value: object,
    ) -> bool:
        """Write one variable. Returns False if it could not be written."""
        if self.client is None or self.book is None:
            return False
        nodeid = self.book.variable(reactor, name, channel)
        if nodeid is None:
            _logger.error("No node for %s:%s:%s", reactor, name, channel)
            return False
        return await self.client.write(nodeid, value)

    async def call(
        self,
        reactor: str,
        owner: str | None,
        method: str,
        *args: object,
    ) -> object:
        """Call an OPC method by name.

        Raises
        ------
        LookupError
            If the method is not in the address book. A page that calls
            a method the server does not have is a bug, not an
            operational failure.

        """
        if self.client is None or self.book is None:
            error_message = "not connected"
            raise LookupError(error_message)
        nodeid = self.book.method(reactor, owner, method)
        if nodeid is None:
            error_message = f"No method {method} on {reactor}/{owner}"
            raise LookupError(error_message)
        return await self.client.call_method(nodeid, *args)


#: The process-wide state. Pages import this.
STATE = AppState()
