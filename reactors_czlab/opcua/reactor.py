"""Reactor node for the OPC UA server."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from reactors_czlab import Actuator, Reactor, Sensor
from reactors_czlab.opcua.actuator import ActuatorOpc
from reactors_czlab.opcua.sensor import SensorOpc

if TYPE_CHECKING:
    from asyncua import Server
    from asyncua.common.node import Node


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
        self.reactor = Reactor(identifier, volume, sensors, actuators)

    def create_opc_nodes(self, server: Server, idx: int) -> None:
        """Create the Reactor nodes and add the sensor and actuator nodes."""
        self.idx = idx
        # Create a Reactor object in the server
        self.node = await server.nodes.objects.add_object(idx, self.id)
        # Add sensor nodes to the server
        self.sensor_nodes = [
            SensorOpc(sensor, self.node, idx) for sensor in self.reactor.sensors
        ]
        # Add actuator nodes to the server
        self.actuator_nodes = [
            ActuatorOpc(actuator, self.node, idx) for actuator in self.reactor.actuators
        ]

    async def update_sensors(self) -> None:
        for sensor in self.sensor_nodes:
            await sensor.update_value()
