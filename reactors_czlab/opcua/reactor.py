"""Reactor node for the OPC UA server."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from asyncua import ua, uamethod

from reactors_czlab.core.reactor import Reactor
from reactors_czlab.opcua.actuator import ActuatorOpc
from reactors_czlab.opcua.sensor import SensorOpc

if TYPE_CHECKING:
    from asyncua import Server
    from asyncua.common import Node

    from reactors_czlab.core.actuator import Actuator
    from reactors_czlab.core.sensor import Sensor

_logger = logging.getLogger("server.opcreactor")


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

        self.sensor_nodes: list[SensorOpc] = []
        self.actuator_nodes: list[ActuatorOpc] = []
        self.create_child_nodes()
        # Flag for sensing loop completed
        self.sample_ready = asyncio.Event()
        self.pairings_var: Node | None = None
        self._publish_lock = asyncio.Lock()

    def __repr__(self) -> str:
        """Print the reactor id."""
        return f"ReactorOpc(id: {self.id})"

    @property
    def sensors(self) -> dict[str, Sensor]:
        """Get the sensors dict owned by the Reactor."""
        return self.reactor.sensors

    @property
    def actuators(self) -> dict[str, Actuator]:
        """Get the actuators dict owned by the Reactor."""
        return self.reactor.actuators

    def create_child_nodes(self) -> None:
        """Create OPC nodes for sensors and actuators."""
        self.sensor_nodes = [SensorOpc(s) for s in self.sensors.values()]
        self.actuator_nodes = [ActuatorOpc(a) for a in self.actuators.values()]

    async def init_node(self, server: Server, idx: int) -> None:
        """Create the Reactor nodes and add the sensor and actuator nodes."""
        self.idx = idx

        # Create a Reactor object in the server
        self.node = await server.nodes.objects.add_object(idx, self.id)
        # Create Actuators and Sensors objects in the server
        self.snode = await self.node.add_object(idx, f"{self.id}:sensors")
        self.anode = await self.node.add_object(idx, f"{self.id}:actuators")

        # Add sensor nodes to the server
        _logger.info("Creating Sensor Nodes for %s", self.id)
        for sensor in self.sensor_nodes:
            await sensor.init_node(self.snode, self.idx, self.id)

        # Add actuator nodes to the server
        for actuator in self.actuator_nodes:
            await actuator.init_node(server, self.anode, self.idx)

        # Read-only picture of the pairing table. It lives on the reactor
        # node, above R{n}:sensors / R{n}:actuators, so match_tree never
        # sees it - a String here could never be inserted into the FLOAT
        # value column of the data table.
        self.pairings_var = await self.node.add_variable(
            idx,
            f"{self.id}:pairings",
            self.pairings_json(),
        )

        await self.init_pairing_methods(idx)

    def _validate_pair(self, sid: str, aid: str) -> bool:
        """Check that a sensor id and an actuator id belong to this reactor."""
        if sid not in self.reactor.sampling.sensors:
            _logger.error("%s is not a sensor of %s", sid, self.id)
            return False
        if aid not in self.reactor.sampling.actuators:
            _logger.error("%s is not an actuator of %s", aid, self.id)
            return False
        return True

    def pairings_json(self) -> str:
        """The current pairing table as a JSON list.

        Sorted so an unchanged table serialises identically and a
        subscriber is not woken by key ordering.
        """
        rows = [
            {"sensor": sid, "actuator": aid, "channel": channel}
            for sid, paired in self.reactor.sampling.pairings.items()
            for aid, channel in paired
        ]
        rows.sort(key=lambda row: (row["sensor"], row["actuator"],
                                   row["channel"]))
        return json.dumps(rows)

    async def publish_pairings(self) -> None:
        """Write the pairing table to the OPC variable.

        ``reactor.sampling.pairings`` is server-side Python state and
        the pairing methods answer with a bare bool, so without this a
        client cannot show what is paired, nor recover the picture after
        its own restart.
        """
        if self.pairings_var is None:
            return
        # The publish lock totally orders snapshot-and-write pairs, so
        # the last write to land is always the one whose snapshot was
        # taken last. sampling.lock is held only for the synchronous
        # snapshot, never across the write: sampling_loop takes it
        # every period to drive the paired actuators.
        async with self._publish_lock:
            async with self.reactor.sampling.lock:
                payload = self.pairings_json()
            await self.pairings_var.write_value(payload)

    async def init_pairing_methods(self, idx: int) -> None:
        """Expose the set_pairing and unpair methods on the reactor node."""
        sampling = self.reactor.sampling
        unpaired = self.reactor.unpaired

        @uamethod
        async def set_pairing(
            parent: Node,
            sid: str,
            aid: str,
            channel: int,
        ) -> bool:
            """Pair an (actuator, channel) to a sensor."""
            if not self._validate_pair(sid, aid):
                return False

            async with sampling.lock:
                already_paired = any(
                    aid == paired_aid
                    for paired in sampling.pairings.values()
                    for paired_aid, _ in paired
                )
                if already_paired:
                    _logger.error("%s is already paired to a sensor", aid)
                    return False

                sampling.pairings[sid].append((aid, channel))

            # A paired actuator is driven by the sampling loop, so take it
            # out of the loop that drives unpaired actuators.
            async with unpaired.lock:
                try:
                    unpaired.actuators.remove(aid)
                except ValueError:
                    _logger.warning("%s was not in the unpaired loop", aid)

            await self.publish_pairings()
            _logger.info("Current pairings: %s", dict(sampling.pairings))
            return True

        @uamethod
        async def unpair(
            parent: Node,
            sid: str,
            aid: str,
            channel: int,
        ) -> bool:
            """Unpair an (actuator, channel) from a sensor."""
            if not self._validate_pair(sid, aid):
                return False

            async with sampling.lock:
                try:
                    sampling.pairings[sid].remove((aid, channel))
                except ValueError:
                    _logger.error(
                        "(%s, %s) is not paired to %s",
                        aid,
                        channel,
                        sid,
                    )
                    return False

            # Hand the actuator back to the unpaired loop.
            async with unpaired.lock:
                if aid not in unpaired.actuators:
                    unpaired.actuators.append(aid)

            await self.publish_pairings()
            _logger.info("Current pairings: %s", dict(sampling.pairings))
            return True

        inarg_sid = ua.Argument()
        inarg_sid.Name = "Sensor_id"
        inarg_sid.DataType = ua.NodeId(ua.ObjectIds.String)
        inarg_sid.Description = ua.LocalizedText(
            Text="Id of the reference sensor",
        )

        inarg_aid = ua.Argument()
        inarg_aid.Name = "Actuator_id"
        inarg_aid.DataType = ua.NodeId(ua.ObjectIds.String)
        inarg_aid.Description = ua.LocalizedText(
            Text="Id of the actuator to pair",
        )

        inarg_chn = ua.Argument()
        inarg_chn.Name = "Channel"
        inarg_chn.DataType = ua.NodeId(ua.ObjectIds.Int64)
        inarg_chn.Description = ua.LocalizedText(
            Text="Index of the sensor channel driving the actuator",
        )

        await self.node.add_method(
            idx,
            f"{self.id}:set_pairing",
            set_pairing,
            [inarg_sid, inarg_aid, inarg_chn],
            [ua.VariantType.Boolean],
        )

        await self.node.add_method(
            idx,
            f"{self.id}:unpair",
            unpair,
            [inarg_sid, inarg_aid, inarg_chn],
            [ua.VariantType.Boolean],
        )

    async def update(self) -> None:
        """Publish sensor readings and actuator states after every sample."""
        while True:
            await self.sample_ready.wait()
            # Re-arm before publishing so a sample taken while we publish is
            # not lost. Without this the event stays set forever and the
            # loop free runs.
            self.sample_ready.clear()

            for sensor_opc in self.sensor_nodes:
                await sensor_opc.update_value()

            for actuator_opc in self.actuator_nodes:
                await actuator_opc.update_value()

    def stop(self) -> None:
        """Kill all actuators."""
        self.reactor.stop()
