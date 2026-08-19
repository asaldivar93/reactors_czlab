"""Tests for the Hamilton calibration interpretation layer.

These deliberately import ``drivers.hamilton_model`` and never the transport:
the point of the split is that what the registers mean can be covered
without pymodbus, on a machine with no RS485 bus.
"""

from __future__ import annotations

import math

import pytest

from reactors_czlab.drivers.hamilton_model import (
    CALIBRATION_OK,
    CALIBRATION_POINTS,
    PROCESS_VALUE_WORDS,
    STATUS_WORDS,
    VALUE_WORDS,
    CalibrationStatus,
    calibration_point_name,
    status_text,
)


class TestCalibrationPointName:
    """Turning an OPC Float argument into a register key."""

    @pytest.mark.parametrize(
        ("cal_point", "expected"),
        [(1.0, "cp1"), (2.0, "cp2"), (1, "cp1"), (2, "cp2")],
    )
    def test_accepts_the_writable_points(
        self,
        cal_point: float,
        expected: str,
    ) -> None:
        """1 and 2 map to the register keys, as int or float."""
        assert calibration_point_name(cal_point) == expected

    @pytest.mark.parametrize("cal_point", [0.0, 3.0, 6.0, -1.0])
    def test_rejects_points_with_no_write_register(
        self,
        cal_point: float,
    ) -> None:
        """cp6 has a status block but no writable register."""
        with pytest.raises(ValueError, match="Invalid calibration point"):
            calibration_point_name(cal_point)

    @pytest.mark.parametrize(
        "cal_point",
        [math.nan, math.inf, -math.inf],
    )
    def test_rejects_non_finite_before_converting(
        self,
        cal_point: float,
    ) -> None:
        """Regression: int(nan) raised before the validity check.

        write_calibration used to do ``f"cp{int(cal_point)}"`` ahead of
        its guard and outside its try block, so a non-finite Cal_point
        escaped as a bare ValueError from int() with a message that said
        nothing about calibration.
        """
        with pytest.raises(ValueError, match="finite number"):
            calibration_point_name(cal_point)


class TestRegisterLayout:
    """The word pairs inside a CP{n}Status block."""

    def test_status_value_and_process_slices_are_distinct(self) -> None:
        """Status, stored value and process value read different words."""
        block = [10, 11, 20, 21, 30, 31]
        assert block[STATUS_WORDS] == [10, 11]
        assert block[VALUE_WORDS] == [30, 31]

        pmc = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
        assert pmc[PROCESS_VALUE_WORDS] == [2, 3]

    def test_the_stored_value_is_in_registers_five_and_six(self) -> None:
        """The requirement names registers 5 and 6 of CP#Status.

        Those are 1-based register numbers, so they are words 4 and 5 of
        the zero-based block this code slices.
        """
        assert VALUE_WORDS == slice(4, 6)


class TestStatusText:
    """Rendering a status code."""

    def test_zero_is_ok(self) -> None:
        """Code 0 is the documented accept."""
        assert status_text(CALIBRATION_OK) == "ok"

    def test_other_codes_carry_the_number_through(self) -> None:
        """Undocumented codes are shown, not guessed at."""
        assert "7" in status_text(7)


class TestCalibrationStatus:
    """The struct handed to the GUI."""

    def test_ok_follows_the_code(self) -> None:
        """ok is true only for the accept code."""
        good = CalibrationStatus("cp1", CALIBRATION_OK, 7.0, 0.98, 7.01)
        bad = CalibrationStatus("cp2", 5, 4.0, 0.5, 4.2)
        assert good.ok
        assert good.text == "ok"
        assert not bad.ok
        assert "5" in bad.text

    def test_points_are_ordered_for_display(self) -> None:
        """The GUI shows CP1 then CP2, so the order is fixed here."""
        assert CALIBRATION_POINTS == ("cp1", "cp2")
