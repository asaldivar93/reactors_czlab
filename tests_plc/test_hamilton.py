"""Test Modbus connection with hamilton sensor."""
import asyncio
import platform

from reactors_czlab.core.hardware import IN_RASPBERRYPI
from reactors_czlab.drivers.hamilton import HamiltonSensor
from reactors_czlab.drivers.modbus import ModbusConfig, ModbusHandler
from reactors_czlab.server_info import HAMILTON_SENSORS

port = "/dev/ttySC2"

async def main():
    if IN_RASPBERRYPI:
        modbus_client = ModbusHandler(ModbusConfig())
        # Your sensor should have the default address 0x01
        sensor_0 = HamiltonSensor("R0:ph", HAMILTON_SENSORS["R0"]["R0:ph"], modbus_client)
        sensor_0.address = 0x02
        try:
            while True:
                await sensor_0.read()
                ph = sensor_0.channels[0].value
                temp = sensor_0.channels[1].value
                print(f"ph: {ph}, temp: {temp}")
                await asyncio.sleep(3)
        except KeyboardInterrupt:
            modbus_client.close()
    else:
        print(f"This is not a Rpi PLC: {platform.machine()}")


if __name__ == "__main__":
    asyncio.run(main())
