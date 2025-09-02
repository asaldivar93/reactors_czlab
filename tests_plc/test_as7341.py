import time

import adafruit_tca9548a
import board

from reactors_czlab.core.sensor import SpectralSensor
from reactors_czlab.server_info import BIOMASS_SENSORS

if __name__ == "__main__":
    i2c = board.I2C()
    tca = adafruit_tca9548a.TCA9548A(i2c)
    sensor = SpectralSensor("0", BIOMASS_SENSORS["R0"]["R0:biomass"])
    sensor.set_i2c(tca[0])
    while True:
        sensor.read()
        print(sensor.channels)
        time.sleep(2)
