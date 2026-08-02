"""The one connection and the one address book, shared by every page.

The GUI hosts the OPC client and the archiver in its own event loop, so
there is exactly one of each per process. ``OpcClient.variables`` - the
dict its subscription callback already maintains - is the read model;
pages poll it on a timer rather than being pushed to. That is why there
is no callback fan-out here, and no second copy of the values.
"""

from __future__ import annotations

import asyncio
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
#: server's SAMPLE_PERIOD is not published, so this is a setting rather
#: than something that can be discovered.
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
        #: Serialises connect() so an operator double-clicking Retry, or
        #: two browser tabs retrying at once, cannot both build an
        #: OpcClient and race to overwrite self.client - the loser would
        #: orphan its session and subscription with no disconnect(),
        #: holding a server session for the life of the process.
        self._connect_lock = asyncio.Lock()

    def __repr__(self) -> str:
        """Print the endpoint and whether it is connected."""
        return f"AppState({self.endpoint}, connected={self.connected})"

    @property
    def connected(self) -> bool:
        """Whether the OPC session is live right now.

        Reads asyncua's real connection state - a plain attribute read,
        not a round trip, so pages can poll it - rather than a flag
        latched at startup. A restarted server is then reflected as soon
        as auto-reconnect notices, not only when the GUI is restarted.
        """
        return (
            self.client is not None
            and self.book is not None
            and self.client.state == UaClientState.CONNECTED
        )

    @property
    def reconnecting(self) -> bool:
        """Whether the link dropped and asyncua is retrying it.

        Distinct from ``connected`` so a page can say "down but
        recovering on its own" rather than the flat "disconnected" an
        operator would answer by hitting Retry - which is the one thing
        that must not happen mid-recovery.
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
    def database_reason(self) -> str:
        """Why the database features are unavailable, for display."""
        return operations.NO_PSYCOPG_REASON

    @property
    def recording(self) -> bool:
        """Whether the archiver is running."""
        return self.client is not None and self.client.recording

    async def connect(self) -> None:
        """Connect, browse and subscribe. Never raises.

        A server that is not up yet is the normal state on boot, so a
        failure is recorded for the UI to show and retried from the
        page's button rather than taking the process down.

        Guarded end to end by ``_connect_lock``, with an early return
        once a client exists. That early return also covers a client
        that is alive but mid-reconnect - ``connected`` is False there,
        and Retry must not tear down a session asyncua is recovering.
        """
        async with self._connect_lock:
            if self.client is not None:
                return

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

            await self.adopt_running_experiments()

    async def adopt_running_experiments(self) -> None:
        """Pick up experiment tags for reactors already running one.

        A GUI restarted mid-experiment would otherwise archive untagged
        rows for the rest of the run, leaving a hole in the export that
        nothing later can fill in.
        """
        if self.client is None or not self.database_available:
            return
        try:
            tags = await asyncio.to_thread(operations.active_experiments)
        except operations.SqlError as err:
            _logger.warning("Could not read running experiments: %s", err)
            return
        if tags:
            self.client.experiment_tags = dict(tags)
            _logger.info("Adopted running experiments: %s", tags)

    async def disconnect(self) -> None:
        """Drop the connection, the address book and the cached ids."""
        if self.client is not None:
            await self.client.disconnect()
        self.client = None
        self.book = None
        # gui.components.pairing caches node ids across reconnects, and
        # node ids are only stable for the life of a server process.
        # Without this a restarted server leaves the GUI holding a stale
        # id: the next read degrades to an empty pairing panel, which
        # looks exactly like "nothing is paired". The import is local to
        # avoid a state -> pairing -> state cycle at module load.
        from reactors_czlab.gui.components import pairing

        pairing.forget_cached_nodes()

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
        """Write one variable. False if it could not be written."""
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
            If the method is not in the address book. A page calling a
            method the server does not have is a bug, not an operational
            failure, so it is not folded into a status string.

        """
        if self.client is None or self.book is None:
            error_message = "not connected"
            raise LookupError(error_message)
        nodeid = self.book.method(reactor, owner, method)
        if nodeid is None:
            error_message = f"No method {method} on {reactor}/{owner}"
            raise LookupError(error_message)
        return await self.client.call_method(nodeid, *args)

    async def call_slow(
        self,
        reactor: str,
        owner: str | None,
        method: str,
        *args: object,
        timeout: float,
    ) -> object:
        """Call a method that takes seconds to minutes to answer.

        Goes through a separate short-lived session; see
        ``OpcClient.call_slow_method`` for why that is required rather
        than merely tidy.

        Raises
        ------
        LookupError
            If the method is not in the address book.

        """
        if self.client is None or self.book is None:
            error_message = "not connected"
            raise LookupError(error_message)
        nodeid = self.book.method(reactor, owner, method)
        if nodeid is None:
            error_message = f"No method {method} on {reactor}/{owner}"
            raise LookupError(error_message)
        return await self.client.call_slow_method(
            nodeid,
            *args,
            timeout=timeout,
        )

    async def start_recording(self) -> None:
        """Begin archiving readings.

        Raises
        ------
        SqlError
            If the database is unavailable. Surfaced so the page can
            tell the operator why the toggle did not take.

        """
        if self.client is None:
            return
        if not self.database_available:
            raise operations.SqlError(self.database_reason)
        await self.client.start_recording()

    async def stop_recording(self) -> None:
        """Stop archiving. Readings stay live on screen."""
        if self.client is None:
            return
        await self.client.stop_recording()


#: The process-wide state. Pages import this.
STATE = AppState()
