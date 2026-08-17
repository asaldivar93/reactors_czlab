"""Tests for the atomic control-configuration arguments."""

from __future__ import annotations

import pytest

from reactors_czlab.core.data import ControlMethod, OutputUnit
from reactors_czlab.gui.control import build_config_args, fields_for


class TestAtomicArguments:
    """A form submit is represented by exactly one method call."""

    def test_pid_builds_one_complete_argument_tuple(self) -> None:
        """Regression: config used to be sent as sequential writes.

        The sequential path exposed a new setpoint with an old gain between
        notifications. One tuple gives the server one commit point instead.
        """
        args = build_config_args(
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

        assert len(args) == 15
        assert args[:2] == (3, 0)
        assert args[7:13] == (7.0, 100.0, 0.01, 0.0, 0.0, 4095.0)
        assert args[13:] == (True, False)

    def test_boundaries_are_adjacent_in_the_same_call(self) -> None:
        """The server can never observe the lower bound on its own."""
        args = build_config_args(
            ControlMethod.on_boundaries,
            OutputUnit.duty,
            {"lb": 9.0, "ub": 11.0, "value": 2000.0, "backwards": True},
        )

        assert args[5:7] == (9.0, 11.0)

    def test_manual_ignores_fields_it_does_not_consume(self) -> None:
        """Stale hidden PID fields cannot affect a manual controller."""
        args = build_config_args(
            ControlMethod.manual,
            OutputUnit.duty,
            {"value": 1500.0, "kp": 999.0},
        )

        assert args[2] == 1500.0
        assert args[8] == 999.0
        assert fields_for(ControlMethod.manual) == ("value",)

    def test_timer_values_follow_the_declared_method_order(self) -> None:
        """The fixed tuple matches Method, Unit, Value, Time_on, Time_off."""
        args = build_config_args(
            ControlMethod.timer,
            OutputUnit.volume,
            {"value": 5.0, "time_on": 2.0, "time_off": 8.0},
        )

        assert args[:5] == (1, 2, 5.0, 2.0, 8.0)

    @pytest.mark.parametrize(
        ("method", "code"),
        [
            (ControlMethod.manual, 0),
            (ControlMethod.timer, 1),
            (ControlMethod.on_boundaries, 2),
            (ControlMethod.pid, 3),
        ],
    )
    def test_method_enum_encoding(self, method: str, code: int) -> None:
        """The first argument is the server's UInt32 method index."""
        values = dict.fromkeys(fields_for(method), 0.0)
        if "auto_integral_band" in values:
            values["auto_integral_band"] = True
        if "backwards" in values:
            values["backwards"] = False
        assert build_config_args(method, OutputUnit.duty, values)[0] == code

    @pytest.mark.parametrize(
        ("unit", "code"),
        [(OutputUnit.duty, 0), (OutputUnit.flow, 1), (OutputUnit.volume, 2)],
    )
    def test_output_unit_enum_encoding(self, unit: str, code: int) -> None:
        """The second argument is the server's UInt32 unit index."""
        args = build_config_args(ControlMethod.manual, unit, {"value": 0.0})
        assert args[1] == code

    def test_an_unknown_method_is_refused(self) -> None:
        """Unknown UI state never reaches the server."""
        with pytest.raises(KeyError):
            build_config_args("bang-bang", OutputUnit.duty, {})

    def test_an_unknown_output_unit_is_refused(self) -> None:
        """A made-up unit cannot silently encode as raw duty."""
        with pytest.raises(KeyError):
            build_config_args(ControlMethod.manual, "litres", {"value": 1.0})

    def test_a_missing_required_field_is_refused(self) -> None:
        """Missing gains must not silently take defaults."""
        with pytest.raises(ValueError, match="needs a value for ki"):
            build_config_args(
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


def test_fields_for_pid_includes_every_live_tuning_knob() -> None:
    """The form exposes every field the server consumes for PID."""
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
