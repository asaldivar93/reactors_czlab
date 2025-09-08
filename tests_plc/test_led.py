import time

import board
import busio
from adafruit_tlc59711 import TLC59711

if __name__ == "__main__":
    print(type(board.SCK))
    print(board.SCK)
    print(board.MOSI)
    spi = busio.SPI(
        board.SCK, MOSI=board.MOSI, MISO=board.MISO
    )
    # spi = busio.SPI()
    led_driver = TLC59711(spi, pixel_count=16)
    try:
        while True:
            led_driver.set_pixel_all((0,0,0))
            led_driver.show()
            time.sleep(2)
            led_driver.set_pixel_all((60000,60000,60000))
            led_driver.show()
            time.sleep(2)
    except KeyboardInterrupt:
        led_driver.set_pixel_all((0,0,0))
        led_driver.show()
