"""Test Modbus connection with hamilton sensor."""

import platform
import time

from reactors_czlab.core.modbus import ModbusHandler
from reactors_czlab.core.reactor import IN_RASPBERRYPI
from reactors_czlab.core.sensor import HamiltonSensor
from reactors_czlab.server_info import PH_SENSORS

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
        sensor_0.address = 0x01
        try:
            while True:
                sensor_0.read()
                ph = sensor_0.channels[0].value
                temp = sensor_0.channels[1].value
                print(f"ph: {ph}, temp: {temp}")
                time.sleep(3)
        except KeyboardInterrupt:
            modbus_client.close()
    else:
        print(f"This is not a Rpi PLC: {platform.machine()}")
