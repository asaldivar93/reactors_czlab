"""Tests for the Hamilton calibration status slicing.

This is the part of the Hamilton calibration path that can be tested at
all: ``core/sensor.py`` imports ``core.modbus`` and so needs pymodbus,
which this environment deliberately does not install. The register
slicing lives in ``core/hamilton.py`` for exactly that reason.
"""

from __future__ import annotations

import math

from reactors_czlab.core.hamilton import (
    FAILED,
    REJECTED,
    SUCCESSFUL,
    CalibrationStatus,
    build_calibration_status,
    point_name,
)


def _decode(registers, cast_type):
    """Stand in for ModbusHandler.decode, keyed by the register pair."""
    values = {
        (0, 0): 0,
        (9, 9): 7,
        (1, 1): 4.01,
        (2, 2): 0.93,
        (3, 3): 7.02,
    }
    value = values[tuple(registers)]
    return int(value) if cast_type == "int" else float(value)


def test_point_name_accepts_the_two_writable_points() -> None:
    """cp1 and cp2 are the points with a writable register."""
    assert point_name(1.0) == "cp1"
    assert point_name(2.0) == "cp2"


def test_point_name_rejects_anything_else() -> None:
    """cp6 has a status block but no writable register."""
    assert point_name(6.0) is None
    assert point_name(0.0) is None


def test_point_name_rejects_non_finite_input() -> None:
    """A Float argument from a generic OPC client can be nan or inf.

    Regression: ``int(cal_point)`` raised ValueError/OverflowError
    straight out of the uamethod, so a mistyped argument took the call
    down instead of being refused.
    """
    assert point_name(math.nan) is None
    assert point_name(math.inf) is None


def test_build_reads_status_value_quality_and_process_value() -> None:
    """Each field comes from its documented register pair."""
    status = build_calibration_status(
        "cp1",
        status_registers=[0, 0, 5, 5, 1, 1],
        quality_registers=[2, 2],
        process_registers=[8, 8, 3, 3],
        decode=_decode,
    )

    assert status == CalibrationStatus(
        point="cp1",
        status=SUCCESSFUL,
        quality=0.93,
        value=4.01,
        process_value=7.02,
    )


def test_a_non_zero_status_code_is_rejected() -> None:
    """The sensor reports 0 for a successful calibration.

    A non-zero code means the sensor answered and refused the
    calibration (unstable reading, unrecognised buffer) - distinct
    from FAILED, which means the request never completed at all.
    """
    status = build_calibration_status(
        "cp2",
        status_registers=[9, 9, 5, 5, 1, 1],
        quality_registers=[2, 2],
        process_registers=[8, 8, 3, 3],
        decode=_decode,
    )

    assert status.status == REJECTED


def test_unavailable_carries_no_readings() -> None:
    """A status that could not be read reports zeros, not stale values."""
    status = CalibrationStatus.unavailable("cp1", FAILED)

    assert (status.quality, status.value, status.process_value) == (
        0.0,
        0.0,
        0.0,
    )
