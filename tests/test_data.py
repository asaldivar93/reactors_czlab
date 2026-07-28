"""Tests for the shared dataclasses."""

from __future__ import annotations

from reactors_czlab.core.data import (
    MAX_OUTPUT,
    Calibration,
    ControlConfig,
    ControlMethod,
    OutputUnit,
)


def test_a_fresh_calibration_is_unfitted() -> None:
    """A calibration with no fit timestamp must not be trusted."""
    cal = Calibration("pump_0")

    assert cal.is_fitted is False
    assert cal.points == []
    assert cal.max_duty == MAX_OUTPUT
    assert cal.dispense_duty == MAX_OUTPUT


def test_calibration_points_are_not_shared_between_instances() -> None:
    """points must be a per-instance list, not a class-level default."""
    first = Calibration("pump_0")
    second = Calibration("pump_1")

    first.points.append((100.0, 1.0))

    assert second.points == []


def test_the_line_converts_both_ways() -> None:
    """flow_at and duty_for are inverses of flow = a * duty + b."""
    cal = Calibration("pump_0", a=0.01, b=-2.0)

    assert cal.flow_at(1000.0) == 8.0
    assert cal.duty_for(8.0) == 1000.0


def test_a_fitted_calibration_reports_itself_fitted() -> None:
    """A non-empty fitted_at is what makes a calibration usable."""
    cal = Calibration("pump_0", fitted_at="2026-07-27T10:00:00+00:00")

    assert cal.is_fitted is True


def test_control_config_defaults_to_duty() -> None:
    """Existing callers get today's behaviour with no change."""
    config = ControlConfig(ControlMethod.manual, value=150)

    assert config.output_unit is OutputUnit.duty
