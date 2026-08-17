"""Tests for truthful actuator summaries on the dashboard."""

from __future__ import annotations

from reactors_czlab.gui.components.values import (
    control_summary,
    pairing_summary,
)


def test_control_summary_carries_method_unit_and_demand() -> None:
    """The card says what is running without opening Configure."""
    summary = control_summary(
        {"method": "pid", "output_unit": "flow", "demand": 12.5},
    )
    assert summary == "pid · flow · demand 12.500 mL/min"


def test_control_summary_reports_an_unavailable_readback() -> None:
    """A failed method call is visible rather than replaced with defaults."""
    assert control_summary(None) == "Control configuration unavailable"


def test_pairing_summary_uses_operator_facing_names() -> None:
    """Published full ids cross through the labelled pairing row once."""
    rows = [
        {
            "sensor": "R0:ph",
            "channel": 0,
            "actuator": "R0:pwm0",
            "sensor_name": "ph",
            "channel_name": "pH",
            "actuator_name": "pwm0",
        },
    ]
    assert pairing_summary("pwm0", rows) == "Paired to ph:pH"


def test_pairing_summary_reports_unpaired() -> None:
    """The absence of a row is state too."""
    assert pairing_summary("pwm1", []) == "Unpaired"
