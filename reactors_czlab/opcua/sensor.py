"""Sensor node for the OPC UA server."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from asyncua import ua

if TYPE_CHECKING:
    from asyncua.common.node import Node

    from reactors_czlab.core.sensor import Sensor

_logger = logging.getLogger("server.opcsensor")


class SensorOpc:
    """Sensor node."""

    def __init__(self, sensor: Sensor) -> None:
        """Initialize OPC sensor node."""
        self.sensor = sensor
        self.channels: list[Node] = []

    async def init_node(self, parent: Node, idx: int) -> None:
        """Add node and variables for the sensor."""
        sensor = self.sensor

        # Add sensor node to reactor
        self.node = await parent.add_object(idx, f"{sensor.id}")
        _logger.debug(f"New Sensor node {self.sensor.id}:{self.node}")

        # Add channels to store data from the sensor
        for i, channel in enumerate(self.sensor.channels):
            var = await self.node.add_variable(idx, f"var_{i}", 0.0)
            await var.set_writable()
            await var.write_attribute(
                ua.AttributeIds.Description,
                ua.DataValue(ua.LocalizedText(Text=channel.description)),
            )
            self.channels.append(var)
            _logger.debug(
                f"New variable in {self.sensor.id}:{self.node} - {channel}",
            )

    async def update_value(self) -> None:
        """Get a new reading and update the server."""
        for i, channel in enumerate(self.channels):
            new_val = self.sensor.channels[i].value
            if not isinstance(new_val, float | int):
                raise TypeError
            await channel.write_value(float(new_val))
