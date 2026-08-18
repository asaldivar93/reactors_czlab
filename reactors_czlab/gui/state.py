"""The one connection and the one address book, shared by every page.

The GUI hosts the OPC client and the archiver in its own event loop, so
there is exactly one of each per process. ``OpcClient.variables`` - the
dict its subscription callback already maintains - is the read model;
pages poll it on a timer rather than being pushed to. That is why there
is no callback fan-out here, and no second copy of the values.
"""

from __future__ import annotations

import asyncio
import contextlib
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

#: Startup placeholder until the connected server's published value is read.
DEFAULT_PERIOD = 10.0
SUPERVISOR_SECONDS = 0.1


class AppState:
    """Everything the pages share."""

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        period: float = DEFAULT_PERIOD,
        history_seconds: float = 0.0,
    ) -> None:
        """Store the connection settings; nothing connects yet."""
        self.endpoint = endpoint
        self.period = period
        self.history_seconds = history_seconds
        self.client: OpcClient | None = None
        self.book: AddressBook | None = None
        self.connection_error: str | None = None
        self._database_reason: str | None = (
            None
            if operations.PSYCOPG_AVAILABLE
            else operations.NO_PSYCOPG_REASON
        )
        self.generation = 0
        self._supervisor_task: asyncio.Task | None = None
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
    def writable(self) -> bool:
        """Whether commands may safely be sent to the shared session."""
        return self.connected

    @property
    def database_available(self) -> bool:
        """Whether the driver, database and required schema are usable."""
        return (
            operations.PSYCOPG_AVAILABLE
            and self._database_reason is None
        )

    @property
    def database_reason(self) -> str:
        """Why the database features are unavailable, for display."""
        if not operations.PSYCOPG_AVAILABLE:
            return operations.NO_PSYCOPG_REASON
        return self._database_reason or ""

    @property
    def any_recording(self) -> bool:
        """Whether at least one reactor is being archived."""
        return self.client is not None and self.client.recording

    def is_recording(self, reactor: str) -> bool:
        """Whether one reactor is being archived."""
        return self.client is not None and self.client.is_recording(reactor)

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
        await self.check_database()
        async with self._connect_lock:
            if self.client is not None:
                return

            if self.history_seconds:
                client = OpcClient(
                    self.endpoint,
                    history_seconds=self.history_seconds,
                )
            else:
                client = OpcClient(self.endpoint)
            try:
                await client.connect()
                await client.init_subscriptions()
                book = AddressBook.from_client(client)
                period = await self._read_server_period(client, book)
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
            self.book = book
            self.period = period
            self.generation += 1
            self.connection_error = None
            _logger.info("Connected to %s", self.endpoint)

            await self.adopt_running_experiments()
            await self.restore_recording_state()
            self._supervisor_task = asyncio.create_task(
                self._supervise_connection(client),
            )

    async def check_database(self) -> None:
        """Refresh database reachability and schema compatibility."""
        if not operations.PSYCOPG_AVAILABLE:
            self._database_reason = operations.NO_PSYCOPG_REASON
            return
        try:
            self._database_reason = await asyncio.to_thread(
                operations.check_schema,
            )
        except operations.SqlError as err:
            self._database_reason = str(err)
        if self._database_reason is not None:
            _logger.warning("Database unavailable: %s", self._database_reason)

    async def _supervise_connection(self, client: OpcClient) -> None:
        """Rebrowse after asyncua recovers from a server restart.

        asyncua 2.0.1 recreates its live subscriptions during reconnect, so
        this supervisor must not subscribe again. It rebuilds only the node-id
        mappings that belong to the restarted server process.
        """
        previous = client.state
        while self.client is client:
            await asyncio.sleep(SUPERVISOR_SECONDS)
            current = client.state
            if (
                previous == UaClientState.RECONNECTING
                and current == UaClientState.CONNECTED
            ):
                try:
                    await self._rebrowse(client)
                except Exception:
                    _logger.exception(
                        "Could not rebuild the address book after reconnect",
                    )
                    # Treat the next connected poll as another transition so
                    # a transient browse failure does not strand stale ids for
                    # the rest of the process.
                    previous = UaClientState.RECONNECTING
                    continue
            previous = current

    async def _rebrowse(self, client: OpcClient) -> None:
        """Replace address-derived state after one successful reconnect."""
        await client.refresh_browse()
        book = AddressBook.from_client(client)
        period = await self._read_server_period(client, book)
        self.book = book
        self.period = period
        from reactors_czlab.gui.components import pairing

        pairing.forget_cached_nodes()
        await self.adopt_running_experiments()
        self.generation += 1
        _logger.info(
            "Rebuilt address book after reconnect (generation %s)",
            self.generation,
        )

    @staticmethod
    async def _read_server_period(
        client: OpcClient,
        book: AddressBook,
    ) -> float:
        """Read the authoritative sampling period from one browsed server."""
        nodeid = book.server_variable("sampling_period")
        if nodeid is None:
            error_message = "ServerConfig:sampling_period is not published"
            raise LookupError(error_message)
        [value] = await client.read_many([nodeid])
        return float(value)

    async def read_server_variable(self, name: str) -> object:
        """Read one server-wide configuration variable by browse name."""
        if not self.writable or self.client is None or self.book is None:
            error_message = "connection is not writable"
            raise LookupError(error_message)
        nodeid = self.book.server_variable(name)
        if nodeid is None:
            error_message = f"No server configuration variable {name}"
            raise LookupError(error_message)
        [value] = await self.client.read_many([nodeid])
        return value

    async def call_server_method(
        self,
        method: str,
        *args: object,
    ) -> object:
        """Call one server-wide configuration method by browse name."""
        if not self.writable or self.client is None or self.book is None:
            error_message = "connection is not writable"
            raise LookupError(error_message)
        nodeid = self.book.server_method(method)
        if nodeid is None:
            error_message = f"No server configuration method {method}"
            raise LookupError(error_message)
        return await self.client.call_method(nodeid, *args)

    async def refresh_period(self) -> float:
        """Reload the server's authoritative sampling period."""
        self.period = float(
            await self.read_server_variable("sampling_period"),
        )
        return self.period

    async def set_sampling_period(self, period: float) -> tuple[bool, str]:
        """Request a period change and always adopt the server read-back."""
        try:
            result = await self.call_server_method(
                "set_sampling_period",
                period,
            )
        except Exception:
            # A method can fail after the server has applied it but before the
            # response reaches this client. Make a best effort to reconcile
            # the displayed value before preserving the original exception.
            with contextlib.suppress(Exception):
                await self.refresh_period()
            raise

        await self.refresh_period()
        if not isinstance(result, (list, tuple)) or len(result) != 2:
            error_message = "set_sampling_period returned an invalid response"
            raise ValueError(error_message)
        accepted, message = result
        return (bool(accepted), str(message))

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
        self.client.experiment_tags = dict(tags)
        if tags:
            _logger.info("Adopted running experiments: %s", tags)

    async def restore_recording_state(self) -> None:
        """Resume the per-reactor recording flags persisted by the GUI."""
        if (
            self.client is None
            or self.book is None
            or not self.database_available
        ):
            return
        try:
            saved = await asyncio.to_thread(operations.recording_state)
        except operations.SqlError as err:
            _logger.warning("Could not restore recording state: %s", err)
            return
        for reactor in self.book.reactors:
            if saved.get(reactor, False):
                await self.client.start_recording(reactor)
        if saved:
            _logger.info("Restored recording state: %s", saved)

    async def disconnect(self) -> None:
        """Drop the connection, the address book and the cached ids."""
        if self._supervisor_task is not None:
            self._supervisor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervisor_task
            self._supervisor_task = None
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
        if not self.writable or self.client is None or self.book is None:
            _logger.warning(
                "Refusing write to %s:%s:%s while connection is not writable",
                reactor,
                name,
                channel,
            )
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
        if not self.writable or self.client is None or self.book is None:
            error_message = "connection is not writable"
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
        if not self.writable or self.client is None or self.book is None:
            error_message = "connection is not writable"
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

    async def start_recording(self, reactor: str) -> None:
        """Begin archiving readings from one reactor and persist it.

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
        await asyncio.to_thread(
            operations.set_recording_state,
            reactor,
            True,
        )
        await self.client.start_recording(reactor)

    async def stop_recording(self, reactor: str) -> None:
        """Stop archiving one reactor and persist it."""
        if self.client is None:
            return
        if not self.database_available:
            raise operations.SqlError(self.database_reason)
        await asyncio.to_thread(
            operations.set_recording_state,
            reactor,
            False,
        )
        await self.client.stop_recording(reactor)


#: The process-wide state. Pages import this.
STATE = AppState()
