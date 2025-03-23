"""OPC server."""

import asyncio
import logging

from asyncua import Server

from reactors_czlab.core.actuator import RandomActuator
from reactors_czlab.core.sensor import DO_SENSORS, PH_SENSORS, RandomSensor
from reactors_czlab.core.utils import Channel, PhysicalInfo
from reactors_czlab.opcua import ReactorOpc

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

actuators_dict = {
    "pump_0": PhysicalInfo(
        "any", 0, 0, [Channel("analog", "pump", pin="Q0.5")]
    ),
    "pump_1": PhysicalInfo(
        "any", 0, 0, [Channel("analog", "pump", pin="Q0.6")]
    ),
    "pump_2": PhysicalInfo(
        "any", 0, 0, [Channel("analog", "pump", pin="Q0.7")]
    ),
}

ph_sensors = []
for k, config in PH_SENSORS.items():
    sensor = RandomSensor(k, config)
    ph_sensors.append(sensor)

do_sensors = []
for k, config in DO_SENSORS.items():
    sensor = RandomSensor(k, config)
    do_sensors.append(sensor)

actuators = []
for k, config in actuators_dict.items():
    actuators.append(RandomActuator(k, config))

reactors = [
    ReactorOpc(f"R_{i}", 5, [ph_sensors[i], do_sensors[i]], [actuators[i]])
    for i in range(3)
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
                # Update reactors
                for reactor_i in reactors:
                    await reactor_i.update_sensors()
                    await reactor_i.update_actuators()
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            await server.stop()


if __name__ == "__main__":
    asyncio.run(main(), debug=True)
