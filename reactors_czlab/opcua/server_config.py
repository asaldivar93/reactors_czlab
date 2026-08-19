"""Server-wide configuration exposed through OPC UA."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from asyncua import ua, uamethod

if TYPE_CHECKING:
    from asyncua import Server
    from asyncua.common import Node

    from reactors_czlab.core.reactor import Reactor

_logger = logging.getLogger("server.opcconfig")

SERVER_CONFIG_NAME = "ServerConfig"


def _argument(name: str, data_type: int) -> ua.Argument:
    """Build one OPC method argument declaration."""
    argument = ua.Argument()
    argument.Name = name
    argument.DataType = ua.NodeId(data_type)
    return argument


class ServerConfigOpc:
    """Publish and atomically update configuration shared by all reactors."""

    def __init__(self, reactors: list[Reactor], period: float) -> None:
        """Store the server-owned configuration; nothing is published yet."""
        self.reactors = reactors
        self.period = period
        self._config_lock = asyncio.Lock()
        self.node: Node | None = None
        self.sampling_period_node: Node | None = None
        self.on_state_changed: Callable[[], None] | None = None

    async def init_node(self, server: Server, idx: int) -> None:
        """Create the read-only variable and validated mutation method."""
        self.node = await server.nodes.objects.add_object(
            idx,
            SERVER_CONFIG_NAME,
        )
        self.sampling_period_node = await self.node.add_variable(
            idx,
            f"{SERVER_CONFIG_NAME}:sampling_period",
            self.period,
            ua.VariantType.Double,
        )

        @uamethod
        async def set_sampling_period(
            parent: Node,
            period: float,
        ) -> tuple[bool, str]:
            """Apply one server-wide sampling period."""
            return await self.set_sampling_period(period)

        await self.node.add_method(
            idx,
            f"{SERVER_CONFIG_NAME}:set_sampling_period",
            set_sampling_period,
            [_argument("Seconds", ua.ObjectIds.Double)],
            [
                _argument("Accepted", ua.ObjectIds.Boolean),
                _argument("Message", ua.ObjectIds.String),
            ],
        )

    async def set_sampling_period(self, period: float) -> tuple[bool, str]:
        """Validate, apply and publish a new period under one lock.

        Returns
        -------
        tuple[bool, str]
            Whether the value was accepted and an operator-readable status.

        """
        async with self._config_lock:
            if any(
                reactor.active_autotune_run() is not None
                for reactor in self.reactors
            ):
                message = "sampling period cannot change during active PID autotuning"
                _logger.warning("Rejected sampling-period change: %s", message)
                return (False, message)

            try:
                # Validation happens before mutation on every reactor. Each
                # reactor applies synchronously, so the event loop cannot see
                # a partially updated server.
                for reactor in self.reactors:
                    reactor.update_period(period)
            except (TypeError, ValueError) as err:
                message = str(err)
                _logger.warning("Rejected sampling-period change: %s", message)
                return (False, message)

            changed = period != self.period
            self.period = period
            if changed and self.on_state_changed is not None:
                try:
                    self.on_state_changed()
                except Exception:
                    _logger.exception("Sampling-period checkpoint callback failed")
            if self.sampling_period_node is not None:
                await self.sampling_period_node.write_value(
                    ua.Variant(period, ua.VariantType.Double),
                )
            message = f"sampling period set to {period:g} seconds"
            _logger.info("%s", message)
            return (True, message)
