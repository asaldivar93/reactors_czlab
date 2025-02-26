"""Sensor node for the OPC UA server."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from asyncua import ua

if TYPE_CHECKING:
    from opcua.common.node import Node
    from reactors_czlab import Sensor

_logger = logging.getLogger("server.opcsensor")

sensor_status = {0: "off", 1: "reading", 2: "calibration"}


class SensorOpc:
    """Sensor node."""

    def __init__(self, sensor: Sensor) -> None:
        """Initialize OPC sensor node."""
        self.sensor = sensor

    async def init_node(self, parent: Node, idx: int) -> None:
        """Add node and variables for the sensor."""
        sensor = self.sensor
        # Add sensor node to reactor
        self.node = await parent.add_object(idx, f"{sensor.id}")
        _logger.info(f"New Actuator node {self.sensor.id}:{self.node}")

        # Add variable to store data from the sensor
        self.value = await self.node.add_variable(idx, "value", 0.0)
        await self.value.set_writable()
        await self.value.write_attribute(
            ua.AttributeIds.Description,
            ua.DataValue(ua.LocalizedText(Text=self.sensor.description)),
        )

        # Add variable to store the status from the sensor
        self.state = await self.node.add_variable(
            idx, "state", 1, varianttype=ua.VariantType.UInt32,
        )
        await self.state.set_writable()
        enum_strings_variant = ua.Variant(
            [ua.LocalizedText(sensor_status[k]) for k in sensor_status],
            ua.VariantType.LocalizedText,
        )
        await self.state.add_property(
            ua.ObjectIds.MultiStateDiscreteType_EnumStrings,
            "EnumStrings",
            enum_strings_variant,
        )

    async def update_value(self) -> None:
        """Get a new reading and update the server."""
        new_val = self.sensor.read()
        await self.value.write_value(new_val)
