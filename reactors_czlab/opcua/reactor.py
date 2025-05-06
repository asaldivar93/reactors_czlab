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
        self.id: str = identifier
        self.base_timer: Timer = Timer(timer)
        _logger.info(f"Init Reactor {self.id}")
        self.reactor = Reactor(
            identifier,
            volume,
            sensors,
            actuators,
            self.base_timer,
        )
        self.sensors = sensors
        self.actuators = actuators
        self.timers: dict[float, Timer] = {}
        self.set_up_timers()
        self.reactor.timers = self.timers

        _logger.info(f"Creating nodes for {self.id}")
        self.sensor_nodes: list[SensorOpc] = []
        self.actuator_nodes: list[ActuatorOpc] = []
        self.create_child_nodes()
        for actuator in self.actuator_nodes:
            actuator.sensors = sensors

    @property
    def sensors(self) -> dict[str, Sensor]:
        """Get the sensors dict."""
        return self._sensors

    @sensors.setter
    def sensors(self, sensors: list[Sensor]) -> None:
        """Set the sensors as a dict."""
        if not isinstance(sensors, list):
            raise TypeError
        self._sensors = {s.id: s for s in sensors}

    @property
    def actuators(self) -> dict[str, Actuator]:
        """Get the actuators dict."""
        return self._actuators

    @actuators.setter
    def actuators(self, actuators: list[Actuator]) -> None:
        """Set the actuators as a dict."""
        self._actuators = {a.id: a for a in actuators}

    def set_up_timers(self) -> None:
        """Create timers and pass them to childs."""
        self.timers = {
            self.base_timer.interval: self.base_timer,
        }
        for sensor in self.sensors.values():
            interval = sensor.sensor_info.sample_interval
            new_timer = self.timers.get(interval, None)
            if new_timer is None:
                new_timer = Timer(interval)
                self.timers.update({interval: new_timer})
            sensor.base_timer = new_timer
            sensor.timer = new_timer

        # Pass the base timer to the actuators
        for actuator in self.actuators.values():
            actuator.base_timer = self.base_timer
            actuator.timer = self.base_timer

    def create_child_nodes(self):
        """Create OPC nodes for sensors and actuators."""
        sensors = self.reactor.sensors
        for sensor in sensors.values():
            interval = sensor.sensor_info.sample_interval
            timer = self.timers[interval]
            self.sensor_nodes.append(SensorOpc(sensor, timer))

        actuators = self.reactor.actuators
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
        for sensor in self.sensor_nodes:
            await sensor.init_node(self.node, self.idx)

        # Add actuator nodes to the server
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
        for timer in self.timers.values():
            await timer.async_callback()

    def stop(self) -> None:
        """Kill all actuators."""
        for actuator in self.reactor.actuators.values():
            actuator.write(0)
