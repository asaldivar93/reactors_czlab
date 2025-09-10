"""Reactor node for the OPC UA server."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from asyncua import ua, uamethod

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
        period: float,
    ) -> None:
        """Initialize the OPC Reactor node."""
        self.id: str = identifier
        self.period = period
        self.reactor = Reactor(
            identifier,
            volume,
            sensors,
            actuators,
            period,
        )
        self.sensors = sensors
        self.actuators = actuators

        _logger.info(f"Creating nodes for {self.id}")
        self.sensor_nodes: list[SensorOpc] = []
        self.actuator_nodes: list[ActuatorOpc] = []
        self.create_child_nodes()

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

    def create_child_nodes(self) -> None:
        """Create OPC nodes for sensors and actuators."""
        sensors = self.reactor.sensors
        self.sensor_nodes = [SensorOpc(s) for s in sensors.values()]

        actuators = self.reactor.actuators
        self.actuator_nodes = [ActuatorOpc(a) for a in actuators.values()]

    async def init_node(self, server: Server, idx: int) -> None:
        """Create the Reactor nodes and add the sensor and actuator nodes."""
        self.idx = idx

        # Create a Reactor object in the server
        self.node = await server.nodes.objects.add_object(idx, self.id)

        # Add sensor nodes to the server
        for sensor in self.sensor_nodes:
            await sensor.init_node(self.node, self.idx)

        # Add actuator nodes to the server
        for actuator in self.actuator_nodes:
            await actuator.init_node(server, self.node, self.idx)

        # Add method to match actuators to sensors
        reactor_state = self.reactor.reactor_slow

        @uamethod
        async def set_pairing(parent, sensor, actuator, channel) -> bool:
            """Pairs an (actuator, channel) to a sensor."""
            print(sensor)
            # Validete sensor_id and actuator_id
            if (
                sensor not in reactor_state.sensors
                or actuator not in reactor_state.actuators
            ):
                raise ua.UaStatusCodeError(ua.StatusCode.is_bad)
            # Check that the actuator is not paired already
            is_paired = [
                (actuator, channel) in paired
                for paired in reactor_state.pairings.values()
            ]
            if any(is_paired):
                raise ua.UaStatusCodeError(ua.StatusCode.is_bad)
            # Pair the actuator
            async with reactor_state.lock:
                reactor_state.pairings[sensor].append((actuator, channel))
            _logger.info(f"Current pairings {reactor_state.pairings}")

            return True

        @uamethod
        async def unpair(parent, sensor, actuator, channel) -> bool:
            """Unpairs an (actuator, channel) from a sensor."""
            # Validete sensor_id and actuator_id
            if (
                sensor not in reactor_state.sensors
                or actuator not in reactor_state.actuators
            ):
                raise ua.UaStatusCodeError(ua.StatusCode.is_bad)
            # Unpair the actuator
            async with reactor_state.lock:
                reactor_state.pairings[sensor].remove((actuator, channel))
            _logger.info(f"Current pairings {reactor_state.pairings}")

            return True

        await self.node.add_method(
            idx,
            "set_pairing",
            set_pairing,
            [
                ua.VariantType.String,
                ua.VariantType.String,
                ua.VariantType.Int64,
            ],
            [ua.VariantType.Boolean],
        )

        await self.node.add_method(
            idx,
            "unpair",
            unpair,
            [
                ua.VariantType.String,
                ua.VariantType.String,
                ua.VariantType.Int64,
            ],
            [ua.VariantType.Boolean],
        )

        # Get all avaliable sensors
        sensors_list = [sensor.id for sensor in self.sensors.values()]
        # Add sensor list to the opc server
        sensors_variant = ua.Variant(
            [ua.LocalizedText(sensor) for sensor in sensors_list],
            ua.VariantType.LocalizedText,
        )
        await self.node.add_property(
            ua.ObjectIds.MultiStateDiscreteType_EnumStrings,
            "Sensors",
            sensors_variant,
        )

        # Get all avaliable actuators
        actuators_list = [actuator.id for actuator in self.actuators.values()]
        # Add sensor list to the opc server
        actuators_variant = ua.Variant(
            [ua.LocalizedText(actuator) for actuator in actuators_list],
            ua.VariantType.LocalizedText,
        )
        await self.node.add_property(
            ua.ObjectIds.MultiStateDiscreteType_EnumStrings,
            "Actuators",
            actuators_variant,
        )

    async def update(self) -> None:
        """Get Sensor readings and update actuators."""
        # TO DO: Replace the update loop with qeue
        while True:
            for sensor_opc in self.sensor_nodes:
                await sensor_opc.update_value()

            for actuator_opc in self.actuator_nodes:
                await actuator_opc.update_value()
            await asyncio.sleep(self.period)

    def stop(self) -> None:
        """Kill all actuators."""
        for actuator in self.reactor.actuators.values():
            actuator.write(0)
