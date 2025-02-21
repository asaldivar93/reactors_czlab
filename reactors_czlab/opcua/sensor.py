"""Sensor node for the OPC UA server."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from asyncua import ua

if TYPE_CHECKING:
    from opcua.common.node import Node
    from reactors_czlab import Sensor


class SensorOpc:
    """Sensor node."""

    def __init__(self, sensor: Sensor) -> None:
        """Initialize OPC sensor node."""
        self.sensor = sensor

    async def add_node(self, parent: Node, idx: int) -> None:
        """Add node and variables for the sensor."""
        sensor = self.sensor
        self.node = await parent.add_object(idx, f"{sensor.id}")
        print(f"Node added for sensor {sensor.id}")
        self.value = await self.node.add_variable(
            idx, "value", 0.0
        )
        await self.value.set_writable()
        # self.value.write_attribute(
        #     ua.AttributeIds.InstrumentRange, ua.Range(sensor.lb, sensor.ub)
        # )
        # self.value.write_attribute(
        #     ua.AttributeIds.EURange, ua.Range(sensor.EUlb, sensor.EUub)
        # )
        # self.value.write_attribute(
        #     ua.AttributeIds.EngineeringUnits,
        #     ua.EUInformation(sensor.unit_symbol, sensor.unit_description, "none"),
        # )

    async def update_value(self) -> None:
        """Get a new reading and update the server."""
        new_val = self.sensor.read()
        await self.value.write_value(new_val)
