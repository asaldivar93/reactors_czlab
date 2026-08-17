"""Reading a Hamilton calibration point, minus the transport.

Everything here is a decision about what the sensor's registers *mean*:
which words inside a ``CP{n}Status`` block hold the status code and the
stored value, which calibration points exist, and what a status code
says to an operator. None of it touches Modbus, so it is standard
library only and importable without ``pymodbus`` - which is what lets
``tests/`` cover it and what lets the GUI render a status without
depending on the server's dependency set.

``HamiltonSensor`` supplies the transport: it reads the register block
and decodes the words, then hands the results here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: The calibration points that have a writable register, in the order an
#: operator thinks of them. ``HamiltonSensor.REGISTERS`` also carries a
#: ``cp6_status`` block, but there is no ``cp6`` write register, so it
#: is not a point that can be set.
CALIBRATION_POINTS: tuple[str, ...] = ("cp1", "cp2")

#: Word pairs inside the 6-register ``CP{n}Status`` block.
#: Reg1/Reg2 status, Reg3/Reg4 unit, Reg5/Reg6 value - see the register
#: table in ``HamiltonSensor``'s docstring. The unit pair is read but
#: not currently interpreted.
STATUS_WORDS = slice(0, 2)
VALUE_WORDS = slice(4, 6)

#: Word pair holding the measured value inside a 10-register PMC block.
PROCESS_VALUE_WORDS = slice(2, 4)

#: The status code a Hamilton sensor reports for an accepted
#: calibration. Every other code is a refusal; the sensor's own manual
#: is the authority on what each one means, so the code is carried
#: through verbatim rather than being mapped to invented text.
CALIBRATION_OK = 0


def calibration_point_name(cal_point: float) -> str:
    """Turn an OPC ``Cal_point`` argument into a register key.

    The argument crosses OPC as a Float, so it arrives as ``1.0`` or
    ``2.0`` rather than an int.

    Parameters
    ----------
    cal_point:
        The calibration point number, 1 or 2.

    Returns
    -------
    str
        The matching key in ``HamiltonSensor.REGISTERS``, e.g. ``cp1``.

    Raises
    ------
    ValueError
        If the argument is not a finite number, or names no writable
        calibration point. Checking finiteness first matters:
        ``int(nan)`` raises ``ValueError`` on its own, with a message
        that says nothing about calibration points. A non-numeric
        argument is reported the same way rather than escaping as a
        ``TypeError``, because this runs behind an OPC method that any
        client can call with anything.

    """
    try:
        finite = math.isfinite(cal_point)
    except TypeError as err:
        error_message = (
            f"Calibration point must be a number, got {cal_point!r}"
        )
        raise ValueError(error_message) from err

    if not finite:
        error_message = (
            f"Calibration point must be a finite number, got {cal_point!r}"
        )
        raise ValueError(error_message)

    name = f"cp{int(cal_point)}"
    if name not in CALIBRATION_POINTS:
        error_message = (
            f"Invalid calibration point {cal_point!r}, "
            f"expected one of {[p[-1] for p in CALIBRATION_POINTS]}"
        )
        raise ValueError(error_message)
    return name


def status_text(code: int) -> str:
    """Render a calibration status code for an operator.

    Only code 0 has a documented meaning here, so anything else is
    shown as itself rather than guessed at.
    """
    if code == CALIBRATION_OK:
        return "ok"
    return f"refused (code {code})"


@dataclass(frozen=True)
class CalibrationStatus:
    """What one calibration point currently holds.

    Attributes
    ----------
    point:
        Which point this describes, ``cp1`` or ``cp2``.
    code:
        The raw status code from Reg1/Reg2 of the ``CP{n}Status`` block.
    value:
        The calibration value the sensor has stored for this point,
        from Reg5/Reg6 of the same block.
    quality:
        The sensor's quality indicator. A property of the sensor, not of
        the point, but read alongside it because an operator judges a
        calibration by both.
    process_value:
        The sensor's live primary measurement (PMC1) at the time of the
        read - the pH or dissolved oxygen the probe is seeing now.

    """

    point: str
    code: int
    value: float
    quality: float
    process_value: float

    @property
    def ok(self) -> bool:
        """Whether the sensor accepted the calibration at this point."""
        return self.code == CALIBRATION_OK

    @property
    def text(self) -> str:
        """The status code rendered for an operator."""
        return status_text(self.code)
