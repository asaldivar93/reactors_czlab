import time

import adafruit_tca9548a
import board
from adafruit_as7341 import AS7341

if __name__=="__main__":
    i2c = board.I2C()
    tca = adafruit_tca9548a.TCA9548A(i2c)
    as7341 = AS7341(tca[0])
    while True:
        values = {
            "415": as7341.channel_415nm,
            "445": as7341.channel_445nm,
            "480": as7341.channel_480nm,
            "515": as7341.channel_515nm,
            "555": as7341.channel_555nm,
            "590": as7341.channel_590nm,
            "630": as7341.channel_630nm,
            "680": as7341.channel_680nm,
            "clear": as7341.channel_clear,
            "nir": as7341.channel_nir,
        }
        print(values)
        time.sleep(3)
