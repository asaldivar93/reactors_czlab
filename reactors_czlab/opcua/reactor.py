"""Reactor node for the OPC UA server."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from asyncua import ua

from reactors_czlab.core.reactor import Reactor
from reactors_czlab.core.utils import Timer
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
        timer: float,
    ) -> None:
        """Initialize the OPC Reactor node."""
        self.id = identifier
        self.base_timer: Timer = Timer(timer)
        self.reactor = Reactor(
            identifier,
            volume,
            sensors,
            actuators,
            self.base_timer,
        )
        self.sensor_nodes: list[SensorOpc] = []
        self.actuator_nodes: list[ActuatorOpc] = []
        _logger.debug(f"Creating nodes for {self.id}")
        self.timers_dict = {self.base_timer.interval: self.base_timer}
        for sensor in sensors:
            interval = sensor.sensor_info.sample_interval
            new_timer = self.timers_dict.get(interval, None)
            if new_timer is None:
                new_timer = Timer(interval)
                self.timers_dict.update({interval: new_timer})
        self.create_child_nodes()
        for actuator in self.actuator_nodes:
            actuator.sensors = sensors

    def create_child_nodes(self):
        sensors = self.reactor.sensors
        actuators = self.reactor.actuators
        for sensor in sensors.values():
            interval = sensor.sensor_info.sample_interval
            timer = self.timers_dict[interval]
            self.sensor_nodes.append(SensorOpc(sensor, timer))
        self.actuator_nodes = [
            ActuatorOpc(actuator, self.base_timer)
            for actuator in actuators.values()
        ]

    async def init_node(self, server: Server, idx: int) -> None:
        """Create the Reactor nodes and add the sensor and actuator nodes."""
        self.idx = idx

        # Create a Reactor object in the server
        self.node = await server.nodes.objects.add_object(idx, self.id)

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
        await self._add_sensor_nodes()

        # Add actuator nodes to the server
        await self._add_actuator_nodes(server)

    async def _add_sensor_nodes(self) -> None:
        """Add sensor nodes."""
        for sensor in self.sensor_nodes:
            await sensor.init_node(self.node, self.idx)

    async def _add_actuator_nodes(
        self,
        server: Server,
    ) -> None:
        """Add actuator nodes."""
        for actuator in self.actuator_nodes:
            await actuator.init_node(server, self.node, self.idx)

    async def update_sensors(self) -> None:
        """Read each sensor and send the value to the server."""
        self.reactor.update_sensors()
        for sensor_opc in self.sensor_nodes:
            await sensor_opc.update_value()

    async def update_actuators(self) -> None:
        """Update the state of the actuators after taking the sensor readings."""
        self.reactor.update_actuators()
        for actuator_opc in self.actuator_nodes:
            await actuator_opc.update_value()

    async def update(self) -> None:
        """Call all timers and subscribers."""
        for timer in self.timers_dict.values():
            await timer.async_callback()

    def stop(self) -> None:
        """Kill all actuators."""
        for actuator in self.reactor.actuators.values():
            actuator.write(0)
