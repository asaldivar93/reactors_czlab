"""Tests for the Actuator base class and its control configuration."""

from __future__ import annotations

from reactors_czlab.core.actuator import RandomActuator
from reactors_czlab.core.control import _ManualControl, _TimerControl
from reactors_czlab.core.data import ControlConfig, ControlMethod


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
