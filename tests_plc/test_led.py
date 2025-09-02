import time

import board
import busio
from adafruit_tlc59711 import TLC59711

if __name__ == "__main__":
    spi = busio.SPI(board.SCK, MOSI=board.MOSI)
    led_driver = TLC59711(spi, pixel_count=16)
    channel = 0
    try:
        while True:
            led_driver.set_channel(0, 30000)
            time.sleep(2)
            led_driver.set_channel(0, 65534)
            time.sleep(2)
    except KeyboardInterrupt:
        led_driver.set_channel(0, 0)
