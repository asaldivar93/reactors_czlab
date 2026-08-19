"""OPC UA server running on the Raspberry Pi PLC."""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import signal
from pathlib import Path

from reactors_czlab.core.actuator import RandomActuator
from reactors_czlab.core.calibration.storage import load_into
from reactors_czlab.core.hardware import get_i2c_channel, init_hardware
from reactors_czlab.core.sensor import RandomSensor
from reactors_czlab.core.server_state import StateStore, default_state_file
from reactors_czlab.drivers.hamilton import HamiltonSensor
from reactors_czlab.drivers.modbus import ModbusConfig, ModbusError, ModbusHandler
from reactors_czlab.drivers.plc import PlcActuator
from reactors_czlab.drivers.spectral import SpectralSensor
from reactors_czlab.opcua import ReactorOpc, ServerConfigOpc
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


def read_calibration_points(path: Path) -> list[tuple[float, float]]:
    """Read headerless ``duty,flow_ml_min`` calibration measurements.

    Parameters
    ----------
    path:
        CSV file with exactly two numeric columns per non-blank row.

    Returns
    -------
    list[tuple[float, float]]
        The rows in file order. Range and finite-value validation is done by
        ``CalibrationRun.import_points`` because it owns calibration rules.

    Raises
    ------
    ValueError
        If the file cannot be read or any non-blank row is not exactly two
        numeric columns.

    """
    try:
        with path.open(newline="", encoding="utf-8") as source:
            rows: list[tuple[float, float]] = []
            for line_number, row in enumerate(csv.reader(source), start=1):
                if not row:
                    continue
                if len(row) != 2:
                    error_message = (
                        f"{path}:{line_number} must contain exactly duty and "
                        "flow_ml_min"
                    )
                    raise ValueError(error_message)
                try:
                    rows.append((float(row[0]), float(row[1])))
                except ValueError as exc:
                    error_message = (
                        f"{path}:{line_number} must contain numeric duty and "
                        "flow_ml_min values"
                    )
                    raise ValueError(error_message) from exc
    except OSError as exc:
        error_message = f"could not read calibration CSV {path}: {exc}"
        raise ValueError(error_message) from exc

    if not rows:
        error_message = f"calibration CSV {path} contains no measurements"
        raise ValueError(error_message)
    return rows


def import_calibration_points(
    reactors: list[ReactorOpc],
    imports: list[tuple[str, Path]],
) -> None:
    """Load one-shot CSV imports into named pump calibration runs.

    Parameters
    ----------
    reactors:
        Newly constructed reactor OPC objects.
    imports:
        Pairs of full actuator ids and headerless CSV paths.

    Raises
    ------
    ValueError
        If an actuator id is duplicated, unknown, or does not have a
        calibration slot, or if its CSV is invalid.

    """
    nodes = {
        node.id: node
        for reactor in reactors
        for node in reactor.actuator_nodes
    }
    seen: set[str] = set()
    for actuator_id, path in imports:
        if actuator_id in seen:
            error_message = f"calibration CSV specified more than once for {actuator_id}"
            raise ValueError(error_message)
        seen.add(actuator_id)
        node = nodes.get(actuator_id)
        if node is None:
            error_message = f"unknown actuator for calibration CSV: {actuator_id}"
            raise ValueError(error_message)
        if node.actuator.channel.calibration is None:
            error_message = f"{actuator_id} does not support pump calibration"
            raise ValueError(error_message)
        summary = node.run.import_points(read_calibration_points(path))
        _logger.info("Imported calibration CSV for %s: %s", actuator_id, summary)


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
        reactors = []
        for r in REACTORS:
            sensors = [
                *(
                    RandomSensor(k, cfg)
                    for k, cfg in HAMILTON_SENSORS[r].items()
                ),
                *(
                    RandomSensor(k, cfg)
                    for k, cfg in BIOMASS_SENSORS[r].items()
                ),
            ]
            actuators = [
                *(
                    RandomActuator(k, cfg)
                    for k, cfg in ANALOG_ACTUATORS[r].items()
                ),
                *(
                    RandomActuator(k, cfg)
                    for k, cfg in MFC_ACTUATORS[r].items()
                ),
            ]
            for actuator in actuators:
                load_into(actuator.channel)

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

    init_hardware()

    modbus_client = ModbusHandler(ModbusConfig())

    reactors = []
    for r in REACTORS:
        sensors = [
            HamiltonSensor(k, cfg, modbus_client)
            for k, cfg in HAMILTON_SENSORS[r].items()
        ]
        for k, cfg in BIOMASS_SENSORS[r].items():
            spectral = SpectralSensor(k, cfg)
            try:
                spectral.set_i2c(get_i2c_channel(I2C_PORTS[r]))
            except (RuntimeError, ValueError, OSError):
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
        # Install any stored pump calibration. Explicit, like
        # init_hardware(): nothing reads the filesystem at import time.
        for actuator in actuators:
            load_into(actuator.channel)

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


