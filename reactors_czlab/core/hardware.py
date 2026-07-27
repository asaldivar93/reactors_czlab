"""Access to the Raspberry Pi PLC hardware.

This module owns every import and handle that only exists on the PLC, so the
rest of ``core`` stays importable (and testable) on a development machine.

Nothing here touches the hardware at import time. Call :func:`init_hardware`
once from the process entry point before building sensors or actuators.
"""

from __future__ import annotations

import logging
import platform
from typing import Any

_logger = logging.getLogger("server.hardware")

IN_RASPBERRYPI = platform.machine().startswith("aarch64")

LED_OFF = 0
LED_FULL = 65535

# Populated by init_hardware(). They stay None off the PLC.
rpiplc: Any = None
led_driver: Any = None

_initialized = False

if IN_RASPBERRYPI:
    from librpiplc import rpiplc  # noqa: F811


def init_hardware() -> None:
    """Bring up the PLC pins, the SPI bus and the LED driver.

    Safe to call more than once and a no-op off the Raspberry Pi, so entry
    points can call it unconditionally.
    """
    global led_driver, _initialized  # noqa: PLW0603

    if _initialized:
        return
    if not IN_RASPBERRYPI:
        _logger.info("Not running on the PLC - hardware access is disabled")
        _initialized = True
        return

    import board
    import busio
    from adafruit_tlc59711 import TLC59711

    rpiplc.init("RPIPLC_V6", "RPIPLC_58")
    spi = busio.SPI(board.SCK, MOSI=board.MOSI)
    led_driver = TLC59711(spi, pixel_count=16)
    # The reactors are lit continuously, as they were before this module
    # existed. If the biomass reading should instead be taken under a light
    # pulse, call set_leds() around SpectralSensor.read() rather than here.
    set_leds(LED_FULL)
    _initialized = True
    _logger.info("PLC hardware initialized")


def set_leds(brightness: int) -> None:
    """Set every LED channel to ``brightness`` (LED_OFF - LED_FULL).

    A no-op when the LED driver is unavailable.
    """
    if led_driver is None:
        return
    level = max(LED_OFF, min(int(brightness), LED_FULL))
    led_driver.set_pixel_all((level, level, level))
    led_driver.show()
