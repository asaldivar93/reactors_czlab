"""OPC server."""

import asyncio
import logging

import adafruit_tca9548a
import board
from asyncua import Server

from reactors_czlab.core.actuator import RandomActuator
from reactors_czlab.core.modbus import ModbusHandler
from reactors_czlab.core.sensor import HamiltonSensor, SpectralSensor
from reactors_czlab.opcua import ReactorOpc
from reactors_czlab.server_info import (
    ANALOG_ACTUATORS,
    BIOMASS_SENSORS,
    HAMILTON_SENSORS,
    MFC_ACTUATORS,
)

_logger = logging.getLogger("server")
_logger.setLevel(logging.DEBUG)

_formatter = logging.Formatter(
    "%(name)s: %(asctime)s %(levelname)s - %(message)s",
)

_file_handler = logging.FileHandler("record.log")
_file_handler.setFormatter(_formatter)
_file_handler.setLevel(logging.DEBUG)

_stream_handler = logging.StreamHandler()
_stream_handler.setLevel(logging.DEBUG)
_stream_handler.setFormatter(_formatter)

_logger.addHandler(_file_handler)
_logger.addHandler(_stream_handler)

# Modbus RS485 configuration
serial_0 = "/dev/ttySC2"
modbus_client = ModbusHandler(
    port=serial_0,
    baudrate=19200,
    timeout=0.5,
)

# I2C configuration
i2c = board.I2C()
tca = adafruit_tca9548a.TCA9548A(i2c)

# Server configuration
REACTORS = ["R0", "R1", "R2"]

hamilton = {}
for r in REACTORS:
    sens = [
        HamiltonSensor(k, config, modbus_client)
        for k, config in HAMILTON_SENSORS[r].items()
    ]
    hamilton.update({r: sens})

biomass = {}
for r in REACTORS:
    sens = [
        SpectralSensor(k, config) for k, config in BIOMASS_SENSORS[r].items()
    ]
    biomass.update({r: sens})

analog = {}
for r in REACTORS:
    acts = [
        RandomActuator(k, config) for k, config in ANALOG_ACTUATORS[r].items()
    ]
    analog.update({r: acts})

mfc = {}
for r in REACTORS:
    acts = [RandomActuator(k, config) for k, config in MFC_ACTUATORS[r].items()]
    mfc.update({r: acts})

reactors = [
    ReactorOpc(
        r,
        volume=5,
        sensors=[*hamilton[r], *biomass[r]],
        actuators=[*analog[r], *mfc[r]],
        timer=7,
    )
    for r in REACTORS
]


async def main() -> None:
    """Run the server."""
    # Init the server

    server = Server()
    await server.init()
    server.set_endpoint("opc.tcp://10.10.10.20:55488/")

    uri = "http://czlab/biocontroller"
    idx = await server.register_namespace(uri)

    # Create reactors, sensor and actuator nodes
    for reactor_i in reactors:
        await reactor_i.init_node(server, idx)

    _logger.info("Server Started")
    async with server:
        try:
            while True:
                for r in reactors:
                    # Read sensors, write actuators
                    r.reactor.update()
                    # Update server
                    await r.update()
                await asyncio.sleep(0.1)
        except KeyboardInterrupt:
            await server.stop()


if __name__ == "__main__":
    asyncio.run(main(), debug=True)
