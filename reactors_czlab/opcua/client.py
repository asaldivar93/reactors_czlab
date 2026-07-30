"""Client object to store reactor variables."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from asyncua import Client, ua

from reactors_czlab.core.data import ERROR_VALUE
from reactors_czlab.sql.operations import (
    SqlError,
    connect_to_db,
    store_data,
)

if TYPE_CHECKING:
    from types import TracebackType

    from asyncua.common import Node

_logger = logging.getLogger("client")

SENSORS_NODE_RE = re.compile(r"^R\d+:sensors$")
ACTUATORS_NODE_RE = re.compile(r"^R\d+:actuators$")
REACTORS_NODE_RE = re.compile(r"^R\d+:")

QUEUE_MAXSIZE = 1000
#: Browse names split into exactly reactor:name:channel.
NAME_PARTS = 3

#: Actuator channels archived to the ``data`` table. Every actuator
#: variable is published by the server and readable by any OPC client;
#: only the ones named here are subscribed to and stored.
#: ``curr_value`` is the duty last written to the pin, ``total_volume``
#: the mL a pump has delivered since the server started - both are time
#: series ``run_plots.py`` filters on. The ``cal_*`` variables are
#: deliberately absent: they change only when a pump is refitted, so at
#: the 500 ms publishing interval below, across every actuator on every
#: reactor, they would fill the table with constants.
ARCHIVED_ACTUATOR_CHANNELS = frozenset({"curr_value", "total_volume"})


class OpcClient:
    """Browse an OPC-UA server, subscribe to it and archive the data."""

    def __init__(self, endpoint: str, timeout: float = 5.0) -> None:
        """Initialize the client.

        Parameters
        ----------
        endpoint:
            opc.tcp://host:port/...
        timeout:
            Seconds before a request to the server is abandoned

        """
        self.endpoint = endpoint
        self.timeout = timeout
        self._connected = False
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._db_task: asyncio.Task | None = None
        self.client: Client | None = None
        self.variables: dict[str, dict] = {}
        self.sensor_vars: dict[str, dict] = {}
        self.actuator_vars: dict[str, dict] = {}
        self.methods: dict[str, dict] = {}
        #: reactor id -> the running experiment that owns it. Consulted
        #: when a row is enqueued, so several experiments over disjoint
        #: reactor sets need nothing more than this dict.
        self.experiment_tags: dict[str, str] = {}

    async def __aenter__(self) -> OpcClient:
        """Connect on entry."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Disconnect on exit."""
        await self.disconnect()

    async def connect(self) -> None:
        """Connect to the OPC-UA server and browse its address space.

        The connection stays open until disconnect() is called.
        """
        if self._connected:
            return

        self.client = Client(url=self.endpoint, timeout=self.timeout)
        await self.client.connect()
        self._connected = True
        _logger.info("Connected to %s", self.endpoint)

        try:
            self.sensor_vars = await self.get_sensor_vars()
            self.actuator_vars = await self.get_actuator_vars()
            self.variables = {**self.sensor_vars, **self.actuator_vars}
            self.methods = await self.get_methods()
            self.mappings = {
                "sensor_vars": self.sensor_vars,
                "actuator_vars": self.actuator_vars,
                "methods": self.methods,
            }
        except Exception:
            _logger.exception("Failed to browse %s", self.endpoint)
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        """Stop the archiver task and close the connection."""
        await self.stop_psql()
        if not self._connected or self.client is None:
            return
        self._connected = False
        with contextlib.suppress(Exception):
            await self.client.disconnect()
        _logger.info("Disconnected from %s", self.endpoint)

    async def get_sensor_vars(self) -> dict[str, dict]:
        """Get a dict of {nodeid: info} for sensors."""
        objects = self.client.nodes.objects
        return await self.match_tree(objects, SENSORS_NODE_RE)

    async def get_actuator_vars(self) -> dict[str, dict]:
        """Get a dict of {nodeid: info} for actuators."""
        objects = self.client.nodes.objects
        return await self.match_tree(objects, ACTUATORS_NODE_RE)

    async def match_tree(
        self,
        objects: Node,
        regular_expression: re.Pattern,
    ) -> dict[str, dict]:
        """Find the variables below every node matching a regex."""
        matches: list[Node] = []
        variables: dict[str, dict] = {}

        async def find_nodes(node: Node) -> None:
            """Recurse to find nodes matching the regex."""
            name = (await node.read_browse_name()).Name
            if regular_expression.match(name):
                matches.append(node)

            for child in await node.get_children():
                await find_nodes(child)

        async def find_vars(node: Node) -> None:
            """Recurse to find the child variables of a node."""
            node_id = node.nodeid.to_string()
            name = (await node.read_browse_name()).Name
            node_class = (await node.read_node_class()).name

            if node_class == "Variable":
                info = name.split(":")
                if len(info) >= NAME_PARTS:
                    variables[node_id] = {
                        "reactor": info[0],
                        "name": info[1],
                        "channel": info[2],
                        "value": 0.0,
                    }
                else:
                    _logger.debug("Skipping variable with name %r", name)

            for child in await node.get_children():
                await find_vars(child)

        await find_nodes(objects)
        for match in matches:
            await find_vars(match)

        return variables

    async def get_methods(self) -> dict[str, dict]:
        """Find all methods associated to reactor nodes."""
        methods: dict[str, dict] = {}

        async def find_methods(node: Node) -> None:
            """Find the methods of a node and its children."""
            node_id = node.nodeid.to_string()
            name = (await node.read_browse_name()).Name
            node_class = (await node.read_node_class()).name

            if node_class == "Method" and REACTORS_NODE_RE.search(name):
                info = name.split(":")
                methods[node_id] = {"reactor": info[0], "name": info[1:]}

            for child in await node.get_children():
                await find_methods(child)

        await find_methods(self.client.nodes.objects)
        return methods

    async def init_subscriptions(self) -> None:
        """Create a subscription to the sensor and actuator variables."""
        params = ua.CreateSubscriptionParameters()
        params.RequestedPublishingInterval = 500
        params.RequestedMaxKeepAliveCount = 60
        params.RequestedLifetimeCount = 60
        params.MaxNotificationsPerPublish = 0
        sub = await self.client.create_subscription(params, self)

        # Everything published is subscribed: the GUI reads its live
        # values off this dict, including the control config and the
        # calibration, which are not archived. What reaches the database
        # is decided by archives(), at enqueue time. Rebuilt here (not
        # just relied on from connect()) so it reflects sensor_vars/
        # actuator_vars as they stand right before subscribing.
        self.variables = {**self.sensor_vars, **self.actuator_vars}
        vars_to_sub = [
            self.client.get_node(nodeid) for nodeid in self.variables
        ]
        await sub.subscribe_data_change(vars_to_sub)

        names = [(await node.read_browse_name()).Name for node in vars_to_sub]
        _logger.info("Subscribed to variables %s", names)

    def archives(self, nodeid: str, info: dict) -> bool:
        """Whether a variable's changes belong in the ``data`` table.

        Sensor channels are archived wholesale. Of the actuator
        variables only ``ARCHIVED_ACTUATOR_CHANNELS`` are: the ``cal_*``
        variables move only on a refit, so at the 500 ms publishing
        interval, across every actuator on every reactor, they would
        fill the table with constants.
        """
        if nodeid not in self.actuator_vars:
            return True
        return info["channel"] in ARCHIVED_ACTUATOR_CHANNELS

    @property
    def recording(self) -> bool:
        """Whether the archiver task is running."""
        return self._db_task is not None

    async def start_recording(self) -> None:
        """Begin archiving queued readings."""
        await self.start_psql()

    async def stop_recording(self) -> None:
        """Stop archiving. Live values keep updating."""
        await self.stop_psql()

    async def datachange_notification(
        self,
        node: Node,
        val: float,
        data: object,
    ) -> None:
        """Update the live value and, while recording, queue it.

        The in-memory value always updates so the GUI has a live view
        of the plant regardless of whether anything is archiving.
        Enqueuing is gated on ``recording`` and ``archives()``: nothing
        drains the queue when the archiver is stopped, so enqueuing
        anyway fills the 1000 slot buffer and then logs an error every
        sample forever.
        """
        nodeid = node.nodeid.to_string()
        info = self.variables.get(nodeid)
        if info is None:
            return
        if val == ERROR_VALUE:
            # The server could not read the device; do not archive it.
            # The live value is still updated: the GUI must be able to
            # show that a probe is failing.
            _logger.debug("Skipping error value from %s", nodeid)
            info["value"] = val
            info["timestamp"] = datetime.now()
            return

        info["value"] = val
        info["timestamp"] = datetime.now()

        if not self.recording or not self.archives(nodeid, info):
            return

        row = dict(info)
        row["experiment_name"] = self.experiment_tags.get(info["reactor"])
        try:
            self._queue.put_nowait((nodeid, row))
        except asyncio.QueueFull:
            _logger.error(
                "Database queue is full (%s items), dropping %s",
                QUEUE_MAXSIZE,
                nodeid,
            )
        else:
            _logger.debug("Data change in %s: %s", nodeid, row)

    async def start_psql(self) -> None:
        """Start the task that archives queued readings."""
        if self._db_task is not None:
            return
        self._db_task = asyncio.create_task(self.commit_to_db())
        _logger.info("Database task created")

    async def run_archiver(self) -> None:
        """Start archiving and block until the task finishes."""
        await self.start_psql()
        if self._db_task is not None:
            await self._db_task

    async def stop_psql(self) -> None:
        """Stop the archiver task."""
        if self._db_task is None:
            return
        self._db_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._db_task
        self._db_task = None

    async def commit_to_db(self) -> None:
        """Drain the queue into the sql database.

        Holds one connection for the lifetime of the task and reconnects if
        it goes away. A row that cannot be stored is logged and dropped: it
        must not take the archiver down with it.
        """
        connection = await asyncio.to_thread(connect_to_db)
        try:
            while True:
                nodeid, info = await self._queue.get()
                try:
                    await asyncio.to_thread(
                        store_data,
                        connection,
                        nodeid,
                        info,
                    )
                except SqlError:
                    _logger.exception("Could not store %s: %s", nodeid, info)
                    connection = await self._reconnect_db(connection)
                finally:
                    self._queue.task_done()
        finally:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(connection.close)

    async def _reconnect_db(self, connection: Any) -> Any:
        """Replace a connection that may have gone bad."""
        if not connection.closed:
            return connection
        _logger.warning("Database connection lost, reconnecting")
        return await asyncio.to_thread(connect_to_db)

    async def write(self, nodeid: str, value: object) -> bool:
        """Write a Python value to a node.

        Returns True on success so the caller can react to a failure.
        """
        try:
            node = self.client.get_node(nodeid)
            await node.write_value(value)
        except (ua.UaError, OSError):
            _logger.exception("Write failed for %s <- %r", nodeid, value)
            return False
        return True

    async def call_method(self, nodeid: str, *args: object) -> object:
        """Call a method from its nodeid.

        Raises
        ------
        ua.UaError
            If the server rejected the call. Callers need to know.

        """
        node = self.client.get_node(nodeid)
        parent = await node.get_parent()
        return await parent.call_method(node, *args)
