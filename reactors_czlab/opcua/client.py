"""Client object to store reactor variables."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from asyncua import Client, ua
from asyncua.client.ua_client import UaClientState

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
#: variable is published by the server and remains readable, but only
#: archival series are subscribed to; configuration and calibration are
#: read on demand.
#: ``curr_value`` is the duty last written to the pin, ``total_volume``
#: the mL a pump has delivered since the server started - both are time
#: series ``run_plots.py`` filters on. The ``cal_*`` and control-config
#: variables are deliberately absent: they change only when a pump is
#: refitted or an operator retunes a controller, so at the 500 ms
#: publishing interval below, across every actuator on every reactor,
#: they would fill the table with constants.
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
        self._recording = False
        self.client: Client | None = None
        self.variables: dict[str, dict] = {}
        self.sensor_vars: dict[str, dict] = {}
        self.actuator_vars: dict[str, dict] = {}
        self.methods: dict[str, dict] = {}
        #: Reactor id -> the name of the experiment currently running on
        #: it. Stamped onto every archived row so a run's data can be
        #: pulled back out later. A reactor absent from this map records
        #: with no experiment name, which is what plain recording does.
        self.experiment_tags: dict[str, str] = {}

    @property
    def state(self) -> UaClientState:
        """The live connection state, as asyncua sees it.

        A plain attribute read rather than a round trip, so a user
        interface can poll it on a timer. Distinguishing CONNECTED from
        RECONNECTING is what lets a page say "the link dropped and is
        recovering on its own" instead of a flat "disconnected" that an
        operator would answer by hitting Retry - which is the one thing
        that must not happen while asyncua is already recovering.
        """
        if self.client is None:
            return UaClientState.DISCONNECTED
        return self.client.uaclient.state

    @property
    def recording(self) -> bool:
        """Whether readings are being archived to the database."""
        return self._recording

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

        # auto_reconnect: the server runs on a Pi that gets rebooted and
        # restarted independently of whatever is watching it. Without
        # this, a dropped link stays dropped until the client process is
        # restarted too.
        self.client = Client(
            url=self.endpoint,
            timeout=self.timeout,
            auto_reconnect=True,
        )
        await self.client.connect()
        self._connected = True
        _logger.info("Connected to %s", self.endpoint)

        try:
            self.sensor_vars = await self.get_sensor_vars()
            await self._read_sensor_descriptions()
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
        # Lower the flag with the task, or `recording` keeps reporting
        # True after the session is gone and a user interface shows a
        # recording badge over a dead connection.
        self._recording = False
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

    async def _read_sensor_descriptions(self) -> None:
        """Batch the Description attribute into the sensor browse model."""
        if not self.sensor_vars:
            return
        nodes = [self.client.get_node(nodeid) for nodeid in self.sensor_vars]
        values = await self.client.read_attributes(
            nodes,
            ua.AttributeIds.Description,
        )
        for info, data_value in zip(
            self.sensor_vars.values(),
            values,
            strict=True,
        ):
            localized = (
                data_value.Value.Value
                if data_value.Value is not None
                else None
            )
            info["description"] = (
                localized.Text if localized is not None else ""
            )

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
        """Subscribe to exactly the variables archived to the database."""
        params = ua.CreateSubscriptionParameters()
        params.RequestedPublishingInterval = 500
        params.RequestedMaxKeepAliveCount = 60
        params.RequestedLifetimeCount = 60
        params.MaxNotificationsPerPublish = 0
        sub = await self.client.create_subscription(params, self)

        # Subscribe what is archived; read every other published value on
        # demand. This keeps the Pi's monitored-item state aligned with the
        # only stream this client consumes continuously.
        vars_to_sub = [
            self.client.get_node(nodeid)
            for nodeid, info in self.variables.items()
            if self.archives(nodeid, info)
        ]
        await sub.subscribe_data_change(vars_to_sub)

        _logger.info(
            "Subscribed to %s variables, %s of them archived",
            len(vars_to_sub),
            len(vars_to_sub),
        )
        if _logger.isEnabledFor(logging.DEBUG):
            names = [
                (await node.read_browse_name()).Name for node in vars_to_sub
            ]
            _logger.debug("Subscribed to variables %s", names)

    async def read_many(self, nodeids: list[str]) -> list[object]:
        """Read many variable values in one OPC Read service call."""
        nodes = [self.client.get_node(nodeid) for nodeid in nodeids]
        return await self.client.read_values(nodes)

    def archives(self, nodeid: str, info: dict) -> bool:
        """Whether a notification for this variable belongs in the table.

        Sensor channels are archived wholesale. Actuator variables are
        filtered to the two that are genuine time series; the rest are
        subscribed for display only.
        """
        if nodeid in self.sensor_vars:
            return True
        if nodeid in self.actuator_vars:
            return info["channel"] in ARCHIVED_ACTUATOR_CHANNELS
        return False

    async def datachange_notification(
        self,
        node: Node,
        val: float,
        data: object,
    ) -> None:
        """Record a new value, and queue it for the database if archiving.

        ``self.variables`` doubles as the live read model a user
        interface polls, so the value is recorded first and the
        decisions about archiving come after.
        """
        nodeid = node.nodeid.to_string()
        info = self.variables.get(nodeid)
        if info is None:
            return

        # Recorded even when it is the error sentinel, so a display can
        # show that a probe is failing right now. It is still never
        # archived - a -0.111 in the data table would be indistinguishable
        # from a reading.
        info["value"] = val
        info["timestamp"] = datetime.now()

        if val == ERROR_VALUE:
            _logger.debug("Skipping error value from %s", nodeid)
            return

        # Without this the queue fills whenever the archiver is stopped,
        # and then logs a dropped-row error on every sample forever.
        if not self._recording:
            return

        if not self.archives(nodeid, info):
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

    async def start_recording(self) -> None:
        """Begin archiving readings to the database.

        Idempotent, so a user interface can call it whenever an
        experiment starts without first checking whether plain recording
        was already running.
        """
        await self.start_psql()
        self._recording = True
        _logger.info("Recording started")

    async def stop_recording(self) -> None:
        """Stop archiving. Readings keep arriving and stay readable.

        The flag is lowered before the task is cancelled so nothing can
        be enqueued in between and sit in the queue until the next
        start.
        """
        self._recording = False
        await self.stop_psql()
        _logger.info("Recording stopped")

    async def run_archiver(self) -> None:
        """Start archiving and block until the task finishes."""
        await self.start_recording()
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
        """Write a Python value to a node, in the node's own type.

        The type is read from the server rather than inferred from the
        Python value: asyncua encodes a bare ``int`` as ``Int64``, and
        the control-config nodes that matter most here - ``method`` and
        ``output_unit`` - are ``UInt32``, which rejects it with
        ``BadTypeMismatch``. Every enum on an actuator would otherwise be
        unwritable from a client.

        Returns True on success so the caller can react to a failure.
        """
        try:
            node = self.client.get_node(nodeid)
            variant_type = await node.read_data_type_as_variant_type()
            await node.write_value(ua.Variant(value, variant_type))
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

    async def call_slow_method(
        self,
        nodeid: str,
        *args: object,
        timeout: float,
    ) -> object:
        """Call a method that takes seconds to minutes to answer.

        Opens a second, short-lived session for the call instead of
        using this client's own.

        This is not an optimisation, it is a correctness requirement.
        asyncua's reconnect supervisor probes the connection every
        ``watchdog_intervall`` (1 s) with a timeout of the same length,
        and a method call that outlasts the probe makes the supervisor
        conclude the link is dead and tear the session down - taking the
        subscription, and so the whole live display, with it. Measured:
        with ``auto_reconnect=True`` a call of 4 s or more kills the
        session regardless of ``timeout``.

        ``calibrate_point`` runs a pump for up to MAX_RUN_SECONDS (600),
        so it can never go through the shared connection. A separate
        session is unaffected - and the run's state lives server-side,
        on the one CalibrationRun the actuator node owns, so a call made
        from another session drives the same run.

        Parameters
        ----------
        nodeid:
            The method's node id. Node ids are stable for the life of
            the server process, so one resolved on this client's browse
            is valid on the temporary session.
        args:
            The method arguments.
        timeout:
            Seconds to allow. Should exceed however long the operator
            asked the method to take.

        Raises
        ------
        ua.UaError
            If the server rejected the call.
        OSError
            If the temporary session could not be opened.

        """
        # No auto_reconnect: this session exists for one call, and the
        # supervisor is exactly what must not run during it.
        client = Client(url=self.endpoint, timeout=timeout)
        await client.connect()
        try:
            node = client.get_node(nodeid)
            parent = await node.get_parent()
            return await parent.call_method(node, *args)
        finally:
            with contextlib.suppress(Exception):
                await client.disconnect()
