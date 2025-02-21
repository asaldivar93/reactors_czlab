"""Reactor node for the OPC UA server."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from reactors_czlab import Actuator, Reactor, Sensor
from reactors_czlab.opcua.actuator import ActuatorOpc
from reactors_czlab.opcua.sensor import SensorOpc

if TYPE_CHECKING:
    from asyncua import Server


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

    async def add_opc_nodes(self, server: Server, idx: int) -> None:
        """Create the Reactor nodes and add the sensor and actuator nodes."""
        self.idx = idx
        sensors = self.reactor.sensors
        actuators = self.reactor.actuators

        # Create a Reactor object in the server
        self.node = await server.nodes.objects.add_object(idx, self.id)

        # Add sensor nodes to the server
        self.sensor_nodes = [SensorOpc(sensor) for sensor in sensors]
        for sensor in self.sensor_nodes:
            await sensor.add_node(self.node, idx)

        # Add actuator nodes to the server
        self.actuator_nodes = [ActuatorOpc(actuator) for actuator in actuators]
        for actuator in self.actuator_nodes:
            await actuator.add_node(self.node, idx)

    async def update_sensors(self) -> None:
        """Read each sensor and send the value to the server."""
        for sensor in self.sensor_nodes:
            await sensor.update_value()
