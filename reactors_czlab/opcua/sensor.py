"""Sensor node for the OPC UA server."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from asyncua import ua, uamethod

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

    async def init_node(self, parent: Node, idx: int, parent_id: str) -> None:
        """Add node and variables for the sensor."""
        sensor = self.sensor
        # Add sensor node to reactor
        self.node = await parent.add_object(idx, f"{sensor.id}")
        bnp = await parent.read_browse_name()
        bns = await self.node.read_browse_name()
        _logger.info("In node %s added %s", bnp.Name, bns.Name)

        # Add channels to store data from the sensor
        for channel in sensor.channels:
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

        @uamethod
        async def write_calibration(
            parent: Node,
            cal_point: float,
            cal_value: float,
        ) -> tuple[str, float, float]:
            """One point calibration of Hamilton sensors."""
            return await self.sensor.write_calibration(cal_point, cal_value)

        inarg_calp = ua.Argument()
        inarg_calp.Name = "Cal_point"
        inarg_calp.DataType = ua.NodeId(ua.ObjectIds.Float)

        inarg_calv = ua.Argument()
        inarg_calv.Name = "Cal_value"
        inarg_calv.DataType = ua.NodeId(ua.ObjectIds.Float)

        outarg1 = ua.Argument()
        outarg1.Name = "Status"
        outarg1.DataType = ua.NodeId(ua.ObjectIds.String)

        outarg2 = ua.Argument()
        outarg2.Name = "Quality"
        outarg2.DataType = ua.NodeId(ua.ObjectIds.Float)

        outarg3 = ua.Argument()
        outarg3.Name = "Value"
        outarg3.DataType = ua.NodeId(ua.ObjectIds.Float)

        await self.node.add_method(
            idx,
            f"{self.id}:calibration",
            write_calibration,
            [inarg_calp, inarg_calv],
            [outarg1, outarg2, outarg3],
        )

    async def update_value(self) -> None:
        """Publish the latest reading of every channel to the server."""
        for node, channel in zip(
            self.channels,
            self.sensor.channels,
            strict=True,
        ):
            await node.write_value(float(channel.value))
            _logger.debug(
                "Updated %s:%s with value %s",
                self.id,
                channel.units,
                channel.value,
            )
