"""Client object to store reactor variables."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from asyncua import Client
    from asyncua.common import Node

_logger = logging.getLogger("client.client")


class ReactorOpcClient:
    """Class used to subcribe to server updates."""

    def __init__(self, identifier: str) -> None:
        """Initialize."""
        self.id = identifier

    async def connect_nodes(self, client: Client, variables: dict) -> None:
        """Subcribe to the variables in the reactor.

        Inputs:
        ----
        client: Client
            The opc client instance that handles the connection
        variables: dict
            A dictionary where the keys are the nodeids of the variable and the
            items a dictionary with the info used to commit to the sql database
        """
        self.variables = variables
        # Get all the variable
        self.vars = [client.get_node(nodeid) for nodeid in variables]
        await self._init_sub(client)

    async def _init_sub(self, client: Client) -> None:
        """Create a subcription to the variables."""
        sub = await client.create_subscription(500, self)
        await sub.subscribe_data_change(self.vars)

    def datachange_notification(
        self, node: Node, val: float, data: object
    ) -> None:
        """Commit to the sql database."""
        nodeid = node.nodeid.to_string()
        new_data = self.variables[nodeid]
        new_data["value"] = val
        new_data["datetime"] = datetime.now().isoformat()
        _logger.debug(f"{self.id}: {new_data}")
        # commit to sql database
