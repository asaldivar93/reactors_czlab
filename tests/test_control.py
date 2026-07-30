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


def test_pid_backwards_inverts_the_error() -> None:
    """backwards=True demands above the setpoint and clamps below it.

    The mirror image of the normal loop (dose below, clamp above), so
    an acid pump paired to a pH probe on ``backwards`` and a base pump
    on the default sense share one setpoint.
    """
    control = _PidControl(
        setpoint=7.0,
        kp=10.0,
        ki=0.0,
        kd=0.0,
        backwards=True,
        max_val=100.0,
    )

    assert control.get_value(9.0) == pytest.approx(20.0, rel=1e-3)
    assert control.get_value(5.0) == 0.0


def test_adopt_config_keeps_the_pid_integral() -> None:
    """Retuning a gain in place must not zero the running integral.

    Regression: gains reach the controller by rebuilding it from a fresh
    ControlConfig; a rebuild would discard _integral_sum, bumping a live
    PID on every gain edit. adopt_config() copies the configuration onto
    the running object instead.
    """
    control = _PidControl(
        setpoint=10.0,
        kp=0.0,
        ki=1.0,
        kd=0.0,
        min_val=0.0,
        max_val=100.0,
    )
    control.get_value(0.0)
    time.sleep(0.02)
    control.get_value(0.0)
    grown = control._integral_sum
    assert grown > 0.0

    control.adopt_config(
        _PidControl(
            setpoint=10.0,
            kp=50.0,
            ki=1.0,
            kd=0.0,
            min_val=0.0,
            max_val=100.0,
        ),
    )

    assert control.kp == 50.0
    assert control._integral_sum == grown


def test_factory_passes_pid_gains_and_backwards(factory: ControlFactory) -> None:
    """kp/ki/kd and the backwards flag cross the config boundary."""
    control = factory.create_control(
        ControlConfig(
            ControlMethod.pid,
            kp=5.0,
            ki=2.0,
            kd=1.0,
            backwards=True,
        ),
    )

    assert (control.kp, control.ki, control.kd) == (5.0, 2.0, 1.0)
    assert control.backwards is True


def test_factory_pid_band_defaults_to_auto(factory: ControlFactory) -> None:
    """Without an explicit band the PID derives it from the range."""
    control = factory.create_control(
        ControlConfig(ControlMethod.pid),
        min_val=0.0,
        max_val=40.0,
    )

    assert control._integral_band_is_default is True
    assert (control.min_integral, control.max_integral) == (0.0, 40.0)


def test_factory_pid_installs_an_explicit_band(factory: ControlFactory) -> None:
    """auto_integral_band=False installs the operator's band verbatim."""
    control = factory.create_control(
        ControlConfig(
            ControlMethod.pid,
            auto_integral_band=False,
            min_integral=1.0,
            max_integral=5.0,
        ),
        min_val=0.0,
        max_val=40.0,
    )

    assert control._integral_band_is_default is False
    assert (control.min_integral, control.max_integral) == (1.0, 5.0)


def test_factory_passes_boundaries_backwards(factory: ControlFactory) -> None:
    """on_boundaries backwards reaches the controller too."""
    control = factory.create_control(
        ControlConfig(ControlMethod.on_boundaries, backwards=True),
    )

    assert control.backwards is True


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


def test_a_defaulted_integral_band_tracks_the_limits() -> None:
    """No band configured: refresh_derived_limits() must move it with
    min_val/max_val, exactly like the clamp itself.
    """
    control = _PidControl(min_val=0.0, max_val=40.0)

    assert control._integral_band_is_default is True
    assert (control.min_integral, control.max_integral) == (0.0, 40.0)

    control.min_val, control.max_val = 0.0, 80.0
    control.refresh_derived_limits()

    assert (control.min_integral, control.max_integral) == (0.0, 80.0)


def test_a_partial_integral_band_is_treated_as_explicit() -> None:
    """Only min_integral given: the whole band counts as a deliberate
    override, not silently completed and then re-derived later.
    """
    control = _PidControl(min_val=0.0, max_val=40.0, min_integral=5.0)

    assert control._integral_band_is_default is False
    assert (control.min_integral, control.max_integral) == (5.0, 40.0)

    control.min_val, control.max_val = 0.0, 80.0
    control.refresh_derived_limits()

    assert (control.min_integral, control.max_integral) == (5.0, 40.0)


def test_a_limit_change_replaces_the_controller(
    factory: ControlFactory,
) -> None:
    """Limits are configuration, so they take part in equality."""
    config = ControlConfig(ControlMethod.pid, setpoint=7.0)

    narrow = factory.create_control(config, max_val=40.0)
    wide = factory.create_control(config, max_val=4095.0)

    assert narrow != wide


@pytest.mark.parametrize(
    "bad",
    [float("nan"), float("inf"), float("-inf")],
)
def test_rejects_a_non_finite_config_value(
    factory: ControlFactory,
    bad: float,
) -> None:
    """A non-finite parameter is not a number a controller can use.

    `_as_float` validated the type and stopped there, so a NaN from an
    OPC `Float` was accepted by every controller. NaN does not fail
    loudly - every comparison against it is false, so it disables the
    check it appears in - and the failures differ per field: a NaN
    `time_on` leaves `elapsed > self._interval` false forever, so the
    timer never leaves the ON phase it starts in and the pump it drives
    never turns off.
    """
    with pytest.raises(TypeError, match="finite"):
        factory.create_control(
            ControlConfig(ControlMethod.manual, value=bad),
        )
    with pytest.raises(TypeError, match="finite"):
        factory.create_control(
            ControlConfig(ControlMethod.timer, time_on=bad, time_off=1.0),
        )


def test_a_non_finite_config_leaves_the_running_controller_alone(
    make_calibrated_actuator,
) -> None:
    """The TypeError lands on a path set_control_config already handles.

    The actuator logs the offending config and keeps the controller it
    was running, rather than propagating out of the OPC datachange
    callback.
    """
    actuator = make_calibrated_actuator()
    actuator.set_control_config(
        ControlConfig(ControlMethod.manual, value=1500.0),
    )
    running = actuator.controller

    actuator.set_control_config(
        ControlConfig(ControlMethod.manual, value=float("nan")),
    )

    assert actuator.controller is running
    assert actuator.controller.value == 1500.0
