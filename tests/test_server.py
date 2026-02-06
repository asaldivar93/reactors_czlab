import asyncio
import logging

from asyncua import Server

from reactors_czlab.core.actuator import RandomActuator
from reactors_czlab.core.sensor import RandomSensor
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

REACTORS = ["R0", "R1", "R2"]

hamilton = {}
for r in REACTORS:
    sens = [
        RandomSensor(k, config) for k, config in HAMILTON_SENSORS[r].items()
    ]
    hamilton.update({r: sens})

biomass = {}
for r in REACTORS:
    sens = [RandomSensor(k, config) for k, config in BIOMASS_SENSORS[r].items()]
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
        period=10,
    )
    for r in REACTORS
]


async def main():
    server = Server()
    await server.init()
    server.set_endpoint("opc.tcp://localhost:4840/")
    uri = "http://czlab/biocontroller"
    idx = await server.register_namespace(uri)
    # Create reactors, sensor and actuator nodes
    tasks = []
    for r_i in reactors:
        await r_i.init_node(server, idx)
        tasks.extend(
            [
                asyncio.create_task(r_i.reactor.slow_loop(r_i.sample_ready)),
                asyncio.create_task(r_i.reactor.fast_loop()),
                asyncio.create_task(r_i.update()),
            ],
        )

    await server.start()
    _logger.info("Server Started")
    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        for r_i in reactors:
            r_i.stop()
    finally:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
