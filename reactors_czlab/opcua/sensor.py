"""Sensor node for the OPC UA server."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from asyncua import ua, uamethod

if TYPE_CHECKING:
    from asyncua.common.node import Node

    from reactors_czlab.core.sensor import Sensor

_logger = logging.getLogger("server.opcsensor")

#: Reported by read_calibration_status when the sensor has no
#: calibration points to read. Matches what the base Sensor's
#: write_calibration reports, so a client tells the two apart from a
#: real status by the same word either way.
UNSUPPORTED_STATUS = "unsupported"


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
        for index, channel in enumerate(sensor.channels):
            var = await self.node.add_variable(
                idx,
                f"{self.id}:{channel.units}",
                0.0,
            )
            await var.write_attribute(
                ua.AttributeIds.Description,
                ua.DataValue(ua.LocalizedText(Text=channel.description)),
            )
            # set_pairing takes the channel's *index*, but browsing only
            # yields its name, so a client had no way to work out what
            # to pass. Published as a Property rather than a Variable so
            # OpcClient.match_tree skips it and it never reaches the
            # data table.
            await var.add_property(idx, "ChannelIndex", index)
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

        @uamethod
        async def read_calibration_status(
            parent: Node,
            cal_point: float,
        ) -> tuple[str, float, float, float]:
            """Report what a calibration point currently holds."""
            status = await self.sensor.read_calibration_status(cal_point)
            if status is None:
                # Either the sensor has no calibration points at all
                # (biomass, simulated) or the read failed; both are
                # already logged where they happened.
                return (UNSUPPORTED_STATUS, 0.0, 0.0, 0.0)
            return (
                status.text,
                status.quality,
                status.value,
                status.process_value,
            )

        inarg_point = ua.Argument()
        inarg_point.Name = "Cal_point"
        inarg_point.DataType = ua.NodeId(ua.ObjectIds.Float)

        status_args = []
        for name in ("Status", "Quality", "Value", "Process_value"):
            arg = ua.Argument()
            arg.Name = name
            arg.DataType = ua.NodeId(
                ua.ObjectIds.String if name == "Status" else ua.ObjectIds.Float,
            )
            status_args.append(arg)

        # On demand, not published. CP status registers are readable at
        # administrator or specialist level only, so every read raises
        # and drops the sensor's operator level on the same RS485 bus
        # the control loop uses - not something to do on a publish
        # cycle for data that changes only at a calibration.
        await self.node.add_method(
            idx,
            f"{self.id}:read_calibration_status",
            read_calibration_status,
            [inarg_point],
            status_args,
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
