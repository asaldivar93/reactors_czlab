"""Tests for the control-configuration write plan."""

from __future__ import annotations

import pytest

from reactors_czlab.core.data import ControlMethod, OutputUnit
from reactors_czlab.gui.control import (
    METHOD_CHANNEL,
    UNIT_CHANNEL,
    build_write_plan,
    fields_for,
    unit_rejection_reason,
)


def _channels(plan: list) -> list[str]:
    """The channel names of a plan, in order."""
    return [write.channel for write in plan]


class TestWriteOrder:
    """The order is the whole point of this module."""

    def test_method_is_written_last(self) -> None:
        """Regression: writing method first applies it to stale values.

        ActuatorOpc.datachange_notification rebuilds the entire
        ControlConfig on every notification, reading only the fields the
        currently selected method needs. Writing `method` first makes
        the server build a pid controller from whatever setpoint and
        gains were left over from the previous configuration, and drive
        on them for one notification - which on a pump means dosing.
        """
        plan = build_write_plan(
            ControlMethod.pid,
            OutputUnit.duty,
            {
                "setpoint": 7.0,
                "kp": 100.0,
                "ki": 0.01,
                "kd": 0.0,
                "backwards": False,
                "auto_integral_band": True,
                "min_integral": 0.0,
                "max_integral": 4095.0,
            },
        )

        assert _channels(plan)[-1] == METHOD_CHANNEL

    def test_output_unit_is_written_just_before_method(self) -> None:
        """The unit changes delivery, so it must precede the rebuild.

        Regression: with output_unit written after method, the new
        controller is built against the old unit for one notification -
        a demand in mL/min interpreted as raw counts, which pegs a pump.
        """
        plan = build_write_plan(
            ControlMethod.manual,
            OutputUnit.volume,
            {"value": 5.0},
        )

        assert _channels(plan) == ["value", UNIT_CHANNEL, METHOD_CHANNEL]

    def test_parameters_come_before_both(self) -> None:
        """Every intermediate notification then rebuilds the old config."""
        plan = build_write_plan(
            ControlMethod.on_boundaries,
            OutputUnit.duty,
            {"lb": 6.8, "ub": 7.2, "value": 2000.0, "backwards": False},
        )

        channels = _channels(plan)
        assert channels.index("lb") < channels.index(UNIT_CHANNEL)
        assert channels.index("ub") < channels.index(METHOD_CHANNEL)


class TestEnumEncoding:
    """The server's variables are UInt32 indices, not strings."""

    @pytest.mark.parametrize(
        ("method", "code"),
        [
            (ControlMethod.manual, 0),
            (ControlMethod.timer, 1),
            (ControlMethod.on_boundaries, 2),
            (ControlMethod.pid, 3),
        ],
    )
    def test_methods_encode_to_their_server_side_index(
        self,
        method: str,
        code: int,
    ) -> None:
        """These mirror the maps in opcua/actuator.py."""
        plan = build_write_plan(
            method,
            OutputUnit.duty,
            dict.fromkeys(fields_for(method), 0.0),
        )
        assert plan[-1].value == code

    @pytest.mark.parametrize(
        ("unit", "code"),
        [(OutputUnit.duty, 0), (OutputUnit.flow, 1), (OutputUnit.volume, 2)],
    )
    def test_units_encode_to_their_server_side_index(
        self,
        unit: str,
        code: int,
    ) -> None:
        """A wrong index here silently drives the wrong delivery mode."""
        plan = build_write_plan(ControlMethod.manual, unit, {"value": 0.0})
        assert plan[-2].value == code

    def test_an_unknown_method_is_refused(self) -> None:
        """Better a KeyError here than an out-of-range write."""
        with pytest.raises(KeyError):
            build_write_plan("bang-bang", OutputUnit.duty, {})


class TestMethodFields:
    """Only the fields a method reads are written."""

    def test_manual_writes_only_its_value(self) -> None:
        """The server reads nothing else for a manual controller."""
        plan = build_write_plan(
            ControlMethod.manual,
            OutputUnit.duty,
            {"value": 1500.0, "kp": 999.0},
        )
        assert "kp" not in _channels(plan)

    def test_pid_writes_its_gains_and_band(self) -> None:
        """All of them: the server rebuilds from the full set."""
        assert set(fields_for(ControlMethod.pid)) == {
            "setpoint",
            "kp",
            "ki",
            "kd",
            "backwards",
            "auto_integral_band",
            "min_integral",
            "max_integral",
        }

    def test_a_missing_field_is_refused(self) -> None:
        """A silently defaulted gain retunes a controller by accident.

        The server rebuilds the whole config from its variables, so an
        unwritten field is not "left alone" - it keeps whatever value
        the last configuration put there.
        """
        with pytest.raises(ValueError, match="needs a value for ki"):
            build_write_plan(
                ControlMethod.pid,
                OutputUnit.duty,
                {
                    "setpoint": 7.0,
                    "kp": 100.0,
                    "kd": 0.0,
                    "backwards": False,
                    "auto_integral_band": True,
                    "min_integral": 0.0,
                    "max_integral": 4095.0,
                },
            )


class TestUnitRejection:
    """Asking the question the server answers only to its own log."""

    def test_duty_never_needs_a_calibration(self) -> None:
        """Raw counts are what an uncalibrated pump is driven in."""
        assert unit_rejection_reason(OutputUnit.duty, None) is None

    def test_flow_on_an_actuator_with_no_slot_is_refused(self) -> None:
        """The MFCs have calibration=None."""
        reason = unit_rejection_reason(OutputUnit.flow, None)
        assert reason is not None
        assert "no calibration slot" in reason

    def test_volume_on_an_unfitted_pump_is_refused(self) -> None:
        """check_unit requires is_fitted, and only logs its refusal.

        Without asking first, the operator sees a configuration that
        looks accepted: set_control_config keeps the running controller
        and returns nothing to the client.
        """
        reason = unit_rejection_reason(
            OutputUnit.volume,
            {"is_fitted": False, "installable_reason": None},
        )
        assert reason is not None
        assert "fitted" in reason

    def test_a_fitted_usable_calibration_passes(self) -> None:
        """Nothing to report is the normal case."""
        assert (
            unit_rejection_reason(
                OutputUnit.flow,
                {"is_fitted": True, "installable_reason": None},
            )
            is None
        )

    def test_the_authority_s_wording_is_passed_through(self) -> None:
        """installable_reason() is already written for an operator.

        CLAUDE.md makes it the single authority on whether a calibration
        may be used; rewording it here would be a second opinion.
        """
        reason = unit_rejection_reason(
            OutputUnit.flow,
            {
                "is_fitted": True,
                "installable_reason": "dispense duty produces no flow",
            },
        )
        assert reason == "dispense duty produces no flow"
