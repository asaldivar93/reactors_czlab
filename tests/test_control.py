"""Tests for the control strategies."""

from __future__ import annotations

import time

import pytest

from reactors_czlab.core.control import (
    ControlFactory,
    _ManualControl,
    _OnBoundariesControl,
    _PidControl,
    _TimerControl,
)
from reactors_czlab.core.data import ControlConfig, ControlMethod


@pytest.fixture
def factory() -> ControlFactory:
    """The control factory under test."""
    return ControlFactory()


def test_factory_builds_each_method(factory: ControlFactory) -> None:
    """Every ControlMethod maps to its controller class."""
    assert isinstance(
        factory.create_control(ControlConfig(ControlMethod.manual)),
        _ManualControl,
    )
    assert isinstance(
        factory.create_control(ControlConfig(ControlMethod.timer)),
        _TimerControl,
    )
    assert isinstance(
        factory.create_control(ControlConfig(ControlMethod.on_boundaries)),
        _OnBoundariesControl,
    )
    assert isinstance(
        factory.create_control(ControlConfig(ControlMethod.pid)),
        _PidControl,
    )


def test_equality_compares_configuration(factory: ControlFactory) -> None:
    """Same config compares equal, different config does not."""
    a = factory.create_control(ControlConfig(ControlMethod.manual, value=150))
    b = factory.create_control(ControlConfig(ControlMethod.manual, value=150))
    c = factory.create_control(ControlConfig(ControlMethod.manual, value=10))

    assert a == b
    assert a != c


def test_equality_across_methods(factory: ControlFactory) -> None:
    """Controllers of different types are never equal."""
    manual = factory.create_control(ControlConfig(ControlMethod.manual))
    timer = factory.create_control(ControlConfig(ControlMethod.timer))
    pid = factory.create_control(ControlConfig(ControlMethod.pid))

    assert manual != timer
    assert timer != pid
    assert manual != pid


def test_equality_ignores_runtime_state() -> None:
    """A timer that has toggled still equals its identical twin.

    Regression: the old __eq__ compared the *current* output, so a running
    timer looked like a new configuration and got rebuilt on every OPC
    write, resetting its phase.
    """
    a = _TimerControl(time_on=0.01, time_off=0.01, value_on=150)
    b = _TimerControl(time_on=0.01, time_off=0.01, value_on=150)

    time.sleep(0.02)
    a.get_value(0)

    assert a.value != b.value
    assert a == b


def test_manual_ignores_the_sensor() -> None:
    """Manual control returns its configured value whatever the input."""
    control = _ManualControl(value=150)
    assert control.get_value(0) == 150
    assert control.get_value(999) == 150


def test_timer_toggles_after_time_on() -> None:
    """The output starts on and drops after time_on elapses."""
    control = _TimerControl(time_on=0.01, time_off=10.0, value_on=150)

    assert control.get_value(0) == 150
    time.sleep(0.02)
    assert control.get_value(0) == 0


def test_on_boundaries_has_hysteresis() -> None:
    """Between the bounds the output holds its previous state."""
    control = _OnBoundariesControl(
        lower_bound=1.1,
        upper_bound=2.1,
        value_on=150,
    )

    assert control.get_value(0.5) == 150  # below lb -> on
    assert control.get_value(1.5) == 150  # between -> hold
    assert control.get_value(3.0) == 0  # above ub -> off
    assert control.get_value(1.5) == 0  # between -> hold


def test_on_boundaries_backwards_inverts() -> None:
    """backwards=True swaps which threshold turns the output on."""
    control = _OnBoundariesControl(
        lower_bound=1.1,
        upper_bound=2.1,
        value_on=150,
        backwards=True,
    )

    assert control.get_value(0.5) == 0
    assert control.get_value(3.0) == 150


def test_on_boundaries_is_verbose_safe() -> None:
    """VERBOSE logging must not raise.

    Regression: the debug line referenced an undefined name, so every
    on_boundaries actuator killed the reactor task on its first update.
    """
    control = _OnBoundariesControl(
        lower_bound=1.0,
        upper_bound=2.0,
        value_on=10,
    )
    assert control.get_value(0.0) == 10


def test_pid_drives_towards_the_setpoint() -> None:
    """A reading below the setpoint produces a positive output."""
    control = _PidControl(setpoint=35, kp=10, ki=0.0, kd=0.0)
    assert control.get_value(20) == pytest.approx(150, rel=1e-3)


def test_pid_output_is_clamped() -> None:
    """The output never leaves the actuator range."""
    control = _PidControl(setpoint=35, kp=1000, max_val=4095)
    assert control.get_value(-1000) == 4095
    assert control.get_value(1e6) == 0


def test_pid_uses_measured_dt() -> None:
    """The integral grows with elapsed time, not with a hardcoded dt.

    Regression: dt was pinned to 10 regardless of the loop period.
    """
    control = _PidControl(setpoint=10, kp=0.0, ki=1.0, kd=0.0)
    control.get_value(0)
    first = control.value

    time.sleep(0.05)
    control.get_value(0)

    assert control.value > first


def test_rejects_non_numeric_config(factory: ControlFactory) -> None:
    """A bad parameter raises TypeError rather than failing later."""
    with pytest.raises(TypeError):
        factory.create_control(ControlConfig(ControlMethod.manual, value="x"))


def test_rejects_unknown_method(factory: ControlFactory) -> None:
    """An unrecognised method is rejected by the factory."""
    with pytest.raises(TypeError):
        factory.create_control(ControlConfig("not_a_method"))


def test_limits_reach_the_controller(factory: ControlFactory) -> None:
    """A dispenser's demand range becomes the controller's clamp range."""
    control = factory.create_control(
        ControlConfig(ControlMethod.pid, setpoint=7.0),
        min_val=0.0,
        max_val=40.0,
    )

    assert control.min_val == 0.0
    assert control.max_val == 40.0
    assert control.clamp(100.0) == 40.0


def test_pid_anti_windup_defaults_to_the_demand_range(
    factory: ControlFactory,
) -> None:
    """The integral band follows the unit, not the raw PWM full scale."""
    control = factory.create_control(
        ControlConfig(ControlMethod.pid, setpoint=7.0),
        min_val=0.0,
        max_val=40.0,
    )

    assert control.max_integral == 40.0


def test_a_limit_change_replaces_the_controller(
    factory: ControlFactory,
) -> None:
    """Limits are configuration, so they take part in equality."""
    config = ControlConfig(ControlMethod.pid, setpoint=7.0)

    narrow = factory.create_control(config, max_val=40.0)
    wide = factory.create_control(config, max_val=4095.0)

    assert narrow != wide
