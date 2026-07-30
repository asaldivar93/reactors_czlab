"""Bench check for HamiltonSensor.read_calibration_status.

Not a pytest suite and not part of the package: it needs real hardware on
the RS485 bus. Run it on the Pi.

    uv run python scripts/hamilton_read_calibration.py --address 1

The numbers it prints cannot be trusted until the Modbus word order has
been verified (see CLAUDE.md, "Modbus byte order - UNVERIFIED"). If the
pH and the calibration values come back nonsensical, flip WORD_ORDER in
core/modbus.py and run this again.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from reactors_czlab.core.data import Channel, PhysicalInfo, PlcOutput
from reactors_czlab.core.modbus import ModbusHandler
from reactors_czlab.core.sensor import HamiltonSensor

MODBUS_PORT = "/dev/ttySC2"
MODBUS_BAUDRATE = 19200
MODBUS_TIMEOUT = 0.1


async def main(address: int) -> None:
    """Read both calibration points of one sensor and print them."""
    handler = ModbusHandler(
        port=MODBUS_PORT,
        baudrate=MODBUS_BAUDRATE,
        timeout=MODBUS_TIMEOUT,
    )
    sensor = HamiltonSensor(
        "bench:ph",
        PhysicalInfo(
            model="ArcPh",
            address=address,
            type=PlcOutput.digital,
            channels=[Channel("pH", "pH", register="pmc1")],
        ),
        handler,
    )

    for point in (1.0, 2.0):
        status = await sensor.read_calibration_status(point)
        print(status)


def cli() -> None:
    """Parse the command line and run the check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", type=int, default=1)
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG)
    asyncio.run(main(args.address))


if __name__ == "__main__":
    cli()
