"""OPC server."""

import asyncio
import logging

from asyncua import Server

from reactors_czlab.core.actuator import PlcActuator
from reactors_czlab.core.modbus import ModbusHandler
from reactors_czlab.core.sensor import HamiltonSensor
from reactors_czlab.opcua import ReactorOpc
from reactors_czlab.server_info import DO_SENSORS, PH_SENSORS, PUMPS

_logger = logging.getLogger("server")
_logger.setLevel(logging.INFO)

_formatter = logging.Formatter(
    "%(name)s: %(asctime)s %(levelname)s - %(message)s",
)

_file_handler = logging.FileHandler("record.log")
_file_handler.setFormatter(_formatter)
_file_handler.setLevel(logging.INFO)

_stream_handler = logging.StreamHandler()
_stream_handler.setLevel(logging.INFO)
_stream_handler.setFormatter(_formatter)

_logger.addHandler(_file_handler)
_logger.addHandler(_stream_handler)

serial_0 = "/dev/ttySC2"

modbus_client = ModbusHandler(
    port=serial_0,
    baudrate=19200,
    timeout=0.5,
)

ph_sensors = []
for k, config in PH_SENSORS.items():
    sensor = HamiltonSensor(k, config, modbus_client)
    ph_sensors.append(sensor)

do_sensors = []
for k, config in DO_SENSORS.items():
    sensor = HamiltonSensor(k, config, modbus_client)
    do_sensors.append(sensor)

#actuators = []
#for k, config in PUMPS.items():
#    actuators.append(PlcActuator(k, config))

reactors = [
    ReactorOpc(
        f"R{i}",
        volume=5,
        sensors=[ph_sensors[i]],
        actuators=[],
        timer=4,
    )
    for i in range(1)
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
