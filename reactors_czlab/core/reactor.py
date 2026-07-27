"""Define the reactor class."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reactors_czlab.core.actuator import Actuator
    from reactors_czlab.core.sensor import Sensor

_logger = logging.getLogger("server.reactor")

#: Reference value handed to controllers that are not paired to a sensor.
#: Manual and timer controllers ignore it; the others should not be used
#: unpaired.
UNPAIRED_INPUT = 0.0

#: How often unpaired actuators are refreshed, in seconds.
UNPAIRED_PERIOD = 0.05


@dataclass
class SamplingState:
    """Shared state of the sampling loop.

    Attributes
    ----------
    pairings:
        {sensor_id: [(actuator_id, channel_index), ...]}
    sensors:
        Ids of the sensors read by the loop
    actuators:
        Ids of the actuators that may be paired to a sensor
    lock:
        Guards pairings against concurrent OPC method calls

    """

    pairings: dict[str, list[tuple[str, int]]] = field(
        default_factory=lambda: defaultdict(list),
    )
    sensors: list[str] = field(default_factory=list)
    actuators: list[str] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class UnpairedState:
    """Shared state of the loop driving actuators with no reference sensor."""

    actuators: list[str] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Reactor:
    """Class representation of the reactors."""

    def __init__(
        self,
        identifier: str,
        volume: float,
        sensors: list[Sensor],
        actuators: list[Actuator],
        period: float,
    ) -> None:
        """Initialize the reactor.

        Parameters
        ----------
        identifier:
            A unique identifier for the reactor.
        volume:
            The initial volume of the reactor.
        sensors:
            A list containing the Sensor instances.
        actuators:
            A list containing the Actuator instances.
        period:
            Seconds between sensor samples.

        """
        self.id: str = identifier
        self.volume: float = volume
        self.period: float = period
        self.sensors = sensors
        self.actuators = actuators
        self.sampling = SamplingState()
        self.unpaired = UnpairedState()

        self.sampling.sensors = [s.id for s in self.sensors.values()]
        self.sampling.actuators = [a.id for a in self.actuators.values()]
        # Every actuator starts unpaired; set_pairing() moves it out of this
        # list and unpair() puts it back.
        self.unpaired.actuators = [a.id for a in self.actuators.values()]

    def __repr__(self) -> str:
        """Print the reactor id."""
        return f"Reactor(id: {self.id})"

    @property
    def sensors(self) -> dict[str, Sensor]:
        """Get the sensors dict."""
        return self._sensors

    @sensors.setter
    def sensors(self, sensors: list[Sensor]) -> None:
        """Set the sensors as a dict."""
        if not isinstance(sensors, list):
            error_message = f"sensors must be a list, got {type(sensors)}"
            raise TypeError(error_message)
        self._sensors = {s.id: s for s in sensors}

    @property
    def actuators(self) -> dict[str, Actuator]:
        """Get the actuators dict."""
        return self._actuators

    @actuators.setter
    def actuators(self, actuators: list[Actuator]) -> None:
        """Set the actuators as a dict."""
        if not isinstance(actuators, list):
            error_message = f"actuators must be a list, got {type(actuators)}"
            raise TypeError(error_message)
        self._actuators = {a.id: a for a in actuators}

    def update_paired_actuators(self) -> None:
        """Drive every paired actuator from its reference sensor channel.

        The caller is responsible for holding ``self.sampling.lock``.
        """
        for sensor_id, paired in self.sampling.pairings.items():
            sensor = self.sensors[sensor_id]
            for aid, chn in paired:
                actuator = self.actuators[aid]
                try:
                    value = sensor.channels[chn].value
                except IndexError:
                    _logger.error("%s is not a channel in %s", chn, sensor.id)
                else:
                    actuator.write_output(value)

    async def sampling_loop(self, sample_ready: asyncio.Event) -> None:
        """Read all sensors, then update the actuators paired to them."""
        loop = asyncio.get_running_loop()
        next_tick = loop.time()
        while True:
            async with asyncio.TaskGroup() as tg:
                for sensor in self.sensors.values():
                    tg.create_task(sensor.read())

            async with self.sampling.lock:
                self.update_paired_actuators()

            # Flag that the sensing loop finished
            sample_ready.set()

            # Drift correct the next tick. If a read overran the period, skip
            # the ticks we missed instead of free running to catch up.
            next_tick += self.period
            now = loop.time()
            if next_tick < now:
                _logger.warning(
                    "%s sampling overran its %.1fs period by %.3fs",
                    self.id,
                    self.period,
                    now - next_tick,
                )
                next_tick = now + self.period
            await asyncio.sleep(max(0.0, next_tick - now))

    async def unpaired_loop(self) -> None:
        """Refresh the actuators that are not paired to a sensor."""
        while True:
            async with self.unpaired.lock:
                for aid in self.unpaired.actuators:
                    self.actuators[aid].write_output(UNPAIRED_INPUT)
            await asyncio.sleep(UNPAIRED_PERIOD)

    def stop(self) -> None:
        """Drive every actuator to zero."""
        for actuator in self.actuators.values():
            actuator.write(0)
