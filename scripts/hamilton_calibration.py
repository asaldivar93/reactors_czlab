"""Test Modbus connection with hamilton sensor."""

import logging
import platform

from reactors_czlab.core.modbus import ModbusHandler
from reactors_czlab.core.reactor import IN_RASPBERRYPI
from reactors_czlab.core.sensor import HamiltonSensor
from reactors_czlab.server_info import PH_SENSORS

_logger = logging.getLogger("server")
_logger.setLevel(logging.INFO)

_formatter = logging.Formatter(
    "%(name)s: %(asctime)s %(levelname)s - %(message)s",
)

_file_handler = logging.FileHandler("record.log")
_file_handler.setFormatter(_formatter)
_file_handler.setLevel(logging.INFO)

_stream_handler = logging.StreamHandler()
_stream_handler.setLevel(logging.WARNING)
_stream_handler.setFormatter(_formatter)

_logger.addHandler(_file_handler)
_logger.addHandler(_stream_handler)

port = "/dev/ttySC2"

if __name__ == "__main__":
    if IN_RASPBERRYPI:
        modbus_client = ModbusHandler(
            port=port,
            baudrate=19200,
            timeout=0.5,
        )
        # Your sensor should have the default address 0x01
        sensor_0 = HamiltonSensor("R0:ph", PH_SENSORS["R0:ph"], modbus_client)
        sensor_0.write_calibration("cp2", 10.01)
    else:
        print(f"This is not a Rpi PLC: {platform.machine()}")
