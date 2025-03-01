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

ph_sensors_dict = {
    "ph_0": {
        "model": "ArcPh",
        "address": 0x01,
        "channels": [
            {"register": 2090, "units": "pH", "description": "pH"},
            {"register": 2410, "units": "oC", "description": "degree_celsius"},
        ],
    },
    "ph_1": {
        "model": "ArcPh",
        "address": 0x02,
        "channels": [
            {"register": 2090, "units": "pH", "description": "pH"},
            {"register": 2410, "units": "oC", "description": "degree_celsius"},
        ],
    },
    "ph_2": {
        "model": "ArcPh",
        "address": 0x03,
        "channels": [
            {"register": 2090, "units": "pH", "description": "pH"},
            {"register": 2410, "units": "oC", "description": "degree_celsius"},
        ],
    },
}

do_sensors_dict = {
    "do_0": {
        "model": "VisiFerm",
        "address": 0x09,
        "channels": [
            {"register": 2090, "units": "ppm", "description": "dissolved_oxygen"},
            {"register": 2410, "units": "oC", "description": "degree_celsius"},
        ],
    },
    "do_1": {
        "model": "VisiFerm",
        "address": 0x10,
        "channels": [
            {"register": 2090, "units": "ppm", "description": "dissolved_oxygen"},
            {"register": 2410, "units": "oC", "description": "degree_celsius"},
        ],
    },
    "do_2": {
        "model": "VisiFerm",
        "address": 0x11,
        "channels": [
            {"register": 2090, "units": "ppm", "description": "dissolved_oxygen"},
            {"register": 2410, "units": "oC", "description": "degree_celsius"},
        ],
    },
}

actuators_dict = {
    "pump_0": {"address": "gpio0"},
    "pump_1": {"address": "gpio0"},
    "pump_2": {"address": "gpio0"},
}

ph_sensors_list = []
for k, val in ph_sensors_dict.items():
    address = val["address"]
    sensor = Sensor(k, address)
    sensor.add_channels(val["channels"])
    ph_sensors_list.extend(sensor)

do_sensors_list = []
for k, val in do_sensors_dict.items():
    address = val["address"]
    sensor = Sensor(k, address)
    sensor.add_channels(val["channels"])
    do_sensors_list.extend(sensor)

actuators_list = []
for k, val in actuators_list.items():
    address = val["address"]
    actuators_list.extend(Actuator(k, address))

reactors = []
for i in range(3):
    reactors.extend(
        ReactorOpc(
            f"R_{i}", 5, [ph_sensors_list[0], do_sensors_list[0]], [actuators_list[0]],
        )
    )


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
                    reactor_i.update_actuators()
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            await server.stop()


if __name__ == "__main__":
    asyncio.run(main(), debug=True)
