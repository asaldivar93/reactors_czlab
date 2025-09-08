import asyncio

import adafruit_tca9548a
import board

from reactors_czlab.core.sensor import SpectralSensor
from reactors_czlab.server_info import BIOMASS_SENSORS


async def main():
    i2c = board.I2C()
    tca = adafruit_tca9548a.TCA9548A(i2c)
    sensor = SpectralSensor("0", BIOMASS_SENSORS["R0"]["R0:biomass"])
    sensor.set_i2c(tca[2])
    while True:
        await sensor.read()
        print(sensor.channels)
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
