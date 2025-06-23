"""Sensor node for the OPC UA server."""

from __future__ import annotations

import logging
from dataclasses import astuple
from typing import TYPE_CHECKING

from asyncua import ua

from reactors_czlab.core.utils import Timer
from reactors_czlab.server_info import VERBOSE

if TYPE_CHECKING:
    from asyncua.common.node import Node

    from reactors_czlab.core.sensor import Sensor

_logger = logging.getLogger("server.opcsensor")


class SensorOpc:
    """Sensor node."""

    def __init__(self, sensor: Sensor, timer: Timer) -> None:
        """Initialize OPC sensor node."""
        self.id = sensor.id
        self.sensor = sensor
        self.channels: list[Node] = []
        self._timer = None
        self.timer = timer

    def __repr__(self) -> str:
        """Print sensor id."""
        return f"SensorOpc(id: {self.sensor.id})"

    def __eq__(self, other: object) -> bool:
        """Test equality by senor id."""
        this = self.sensor.id
        return this == other

    @property
    def timer(self) -> Timer:
        """Timer getter."""
        return self._timer

    @timer.setter
    def timer(self, timer: Timer) -> None:
        """Timer setter."""
        if not isinstance(timer, Timer):
            raise TypeError
        timer.add_async_sensor(self)
        self._timer = timer

    async def async_timer_callback(self) -> None:
        """Read sensor and update server."""
        await self.update_value()

    async def init_node(self, parent: Node, idx: int) -> None:
        """Add node and variables for the sensor."""
        sensor = self.sensor

        # Add sensor node to reactor
        self.node = await parent.add_object(idx, f"{sensor.id}")

        # Add channels to store data from the sensor
        for i, channel in enumerate(self.sensor.channels):
            var = await self.node.add_variable(idx, f"var_{i}", 0.0)
            await var.set_writable()
            await var.write_attribute(
                ua.AttributeIds.Description,
                ua.DataValue(ua.LocalizedText(Text=channel.description)),
            )
            self.channels.append(var)

        # Add an array to hold calibration status
        status = list(astuple(self.sensor.calibration_status))
        self.calibration_array = await self.node.add_variable(
            idx,
            "calibration_status",
            ua.Variant(status, ua.VariantType.Float),
        )
        await self.calibration_array.set_writable()

        async def calibrate(parent, cp: str, value: float) -> None:
            self.sensor.write_calibration(cp, value)
            status = list(astuple(self.sensor.calibration_status))
            await self.calibration_array.write_value(status)

        # Add calibration method
        # Argument 1
        inarg_cp = ua.Argument()
        inarg_cp.Name = "CP"
        inarg_cp.DataType = ua.NodeId(ua.ObjectIds.String)
        inarg_cp.ValueRank = -1
        inarg_cp.ArrayDimensions = []
        inarg_cp.Description = ua.LocalizedText("Calibration Point")
        # Argument 2
        inarg_val = ua.Argument()
        inarg_val.Name = "Value"
        inarg_val.DataType = ua.NodeId(ua.ObjectIds.Float)
        inarg_val.ValueRank = -1
        inarg_val.ArrayDimensions = []
        inarg_val.Description = ua.LocalizedText("Calibration Point Value")

        await self.node.add_method(
            idx,
            "write_calibration",
            calibrate,
            [inarg_cp, inarg_val],
            [],
        )

    async def update_value(self) -> None:
        """Update the server."""
        for i, channel in enumerate(self.channels):
            new_val = self.sensor.channels[i].value
            if not isinstance(new_val, float | int):
                raise TypeError
            await channel.write_value(float(new_val))
            if VERBOSE:
                _logger.debug(f"Updated {channel} with value {new_val}")
