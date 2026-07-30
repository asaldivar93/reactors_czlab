"""Hamilton Arc calibration status, decoded from holding registers.

Standard library only, and deliberately not part of ``core/sensor.py``:
that module imports ``core.modbus`` and cannot be imported without
pymodbus, which the Pi has and the test environment does not. The
register slicing is the part worth testing, so it lives here and
``HamiltonSensor`` calls into it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

#: Calibration points with a writable register in
#: ``HamiltonSensor.REGISTERS``. cp6 has a status block but no writable
#: value register, so it cannot be set over the bus.
CALIBRATION_POINTS = ("cp1", "cp2")

#: Status code the sensor reports for a calibration that succeeded.
CALIBRATION_OK = 0

#: Status strings. These reach the operator through an OPC method's
#: return value, so they are constants rather than inline literals.
SUCCESSFUL = "Successful"
FAILED = "failed"
UNSUPPORTED = "unsupported"

#: Offsets of the 32 bit values inside the register blocks. A Hamilton
#: value spans two registers, so every one of these is two wide.
_FIRST_VALUE = slice(0, 2)  # status code, and the quality block's only value
_STATUS_VALUE = slice(4, 6)  # the stored calibration value
_PROCESS_VALUE = slice(2, 4)  # the measurement inside a PMC block


@dataclass(frozen=True)
class CalibrationStatus:
    """State of one calibration point of a Hamilton sensor.

    Parameters
    ----------
    point:
        ``cp1`` or ``cp2``, or ``unknown`` when the request named
        neither.
    status:
        ``Successful``, ``failed`` or ``unsupported``.
    quality:
        The sensor's quality indicator.
    value:
        The calibration value the sensor currently holds for the point.
    process_value:
        The live PMC1 measurement, for judging whether the sensor has
        settled.

    """

    point: str
    status: str
    quality: float
    value: float
    process_value: float

    @classmethod
    def unavailable(cls, point: str, status: str) -> CalibrationStatus:
        """A status carrying no readings, for a read that did not happen.

        Zeros rather than stale values: an operator must not read a
        number off a failed request and believe it.
        """
        return cls(
            point=point,
            status=status,
            quality=0.0,
            value=0.0,
            process_value=0.0,
        )


def point_name(cal_point: float) -> str | None:
    """Name of the calibration point ``cal_point`` selects.

    The argument arrives from a generic OPC client as a ``Float``, so it
    can be ``nan`` or ``inf``, and ``int()`` raises on both. Returning
    ``None`` keeps that a refusal rather than an exception out of the
    method call.

    Returns
    -------
    str or None
        ``cp1`` / ``cp2``, or ``None`` when ``cal_point`` names neither.

    """
    try:
        name = f"cp{int(cal_point)}"
    except (ValueError, OverflowError):
        return None
    return name if name in CALIBRATION_POINTS else None


def build_calibration_status(
    point: str,
    status_registers: Sequence[int],
    quality_registers: Sequence[int],
    process_registers: Sequence[int],
    decode: Callable[[Sequence[int], str], float],
) -> CalibrationStatus:
    """Slice three register blocks into a ``CalibrationStatus``.

    Parameters
    ----------
    point:
        ``cp1`` or ``cp2``.
    status_registers:
        The six register ``cp{n}_status`` block: code in registers 1-2,
        unit in 3-4, value in 5-6.
    quality_registers:
        The two register ``quality`` block.
    process_registers:
        The ten register ``pmc1`` block; the measurement is in
        registers 3-4.
    decode:
        ``ModbusHandler.decode``, taking two registers and a cast type.
        Injected so this module needs no pymodbus.

    """
    code = decode(status_registers[_FIRST_VALUE], "int")
    return CalibrationStatus(
        point=point,
        status=SUCCESSFUL if code == CALIBRATION_OK else FAILED,
        quality=float(decode(quality_registers[_FIRST_VALUE], "float")),
        value=float(decode(status_registers[_STATUS_VALUE], "float")),
        process_value=float(
            decode(process_registers[_PROCESS_VALUE], "float"),
        ),
    )
