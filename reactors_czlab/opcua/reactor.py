"""Reactor node for the OPC UA server."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from asyncua import ua

from reactors_czlab.core.reactor import Reactor
from reactors_czlab.opcua.actuator import ActuatorOpc
from reactors_czlab.opcua.sensor import SensorOpc

if TYPE_CHECKING:
    from asyncua import Server

    from reactors_czlab.core.actuator import Actuator
    from reactors_czlab.core.sensor import Sensor

_logger = logging.getLogger("server.opcreactor")

reactor_status = {0: "off", 1: "on", 2: "experiment"}


class ReactorOpc:
    """Reactor node."""

    def __init__(
        self,
        identifier: str,
        volume: float,
        sensors: list[Sensor],
        actuators: list[Actuator],
    ) -> None:
        """Initialize the OPC Reactor node."""
        self.id = identifier
        self.reactor = Reactor(identifier, volume, sensors, actuators)

    async def init_node(self, server: Server, idx: int) -> None:
        """Create the Reactor nodes and add the sensor and actuator nodes."""
        self.idx = idx
        sensors = self.reactor.sensors
        actuators = self.reactor.actuators

        # Create a Reactor object in the server
        self.node = await server.nodes.objects.add_object(idx, self.id)
        _logger.info(f"New Reactor node {self.reactor.id}:{self.node}")

        # Add variable to store the status from the reactor
        self.state = await self.node.add_variable(
            idx,
            "state",
            0,
            varianttype=ua.VariantType.UInt32,
        )
        await self.state.set_writable()
        enum_strings_variant = ua.Variant(
            [ua.LocalizedText(reactor_status[k]) for k in reactor_status],
            ua.VariantType.LocalizedText,
        )
        await self.state.add_property(
            ua.ObjectIds.MultiStateDiscreteType_EnumStrings,
            "EnumStrings",
            enum_strings_variant,
        )

        # Add sensor nodes to the server
        await self._add_sensor_nodes(sensors)

        # Add actuator nodes to the server
        await self._add_actuator_nodes(server, actuators)

    async def _add_sensor_nodes(self, sensors: dict) -> None:
        """Add sensor nodes."""
        self.sensor_nodes = [SensorOpc(sensor) for sensor in sensors.values()]
        for sensor in self.sensor_nodes:
            await sensor.init_node(self.node, self.idx)

    async def _add_actuator_nodes(
        self, server: Server, actuators: dict
    ) -> None:
        """Add actuator nodes."""
        self.actuator_nodes = [
            ActuatorOpc(actuator) for actuator in actuators.values()
        ]
        for actuator in self.actuator_nodes:
            await actuator.init_node(server, self.node, self.idx)

    async def update_sensors(self) -> None:
        """Read each sensor and send the value to the server."""
        for sensor in self.sensor_nodes:
            await sensor.update_value()

    def update_actuators(self) -> None:
        """Update the state of the actuators after taking the sensor readings."""
        for actuator in self.reactor.actuators.values():
            actuator.write_output()
