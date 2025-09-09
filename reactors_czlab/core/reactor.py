"""Define the reactor class."""

from __future__ import annotations

import asyncio
import logging
import platform
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reactors_czlab.core.actuator import Actuator
    from reactors_czlab.core.sensor import Sensor

if platform.machine().startswith("aarch64"):
    import board
    import busio
    from adafruit_tlc59711 import TLC59711
    from librpiplc import rpiplc

    rpiplc.init("RPIPLC_V6", "RPIPLC_58")
    spi = busio.SPI(board.SCK, MOSI=board.MOSI)
    led_driver = TLC59711(spi, pixel_count=16)
    # Set all leds to max value
    led_driver.set_pixel_all((65535, 65535, 65535))
    led_driver.show()

_logger = logging.getLogger("server.sensors")
IN_RASPBERRYPI = platform.machine().startswith("aarch64")
pwm_lock = asyncio.Lock()


@dataclass
class ReactorState:
    """Shared state of the reactor."""

    pairings: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    sensors: list[Sensor] = field(default_factory=list)
    actuators: list[Actuator] = field(default_factory=list)
    fast_actuators: list[Actuator] = field(default_factory=list)
    slow_actuators: list[Actuator] = field(default_factory=list)
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
            A list containig the Sensor instances.
        actuators:
            A list cotaining the Actuator instances.

        """
        self.id: str = identifier
        self.volume: float = volume
        self.period: float = period
        self.sensors = sensors
        self.actuators = actuators
        self.reactor_state = ReactorState()
        for actuator in self.actuators.values():
            if actuator.info.type == "digital":
                self.reactor_state.slow_actuators.append(actuator)
            else:
                self.reactor_state.fast_actuators.append(actuator)
        self.reactor_state.sensors = sensors
        self.reactor_state.actuators = actuators

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

    async def slow_loop(self) -> None:
        """Read sensors and update paired actuators."""
        loop = asyncio.get_running_loop()
        next_tick = loop.time()
        while True:
            next_tick += self.period
            led_driver.set_pixel_all((65535, 65535, 65535))
            led_driver.show()
            # Read all sensors
            for sensor in self.sensors.values():
                await sensor.read()

            # Get pairings
            async with self.reactor_state.lock:
                # Update paired actuators
                for sensor_id in self.reactor_state.pairings:
                    sensor = self.sensors[sensor_id]  # get the sensor
                    for aid, chn in self.reactor_state.pairings[sensor_id]:
                        actuator = self.actuators[aid]  # get the actuator
                        # Verify that the selected chn exist
                        try:
                            value = sensor.channels[chn].value
                        except IndexError:
                            _logger.error(f"{chn} not a channel in {sensor.id}")
                        else:
                            if actuator.info.type == "digital":
                                await actuator.write_output(value)
                            else:
                                actuator.write_output(value)

                # Update other digital actuators (MODBUS, I2C)
                for actuator in self.reactor_state.slow_actuators:
                    await actuator.write_output(0)

            now = loop.time()
            delay = max(0.0, next_tick - now)
            await asyncio.sleep(delay)

    async def fast_loop(self) -> None:
        """Update fast acting actuators."""
        while True:
            async with self.reactor_state.lock:
                for actuator in self.reactor_state.fast_actuators:
                    async with pwm_lock:
                        actuator.write_output(0)

            await asyncio.sleep(0.1)
