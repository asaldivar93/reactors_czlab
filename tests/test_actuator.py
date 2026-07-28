"""Tests for the Actuator base class and its control configuration."""

from __future__ import annotations

from reactors_czlab.core.actuator import RandomActuator
from reactors_czlab.core.control import _ManualControl, _TimerControl
from reactors_czlab.core.data import (
    ERROR_VALUE,
    ControlConfig,
    ControlMethod,
    OutputUnit,
)


def test_starts_in_manual_at_zero(actuator: RandomActuator) -> None:
    """A new actuator holds its output at zero."""
    assert isinstance(actuator.controller, _ManualControl)
    assert actuator.controller.value == 0


def test_write_output_reaches_the_channel(actuator: RandomActuator) -> None:
    """A manual value is written through to the channel."""
    actuator.set_control_config(
        ControlConfig(ControlMethod.manual, value=150),
    )
    actuator.write_output(0)

    assert actuator.channel.value == 150
    assert actuator.channel.old_value == 150


def test_write_output_skips_unchanged_values(
    actuator: RandomActuator,
) -> None:
    """An unchanged value is not pushed to the hardware twice."""
    actuator.set_control_config(
        ControlConfig(ControlMethod.manual, value=150),
    )
    actuator.write_output(0)
    actuator.channel.value = -1  # pretend the pin drifted
    actuator.write_output(0)

    assert actuator.channel.value == -1  # not rewritten


def test_config_change_replaces_the_controller(
    actuator: RandomActuator,
) -> None:
    """A different config installs a new controller."""
    actuator.set_control_config(
        ControlConfig(ControlMethod.timer, time_on=3, time_off=5, value=135),
    )
    assert isinstance(actuator.controller, _TimerControl)
    assert actuator.controller.time_on == 3


def test_identical_config_keeps_the_controller(
    actuator: RandomActuator,
) -> None:
    """Rewriting the same config must not reset a running controller."""
    config = ControlConfig(
        ControlMethod.timer,
        time_on=3,
        time_off=5,
        value=135,
    )
    actuator.set_control_config(config)
    first = actuator.controller

    actuator.set_control_config(config)

    assert actuator.controller is first


def test_bad_config_keeps_the_old_controller(
    actuator: RandomActuator,
) -> None:
    """A malformed config is logged and ignored, not applied."""
    actuator.set_control_config(
        ControlConfig(ControlMethod.manual, value=150),
    )
    good = actuator.controller

    actuator.set_control_config(ControlConfig(ControlMethod.manual, value=[]))

    assert actuator.controller is good


def test_flow_config_is_rejected_without_a_calibration(
    actuator: RandomActuator,
) -> None:
    """mL/min against an uncalibrated pump must not reach the hardware."""
    actuator.set_control_config(
        ControlConfig(ControlMethod.manual, value=150),
    )
    good = actuator.controller

    actuator.set_control_config(
        ControlConfig(
            ControlMethod.manual,
            value=20,
            output_unit=OutputUnit.flow,
        ),
    )

    assert actuator.controller is good
    assert actuator.dispenser.unit is OutputUnit.duty


def test_flow_config_converts_the_demand(make_calibrated_actuator) -> None:
    """A manual 20 mL/min lands on the pin as 2000 counts."""
    actuator = make_calibrated_actuator()
    actuator.set_control_config(
        ControlConfig(
            ControlMethod.manual,
            value=20,
            output_unit=OutputUnit.flow,
        ),
    )

    actuator.write_output(0)

    assert actuator.channel.value == 2000


def test_total_volume_survives_a_config_change(
    make_calibrated_actuator,
) -> None:
    """The total records the physical pump, not the configuration."""
    actuator = make_calibrated_actuator()
    actuator.dispenser.total_volume = 12.5

    actuator.set_control_config(
        ControlConfig(
            ControlMethod.manual,
            value=20,
            output_unit=OutputUnit.flow,
        ),
    )

    assert actuator.dispenser.total_volume == 12.5


def test_calibrating_blocks_both_paths(make_calibrated_actuator) -> None:
    """A calibration run must not have a controller fighting it."""
    actuator = make_calibrated_actuator()
    actuator.set_control_config(
        ControlConfig(ControlMethod.manual, value=150),
    )
    actuator.calibrating = True

    actuator.write_output(0)
    actuator.tick()

    assert actuator.channel.value == ERROR_VALUE  # never written


def test_a_failed_sensor_read_holds_the_last_output(
    actuator: RandomActuator,
) -> None:
    """ERROR_VALUE is a sentinel, not a measurement.

    Regression: a failed pH probe reads -0.111, which would drive
    _OnBoundariesControl to dose base forever.
    """
    actuator.set_control_config(
        ControlConfig(ControlMethod.manual, value=150),
    )
    actuator.write_output(0)

    actuator.set_control_config(
        ControlConfig(ControlMethod.manual, value=900),
    )
    actuator.write_output(ERROR_VALUE)

    assert actuator.channel.value == 150


def test_control_period_reaches_the_dispenser(
    make_calibrated_actuator,
) -> None:
    """The Reactor stamps its period on; the guard has to see it."""
    actuator = make_calibrated_actuator()

    actuator.control_period = 42.0

    assert actuator.dispenser.control_period == 42.0
