"""OPC server."""

import asyncio
import logging

from asyncua import Server

from reactors_czlab.core.actuator import PlcActuator
from reactors_czlab.core.sensor import DO_SENSORS, PH_SENSORS, AnalogSensor
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

sensors_dict = {
    "A0.5": PhysicalInfo("any", 0, 0, [Channel("analog", "a", pin="A0.5")]),
    "A0.6": PhysicalInfo("any", 0, 0, [Channel("analog", "a", pin="A0.6")]),
    "A0.7": PhysicalInfo("any", 0, 0, [Channel("analog", "a", pin="A0.7")]),
}

sensors = []
for k, config in sensors_dict.items():
    sensor = AnalogSensor(k, config)
    sensors.append(sensor)

actuators = []
for k, config in actuators_dict.items():
    actuators.append(PlcActuator(k, config))

reactors = [
    ReactorOpc(f"R_{i}", 5, [sensors[i]], [actuators[i]]) for i in range(3)
]


async def main() -> None:
    """Run the server."""
    # Init the server
    server = Server()
    await server.init()
    server.set_endpoint("opc.tcp://0.0.0.0:55488/")

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
            for reactor_i in reactors:
                reactor_i.stop()
            await server.stop()


if __name__ == "__main__":
    asyncio.run(main(), debug=True)
