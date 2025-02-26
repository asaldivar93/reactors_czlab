"""OPC server."""

import asyncio
import logging

from asyncua import Server
from reactors_czlab import Actuator, Sensor
from reactors_czlab.opcua import ReactorOpc

_logger = logging.getLogger("server")
_logger.setLevel(logging.DEBUG)

_formatter = logging.Formatter("%(name)s: %(asctime)s %(levelname)s - %(message)s")

_file_handler = logging.FileHandler("record.log")
_file_handler.setFormatter(_formatter)
_file_handler.setLevel(logging.DEBUG)

_stream_handler = logging.StreamHandler()
_stream_handler.setLevel(logging.WARNING)
_stream_handler.setFormatter(_formatter)

_logger.addHandler(_file_handler)
_logger.addHandler(_stream_handler)

control_config = {"method": "manual", "value": 150}

sensor_1 = Sensor("temperature_1")
sensor_2 = Sensor("temperature_2")

actuator_1 = Actuator("pump_1", control_config)
actuator_2 = Actuator("pump_2", control_config)

sensors = [sensor_1, sensor_2]
actuators = [actuator_1, actuator_2]

async def main() -> None:
    """Run the server."""
    server = Server()
    await server.init()
    server.set_endpoint("opc.tcp://localhost:4840/")

    uri = "http://czlab"
    idx = await server.register_namespace(uri)

    reactor = ReactorOpc("Reactor_1", 5, sensors, actuators)
    await reactor.init_node(server, idx)

    _logger.info("Server Started")
    async with server:
        try:
            while True:
                await reactor.update_sensors()
                reactor.update_actuators()
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            await server.stop()

if __name__ == "__main__":
    asyncio.run(main(), debug=True)
