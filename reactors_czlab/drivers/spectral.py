"""AS7341 spectral sensor adapter and serialized I2C access."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from reactors_czlab.core.data import ERROR_VALUE, PhysicalInfo
from reactors_czlab.core.hardware import IN_RASPBERRYPI
from reactors_czlab.core.sensor import Sensor

if IN_RASPBERRYPI:
    from adafruit_as7341 import AS7341

_i2c_lock = asyncio.Lock()
_i2c_executor = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="i2c",
)

_logger = logging.getLogger("server.sensors")


class SpectralSensor(Sensor):
    """AS7341 11 channel sensor."""

    def __init__(
        self,
        identifier: str,
        config: PhysicalInfo,
    ) -> None:
        """AS7341 spectral sensor.

        Call set_i2c() with the multiplexer channel before reading.

        Parameters
        ----------
        identifier:
            A unique identifier for the sensor
        config:
            PhysicalInfo sensor information: model, address, channels

        """
        super().__init__(identifier, config)
        self.bus: AS7341 | None = None

    def set_i2c(self, i2c: object) -> None:
        """Attach the sensor to an I2C bus (usually a TCA9548A channel)."""
        self.bus = AS7341(i2c)

    async def read(self) -> None:
        """Read every spectral channel over I2C."""
        if self.bus is None:
            _logger.warning(
                "%s has no i2c bus, channels set to %s",
                self.id,
                ERROR_VALUE,
            )
            for chn in self.channels:
                chn.value = ERROR_VALUE
            return

        loop = asyncio.get_running_loop()

        def _blocking_call() -> None:
            values = {
                "415": self.bus.channel_415nm,
                "445": self.bus.channel_445nm,
                "480": self.bus.channel_480nm,
                "515": self.bus.channel_515nm,
                "555": self.bus.channel_555nm,
                "590": self.bus.channel_590nm,
                "630": self.bus.channel_630nm,
                "680": self.bus.channel_680nm,
                "clear": self.bus.channel_clear,
                "nir": self.bus.channel_nir,
            }
            for chn in self.channels:
                chn.value = values[chn.units]
            _logger.debug("In %s: %s", self.id, values)

        try:
            async with _i2c_lock:
                await loop.run_in_executor(_i2c_executor, _blocking_call)
        except OSError:
            _logger.warning(
                "i2c read failed for %s, channels set to %s",
                self.id,
                ERROR_VALUE,
                exc_info=True,
            )
            for chn in self.channels:
                chn.value = ERROR_VALUE