async def main(
    endpoint: str,
    *,
    simulated: bool = False,
    state_file: Path | None = None,
    use_state: bool = True,
    calibration_imports: list[tuple[str, Path]] | None = None,
) -> None:
    """Run the server until interrupted, restoring durable state first."""
    from asyncua import Server

    reactors = build_reactors(simulated=simulated)
    if calibration_imports:
        import_calibration_points(reactors, calibration_imports)
    core_reactors = [reactor_opc.reactor for reactor_opc in reactors]
    state_store = (
        StateStore(default_state_file() if state_file is None else state_file)
        if use_state
        else None
    )
    recovered_period = (
        state_store.restore(core_reactors) if state_store is not None else None
    )
    sampling_period = SAMPLE_PERIOD if recovered_period is None else recovered_period

    server = Server()
    await server.init()
    server.set_endpoint(endpoint)
    idx = await server.register_namespace(NAMESPACE_URI)

    for r_i in reactors:
        await r_i.init_node(server, idx)

    server_config = ServerConfigOpc(
        core_reactors,
        sampling_period,
    )
    await server_config.init_node(server, idx)

    if state_store is not None:
        def checkpoint() -> None:
            state_store.checkpoint(core_reactors, server_config.period)

        server_config.on_state_changed = checkpoint
        for reactor_opc in reactors:
            reactor_opc.on_state_changed = checkpoint
            for actuator_opc in reactor_opc.actuator_nodes:
                actuator_opc.on_state_changed = checkpoint

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    signal_installed = False
    try:
        loop.add_signal_handler(signal.SIGTERM, shutdown.set)
        signal_installed = True
    except NotImplementedError:
        pass

    await server.start()
    _logger.info("Server started on %s", endpoint)
    tasks: list[asyncio.Task[None]] = []
    for reactor_opc in reactors:
        tasks.extend(
            [
                asyncio.create_task(
                    reactor_opc.reactor.sampling_loop(reactor_opc.sample_ready),
                ),
                asyncio.create_task(reactor_opc.reactor.actuator_loop()),
                asyncio.create_task(reactor_opc.update()),
            ],
        )
    if state_store is not None:
        tasks.append(
            asyncio.create_task(
                state_store.checkpoint_loop(
                    core_reactors,
                    lambda: server_config.period,
                ),
            ),
        )

    task_group = asyncio.gather(*tasks)
    shutdown_waiter = asyncio.create_task(shutdown.wait())
    try:
        done, _ = await asyncio.wait(
            {task_group, shutdown_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if task_group in done:
            await task_group
        else:
            _logger.info("SIGTERM received; shutting down")
    except (asyncio.CancelledError, KeyboardInterrupt):
        _logger.info("Shutting down")
    finally:
        shutdown_waiter.cancel()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Leave the hardware in a safe state before dropping the server.
        for r_i in reactors:
            r_i.stop()
        if state_store is not None:
            state_store.checkpoint(core_reactors, server_config.period)
        await server.stop()
        if signal_installed:
            loop.remove_signal_handler(signal.SIGTERM)


def cli() -> None:
    """Parse the command line and run the server."""
    parser = argparse.ArgumentParser(description="Run the bioreactor server")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument(
        "--simulated",
        action="store_true",
        help="Run with simulated devices instead of the PLC hardware",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=default_state_file(),
        help=(
            "Checkpoint path (default: REACTORS_STATE_FILE or "
            "~/.reactors_czlab/server-state.json)"
        ),
    )
    parser.add_argument(
        "--no-state",
        action="store_true",
        help="Start manual/zero without reading or writing a checkpoint",
    )
    parser.add_argument(
        "--import-calibration-points",
        action="append",
        nargs=2,
        metavar=("ACTUATOR", "CSV"),
        default=[],
        help=(
            "one-shot import of a headerless duty,flow_ml_min CSV into an "
            "actuator calibration run; may be repeated"
        ),
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    setup_logging(verbose=not args.quiet)
    try:
        asyncio.run(
            main(
                args.endpoint,
                simulated=args.simulated,
                state_file=args.state_file,
                use_state=not args.no_state,
                calibration_imports=[
                    (actuator_id, Path(csv_path))
                    for actuator_id, csv_path in args.import_calibration_points
                ],
            ),
        )
    except ValueError as exc:
        _logger.error("Could not import calibration points: %s", exc)
        raise SystemExit(1) from None
    except ModbusError:
        _logger.exception("Could not start: the RS485 bus is unavailable")
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        _logger.info("Interrupted")


if __name__ == "__main__":
    cli()
