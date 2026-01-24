"""Client object to store reactor variables."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING

from asyncua import Client, ua

from reactors_czlab.sql.operations import store_data

if TYPE_CHECKING:
    from asyncua.common import Node


_logger = logging.getLogger("client.client")

SENSORS_NODE_RE = re.compile(r"^R\d+:sensors$")
ACTUATORS_NODE_RE = re.compile(r"^R\d+:actuators$")
REACTORS_NODE_RE = r"^R\d+:"


class OpcClient:
    def __init__(self, endpoint: str, timeout: float = 5.0):
        """endpoint: opc.tcp://host:port/..."""
        self.endpoint = endpoint
        self.timeout = timeout
        self._connected = False
        self._queue = asyncio.Queue(maxsize=1000)
        self.variables: dict[str, dict] = {}
        self.client: Client
        self.sensor_vars: dict[str, dict]
        self.actuator_vars: dict[str, dict]
        self.methods: dict[str, dict]

    async def connect(self):
        """Connect to OPC-UA server."""
        if self._connected:
            return

        try:
            self.client = Client(url=self.endpoint)
            async with self.client:
                self._connected = True
                _logger.info("Connected to %s", self.endpoint)
                self.sensor_vars = await self.get_sensor_vars()
                self.actuator_vars = await self.get_actuator_vars()
                self.variables.update(self.sensor_vars)
                self.variables.update(self.actuator_vars)
                self.methods = await self.get_methods()

        except Exception as e:
            _logger.exception("Failed to connect to OPC-UA server: %s", e)
            raise

    async def get_sensor_vars(self):
        """Get a dict of {Nodeid: info} for sensors."""
        objects = self.client.nodes.objects
        return await self.match_tree(objects, SENSORS_NODE_RE)

    async def get_actuator_vars(self):
        """Get a dict of {Nodeid: info} for actuators."""
        objects = self.client.nodes.objects
        return await self.match_tree(objects, ACTUATORS_NODE_RE)

    async def match_tree(self, objects: Node, regular_expression: re.Pattern):
        """Find the children variables of nodes followith a re."""
        matches = []
        variables = {}

        async def find_nodes(node: Node):
            """Recursion to find Nodes matchin re."""
            bn = await node.read_browse_name()
            name = bn.Name
            children = await node.get_children()

            if regular_expression.match(name):
                matches.append(node)

            for child in children:
                await find_nodes(child)

        async def find_vars(node: Node):
            """Recursion to find child vars of a node."""
            node_id = node.nodeid.to_string()
            name = (await node.read_browse_name()).Name
            node_class = (await node.read_node_class()).name
            children = await node.get_children()

            if node_class == "Variable":
                info = name.split(":")
                with contextlib.suppress(IndexError):
                    variables[node_id] = {
                        "reactor": info[0],
                        "name": info[1],
                        "channel": info[2],
                        "value": 0.0,
                    }

            for child in children:
                await find_vars(child)

        await find_nodes(objects)
        for m in matches:
            await find_vars(m)

        return variables

    async def get_methods(self):
        """Find all methods associated to reactor nodes."""
        methods = {}

        async def find_methods(node: Node):
            """Find methods of a node."""
            node_id = node.nodeid.to_string()
            name = (await node.read_browse_name()).Name
            node_class = (await node.read_node_class()).name
            children = await node.get_children()

            if node_class == "Method" and bool(
                re.search(REACTORS_NODE_RE, name),
            ):
                info = name.split(":")
                methods[node_id] = {
                    "reactor": info[0],
                    "name": info[1],
                }

            for child in children:
                await find_methods(child)

        objects = self.client.nodes.objects
        await find_methods(objects)

        return methods

    async def init_subcriptions(self) -> None:
        """Create a subcription to the variables."""
        params = ua.CreateSubscriptionParameters()
        params.RequestedPublishingInterval = 500
        params.RequestedMaxKeepAliveCount = 60
        params.RequestedLifetimeCount = 60
        params.MaxNotificationsPerPublish = 0
        sub = await self.client.create_subscription(params, self)
        vars_to_sub = [
            self.client.get_node(nodeid) for nodeid in self.sensor_vars
        ]
        vars_to_sub.extend(
            [
                self.client.get_node(nodeid)
                for nodeid, info in self.actuator_vars.items()
                if info["channel"] == "curr_value"
            ],
        )
        await sub.subscribe_data_change(vars_to_sub)

    async def datachange_notification(
        self,
        node: Node,
        val: float,
        data: object,
    ) -> None:
        """On data change queue new vals to the sql database."""
        nodeid = node.nodeid.to_string()
        info = self.variables.get(nodeid, None)
        if info is not None:
            info["value"] = val
            info["timestamp"] = datetime.now()
            await self._queue.put((nodeid, info))
            _logger.debug(
                f"Data change in {nodeid}:{info}",
            )

    async def commit_to_db(self):
        """Commit ot sql database."""
        while True:
            nodeid, info = await self._queue.get()
            try:
                store_data(nodeid, info)
            finally:
                self._queue.task_done()

    async def write(self, nodeid: str, value):
        """Write a Python value to a node.

        If the node expects a Variant type, asyncua will attempt conversion.
        """
        try:
            node = self.client.get_node(nodeid)
            await node.write_value(value)
        except Exception as e:
            _logger.exception(
                "Write failed for %s <- %r : %s",
                nodeid,
                value,
                e,
            )

    async def call_method(self, nodeid: str, *args):
        """Call a method from its nodeid."""
        try:
            node = self.client.get_node(nodeid)
            parent = await node.get_parent()
            return await parent.call_method(nodeid, *args)
        except Exception as e:
            _logger.exception(
                "Method call failed %s.%s : %s",
                parent.nodeid.to_string(),
                nodeid,
                e,
            )
