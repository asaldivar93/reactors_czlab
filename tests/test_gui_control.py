"""Tests for the control-config write plan.

The server rebuilds an entire ControlConfig on every variable change, so
the order the GUI writes in is a safety property, not a style choice.
"""

from __future__ import annotations

from reactors_czlab.core.data import ControlMethod, OutputUnit
from reactors_czlab.gui.control import (
    METHOD_FIELDS,
    build_write_plan,
    method_index,
    unit_index,
    unit_rejection_reason,
)

FITTED = {"is_fitted": True, "fitted_at": "2026-07-27T10:00:00+00:00"}
UNFITTED = {"is_fitted": False, "fitted_at": ""}


def test_method_is_always_written_last() -> None:
    """Regression: writing method first applies the new controller
    against whatever stale setpoint and gains are still in the server's
    variables. ActuatorOpc.datachange_notification rebuilds the whole
    config on every notification, so a manual -> pid switch could drive
    hard for one notification before the real setpoint landed.
    """
    plan = build_write_plan(
        ControlMethod.pid,
        OutputUnit.duty,
        {"setpoint": 7.0, "kp": 50.0, "ki": 0.02, "kd": 0.0,
         "backwards": False, "min_integral": 0.0, "max_integral": 100.0,
         "auto_integral_band": True, "value": 0.0},
    )

    assert plan[-1].channel == "method"
    assert plan[-2].channel == "output_unit"


def test_every_selected_field_is_written() -> None:
    """The plan covers exactly the fields the method uses, plus value."""
    plan = build_write_plan(
        ControlMethod.timer,
        OutputUnit.duty,
        {"time_on": 5.0, "time_off": 10.0, "value": 2000.0},
    )

    written = {write.channel for write in plan}
    assert written == {
        "time_on",
        "time_off",
        "value",
        "output_unit",
        "method",
    }


def test_unrelated_fields_are_not_written() -> None:
    """A manual config must not push stale PID gains at the server."""
    plan = build_write_plan(
        ControlMethod.manual,
        OutputUnit.duty,
        {"value": 1500.0, "kp": 999.0},
    )

    assert [write.channel for write in plan] == [
        "value",
        "output_unit",
        "method",
    ]


def test_the_enum_indices_match_the_server() -> None:
    """The GUI writes integers; the server maps them back to enums."""
    assert method_index(ControlMethod.pid) == 3
    assert unit_index(OutputUnit.volume) == 2


def test_method_fields_covers_every_method() -> None:
    """A new ControlMethod without an entry would write nothing."""
    assert set(METHOD_FIELDS) == set(ControlMethod)


def test_duty_is_allowed_without_a_calibration() -> None:
    """Raw counts need nothing from the calibration."""
    assert unit_rejection_reason(OutputUnit.duty, UNFITTED) is None


def test_flow_needs_a_fitted_calibration() -> None:
    """check_unit() rejects this server-side and only logs it.

    The form has to refuse first, or the operator writes a config that
    is silently dropped and sees no reason why.
    """
    reason = unit_rejection_reason(OutputUnit.flow, UNFITTED)

    assert reason is not None
    assert "fitted" in reason


def test_volume_is_allowed_on_a_fitted_calibration() -> None:
    """A fitted line converts mL to a duty."""
    assert unit_rejection_reason(OutputUnit.volume, FITTED) is None


def test_a_missing_calibration_payload_is_refused() -> None:
    """No calibration slot at all is not a fitted calibration."""
    assert unit_rejection_reason(OutputUnit.flow, None) is not None
