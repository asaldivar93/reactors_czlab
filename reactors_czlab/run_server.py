"""OPC UA server running on the Raspberry Pi PLC."""

from __future__ import annotations

import argparse
import asyncio
import logging

from reactors_czlab.core.actuator import PlcActuator, RandomActuator
from reactors_czlab.core.hardware import init_hardware
from reactors_czlab.core.modbus import ModbusError, ModbusHandler
from reactors_czlab.core.sensor import (
    HamiltonSensor,
    RandomSensor,
    SpectralSensor,
)
from reactors_czlab.opcua import ReactorOpc
from reactors_czlab.server_info import (
    ANALOG_ACTUATORS,
    BIOMASS_SENSORS,
    HAMILTON_SENSORS,
    I2C_PORTS,
    MFC_ACTUATORS,
)

_logger = logging.getLogger("server")

REACTORS = ["R0", "R1", "R2"]
DEFAULT_ENDPOINT = "opc.tcp://10.10.10.20:55488/"
NAMESPACE_URI = "http://czlab/biocontroller"
MODBUS_PORT = "/dev/ttySC2"
MODBUS_BAUDRATE = 19200
MODBUS_TIMEOUT = 0.1
REACTOR_VOLUME = 5
SAMPLE_PERIOD = 10


def setup_logging(verbose: bool = True) -> None:
    """Attach the file and stream handlers to the server logger."""
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)

    _logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter(
        "%(name)s: %(asctime)s %(levelname)s - %(message)s",
    )

    file_handler = logging.FileHandler("record.log")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(logging.DEBUG if verbose else logging.INFO)

    _logger.addHandler(file_handler)
    _logger.addHandler(stream_handler)


def build_reactors(*, simulated: bool = False) -> list[ReactorOpc]:
    """Build the reactor objects and their sensors and actuators.

    Parameters
    ----------
    simulated:
        Replace every device with its Random* stand-in, so the server can
        run on a machine with no PLC, no RS485 bus and no I2C bus.

    """
    if simulated:
        return [
            ReactorOpc(
                r,
                volume=REACTOR_VOLUME,
                sensors=[
                    *(
                        RandomSensor(k, cfg)
                        for k, cfg in HAMILTON_SENSORS[r].items()
                    ),
                    *(
                        RandomSensor(k, cfg)
                        for k, cfg in BIOMASS_SENSORS[r].items()
                    ),
                ],
                actuators=[
                    *(
                        RandomActuator(k, cfg)
                        for k, cfg in ANALOG_ACTUATORS[r].items()
                    ),
                    *(
                        RandomActuator(k, cfg)
                        for k, cfg in MFC_ACTUATORS[r].items()
                    ),
                ],
                period=SAMPLE_PERIOD,
            )
            for r in REACTORS
        ]

    import adafruit_tca9548a
    import board

    init_hardware()

    modbus_client = ModbusHandler(
        port=MODBUS_PORT,
        baudrate=MODBUS_BAUDRATE,
        timeout=MODBUS_TIMEOUT,
    )
    tca = adafruit_tca9548a.TCA9548A(board.I2C())

    reactors = []
    for r in REACTORS:
        sensors = [
            HamiltonSensor(k, cfg, modbus_client)
            for k, cfg in HAMILTON_SENSORS[r].items()
        ]
        for k, cfg in BIOMASS_SENSORS[r].items():
            spectral = SpectralSensor(k, cfg)
            try:
                spectral.set_i2c(tca[I2C_PORTS[r]])
            except (ValueError, OSError):
                _logger.warning(
                    "No spectral sensor on i2c channel %s for %s",
                    I2C_PORTS[r],
                    r,
                    exc_info=True,
                )
            else:
                sensors.append(spectral)

        actuators = [
            PlcActuator(k, cfg) for k, cfg in ANALOG_ACTUATORS[r].items()
        ]
        actuators.extend(
            RandomActuator(k, cfg) for k, cfg in MFC_ACTUATORS[r].items()
        )

        reactors.append(
            ReactorOpc(
                r,
                volume=REACTOR_VOLUME,
                sensors=sensors,
                actuators=actuators,
                period=SAMPLE_PERIOD,
            ),
        )
    return reactors


async def main(endpoint: str, *, simulated: bool = False) -> None:
    """Run the server until interrupted."""
    from asyncua import Server

    reactors = build_reactors(simulated=simulated)

    server = Server()
    await server.init()
    server.set_endpoint(endpoint)
    idx = await server.register_namespace(NAMESPACE_URI)

    tasks = []
    for r_i in reactors:
        await r_i.init_node(server, idx)
        tasks.extend(
            [
                asyncio.create_task(
                    r_i.reactor.sampling_loop(r_i.sample_ready),
                ),
                asyncio.create_task(r_i.reactor.unpaired_loop()),
                asyncio.create_task(r_i.update()),
            ],
        )

    await server.start()
    _logger.info("Server started on %s", endpoint)
    try:
        await asyncio.gather(*tasks)
    except (asyncio.CancelledError, KeyboardInterrupt):
        _logger.info("Shutting down")
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Leave the hardware in a safe state before dropping the server.
        for r_i in reactors:
            r_i.stop()
        await server.stop()


def cli() -> None:
    """Parse the command line and run the server."""
    parser = argparse.ArgumentParser(description="Run the bioreactor server")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument(
        "--simulated",
        action="store_true",
        help="Run with simulated devices instead of the PLC hardware",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    setup_logging(verbose=not args.quiet)
    try:
        asyncio.run(main(args.endpoint, simulated=args.simulated))
    except ModbusError:
        _logger.exception("Could not start: the RS485 bus is unavailable")
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        _logger.info("Interrupted")


if __name__ == "__main__":
    cli()
