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

    async def __init__(self, sensor: Sensor, parent_node: Node, idx: int) -> None:
        """Initialize OPC sensor node."""
        self.sensor = sensor
        self.node = await parent_node.add_object(idx, f"{sensor.id}")
        self.value = self.node.add_variable(
            idx, f"{sensor.id}", 0.0, ua.VariantType.Float
        )
        self.value.set_attribute(
            ua.AttributeIds.InstrumentRange,
            ua.Range(sensor.lb, sensor.ub)
        )
        self.value.set_attribute(
            ua.AttributeIds.EURange,
            ua.Range(sensor.EUlb, sensor.EUub)
        )
        self.value.set_attribute(
            ua.AttributeIds.EngineeringUnits,
            ua.EUInformation(sensor.unit_symbol, sensor.unit_description, "none")
        )

    async def update_value(self) -> None:
        """Get a new reading and update the server."""
        new_val = self.sensor.read()
        await self.value.write_value(new_val)
