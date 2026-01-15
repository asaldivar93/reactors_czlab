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
        self.id = sensor.id
        self.sensor = sensor
        self.channels: list[Node] = []

    def __repr__(self) -> str:
        """Print sensor id."""
        return f"SensorOpc(id: {self.sensor.id})"

    def __eq__(self, other: object) -> bool:
        """Test equality by senor id."""
        this = self.sensor.id
        return this == other

    async def init_node(self, parent: Node, idx: int, parent_id: str) -> None:
        """Add node and variables for the sensor."""
        sensor = self.sensor
        # Add sensor node to reactor
        self.node = await parent.add_object(idx, f"{sensor.id}")

        # Add channels to store data from the sensor
        for i, channel in enumerate(sensor.channels):
            var = await self.node.add_variable(
                idx,
                f"{self.id}:{channel.units}",
                0.0,
            )
            await var.set_writable()
            await var.write_attribute(
                ua.AttributeIds.Description,
                ua.DataValue(ua.LocalizedText(Text=channel.description)),
            )
            self.channels.append(var)

    async def update_value(self) -> None:
        """Update the server."""
        for i, channel in enumerate(self.channels):
            new_val = self.sensor.channels[i].value  # Get value in sensor
            await channel.write_value(float(new_val))
            _logger.debug(
                f"Updated {self.id}:{channel} with value {new_val}",
            )
