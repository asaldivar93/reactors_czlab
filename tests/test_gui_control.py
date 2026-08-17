"""Tests for the atomic control-configuration arguments."""

from __future__ import annotations

import pytest

from reactors_czlab.core.data import ControlMethod
from reactors_czlab.gui.control import (
    CONFIG_FIELDS,
    build_config_args,
    fields_for,
)


def _values(**overrides: object) -> dict[str, object]:
    """One complete server read-back payload without its enum metadata."""
    values: dict[str, object] = {
        "value": 0.0,
        "time_on": 0.0,
        "time_off": 0.0,
        "lb": 0.0,
        "ub": 0.0,
        "setpoint": 0.0,
        "kp": 100.0,
        "ki": 0.01,
        "kd": 0.0,
        "min_integral": 0.0,
        "max_integral": 4095.0,
        "auto_integral_band": True,
        "backwards": False,
    }
    values.update(overrides)
    return values


class TestAtomicArguments:
    """A form submit is represented by exactly one method call."""

    def test_pid_builds_one_complete_argument_tuple(self) -> None:
        """Regression: config used to be sent as sequential writes.

        The sequential path exposed a new setpoint with an old gain between
        notifications. One tuple gives the server one commit point instead.
        """
        args = build_config_args(
            3,
            0,
            _values(setpoint=7.0, kp=50.0),
        )

        assert len(args) == 15
        assert args[:2] == (3, 0)
        assert args[7:13] == (7.0, 50.0, 0.01, 0.0, 0.0, 4095.0)
        assert args[13:] == (True, False)

    def test_boundaries_are_adjacent_in_the_same_call(self) -> None:
        """The server can never observe the lower bound on its own."""
        args = build_config_args(2, 0, _values(lb=9.0, ub=11.0))
        assert args[5:7] == (9.0, 11.0)

    def test_timer_values_follow_the_declared_method_order(self) -> None:
        """The fixed tuple matches Method, Unit, Value, Time_on, Time_off."""
        args = build_config_args(
            1,
            2,
            _values(value=5.0, time_on=2.0, time_off=8.0),
        )
        assert args[:5] == (1, 2, 5.0, 2.0, 8.0)

    def test_server_enum_indices_are_not_reencoded(self) -> None:
        """A reordered server enum cannot silently select the wrong unit."""
        assert build_config_args(17, 9, _values())[:2] == (17, 9)

    @pytest.mark.parametrize("missing", CONFIG_FIELDS)
    def test_every_declared_argument_is_required(self, missing: str) -> None:
        """Read-back drift is reported instead of filled from client defaults."""
        values = _values()
        del values[missing]
        with pytest.raises(ValueError, match=missing):
            build_config_args(0, 0, values)


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


def test_unknown_method_name_is_refused_by_the_form_model() -> None:
    """The field list is still explicit even though enum codes are dynamic."""
    with pytest.raises(KeyError):
        fields_for("bang-bang")
