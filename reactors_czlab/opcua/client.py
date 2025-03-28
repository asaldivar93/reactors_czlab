"""Client object to store reactor variables."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from asyncua import Client
    from asyncua.common import Node

    from reactors_czlab.core.utils import PhysicalInfo

_logger = logging.getLogger("client.client")


class ReactorOpcClient:
    """Class used to subcribe to server updates."""

    def __init__(self, identifier: str, experiment: str) -> None:
        """Instance the reactor client.

        Parameters
        ----------
        identifier: str
            A unique identifier for the reactor. This id should match
            the id in the server.
        experiment: str
            The experiment id associated to this instance.

        """
        self.id = identifier
        self.experiment = experiment

    async def connect_nodes(
        self,
        client: Client,
        variables: dict[str, PhysicalInfo],
    ) -> None:
        """Subcribe to the variables in the reactor.

        Inputs:
        ----
        client: Client
            The opc client instance that handles the connection
        variables: dict[str, PhysicalInfo]
            A dictionary where the keys are the nodeids of the variable and the
            items are a core.utils.PhysicalInfo instance
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
        self,
        node: Node,
        val: float,
        data: object,
    ) -> None:
        """Commit to the sql database."""
        nodeid = node.nodeid.to_string()
        data = self.variables[nodeid]
        data.channels[0].value = val
        timestamp = datetime.now().isoformat()
        _logger.debug(f"Data change in {self.id} - {data.model}: {val}")

        # commit to sql database
