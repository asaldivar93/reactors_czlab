"""OPC server."""

import asyncio
import logging

from asyncua import Server

from reactors_czlab.core.actuator import RandomActuator
from reactors_czlab.core.modbus import ModbusHandler
from reactors_czlab.core.sensor import RandomSensor
from reactors_czlab.opcua import ReactorOpc
from reactors_czlab.server_info import ACTUATORS, REACTORS, SENSORS

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

serial_0 = "/dev/ttySC2"

modbus_client = ModbusHandler(
    port=serial_0,
    baudrate=19200,
    timeout=0.5,
)

sensors = {}
for r in REACTORS:
    sens = [RandomSensor(k, config) for k, config in SENSORS[r].items()]
    sensors.update({r: sens})

actuators = {}
for r in REACTORS:
    acts = [RandomActuator(k, config) for k, config in ACTUATORS[r].items()]
    actuators.update({r: acts})

reactors = [
    ReactorOpc(
        r,
        volume=5,
        sensors=sensors[r],
        actuators=actuators[r],
        timer=0.5,
    )
    for r in REACTORS
]


async def main() -> None:
    """Run the server."""
    # Init the server

    server = Server()
    await server.init()
    server.set_endpoint("opc.tcp://localhost:4840/")

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
