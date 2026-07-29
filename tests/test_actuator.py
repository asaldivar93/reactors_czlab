"""Tests for the Actuator base class and its control configuration."""

from __future__ import annotations

import pytest

from reactors_czlab.core.actuator import RandomActuator
from reactors_czlab.core.control import _ManualControl, _TimerControl
from reactors_czlab.core.data import (
    ERROR_VALUE,
    Calibration,
    ControlConfig,
    ControlMethod,
    OutputUnit,
)
from reactors_czlab.core.dispenser import Dispenser


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


def test_calibrating_blocks_a_bolus_in_flight(
    make_calibrated_actuator,
    clock,
    monkeypatch,
) -> None:
    """tick() must not finish a bolus while a calibration run owns the pump.

    Regression: mutation testing showed the previous version of this
    test stayed green with ``if self.calibrating: return`` deleted from
    ``tick()``, because no bolus was ever armed - ``dispenser.tick()``
    returned ``None`` regardless of the guard. This arms a real bolus,
    lets its deadline pass, then checks that ``tick()`` does not finish
    it while calibrating.

    ``dispenser.reset()`` is stubbed out here so the ``calibrating``
    property's own edge-triggered reset (see
    ``test_calibrating_freezes_the_dispensers_clock``) does not clear
    the bolus before ``tick()`` gets a chance to - this test isolates
    ``tick()``'s own guard.
    """
    actuator = make_calibrated_actuator()
    actuator.dispenser = Dispenser(
        OutputUnit.volume,
        actuator.channel,
        actuator.control_period,
        clock=clock,
    )
    actuator.set_control_config(
        ControlConfig(
            ControlMethod.manual,
            value=1.0,
            output_unit=OutputUnit.volume,
        ),
    )
    actuator.write_output(0)  # arms a 3 s bolus (1 mL at 20 mL/min)
    clock.advance(3.0)  # past the bolus deadline

    monkeypatch.setattr(actuator.dispenser, "reset", lambda: None)
    writes = []
    monkeypatch.setattr(actuator, "write", writes.append)
    actuator.calibrating = True

    actuator.tick()

    assert writes == []


def test_calibrating_freezes_the_dispensers_clock(
    make_calibrated_actuator,
    clock,
) -> None:
    """A calibration run must not inject phantom volume into the total.

    Regression: ``calibrating`` used to be a plain attribute, so it
    froze the dispenser's accrual clock without resetting it. The first
    decision after the run ended then attributed the run's entire
    duration to the pre-calibration duty.
    """
    actuator = make_calibrated_actuator()
    actuator.dispenser = Dispenser(
        OutputUnit.duty,
        actuator.channel,
        actuator.control_period,
        clock=clock,
    )
    actuator.set_control_config(
        ControlConfig(ControlMethod.manual, value=2000),
    )
    actuator.write_output(0)  # settles at 2000 counts -> 20 mL/min

    actuator.calibrating = True
    clock.advance(300.0)  # a long calibration run
    actuator.calibrating = False

    actuator.write_output(0)  # first real decision after the run

    assert actuator.dispenser.total_volume == pytest.approx(0.0)


def test_calibrating_blocks_a_decision(
    make_calibrated_actuator,
    monkeypatch,
) -> None:
    """write_output() must not drive a pump a calibration run owns.

    Regression: mutation testing showed the suite stayed green with
    ``if self.calibrating: return`` deleted from ``write_output()`` -
    the exact twin of the ``tick()`` guard that
    ``test_calibrating_blocks_a_bolus_in_flight`` pins. Both arms of
    the interlock now have a test; without this one, a calibration
    point's own duty could be overwritten mid-measurement by the
    sampling loop, silently corrupting the fit that follows.
    """
    actuator = make_calibrated_actuator()
    actuator.set_control_config(
        ControlConfig(ControlMethod.manual, value=2000),
    )
    actuator.calibrating = True
    writes = []
    monkeypatch.setattr(actuator, "write", writes.append)

    actuator.write_output(0)

    assert writes == []


def test_a_unit_swap_stops_a_bolus_in_flight(
    make_calibrated_actuator,
    clock,
) -> None:
    """Changing the output unit mid-bolus must turn the pump off.

    Regression: the swap called ``Dispenser.reset()``, which discards
    the bolus deadline, but never honoured that method's "the caller
    must write 0" contract - the other two callers, ``Reactor.stop()``
    and ``CalibrationRun.calibrate_point()``, do. The pump was left at
    the dispense duty with nothing that would ever stop it: the new
    dispenser has no deadline to expire and, in duty or flow mode, its
    ``tick()`` returns ``None`` forever. Only the next successful
    ``write_output()`` could clear it, and ``write_output()`` returns
    early for as long as the reference sensor reads ``ERROR_VALUE`` -
    so on a failed probe the pump ran unbounded, from one ordinary
    operator write of ``output_unit``.
    """
    actuator = make_calibrated_actuator()
    actuator.dispenser = Dispenser(
        OutputUnit.volume,
        actuator.channel,
        actuator.control_period,
        clock=clock,
    )
    actuator.set_control_config(
        ControlConfig(
            ControlMethod.manual,
            value=1.0,
            output_unit=OutputUnit.volume,
        ),
    )
    actuator.write_output(0)  # arms a 3 s bolus at 2000 counts
    assert actuator.channel.value == 2000

    actuator.set_control_config(
        ControlConfig(ControlMethod.manual, value=0),
    )

    assert actuator.channel.value == 0
    # old_value has to follow, or the next decision is compared against
    # a duty the pin is no longer at.
    assert actuator.channel.old_value == 0

    # A dead probe holds write_output() off indefinitely; nothing may
    # bring the pump back up on its own.
    for _ in range(200):
        clock.advance(1.0)
        actuator.write_output(ERROR_VALUE)
        actuator.tick()

    assert actuator.channel.value == 0


def test_a_unit_swap_does_not_disturb_a_calibration_run(
    make_calibrated_actuator,
    clock,
    monkeypatch,
) -> None:
    """An OPC output_unit write mid-calibration must not touch the pin.

    Regression: the unit-swap write-0 added to stop a bolus (see
    ``test_a_unit_swap_stops_a_bolus_in_flight``) was the one
    pin-writing path in the class not guarded on ``calibrating`` -
    ``write_output()`` and ``tick()`` both return early while a
    calibration run owns the pump. A run drives the pump directly, so
    an operator ``output_unit`` write routed through
    ``set_control_config()`` would zero the pump under the run's feet.
    The run still reports its requested duration, and the operator
    records the shortened volume against the intended duty - a silently
    wrong calibration point that then fits cleanly. ``calibrate_point()``'s
    own ``finally`` writes 0 when the run genuinely ends.
    """
    actuator = make_calibrated_actuator()
    actuator.dispenser = Dispenser(
        OutputUnit.volume,
        actuator.channel,
        actuator.control_period,
        clock=clock,
    )
    actuator.set_control_config(
        ControlConfig(
            ControlMethod.manual,
            value=1.0,
            output_unit=OutputUnit.volume,
        ),
    )
    actuator.write_output(0)  # the pump is up at the dispense duty
    assert actuator.channel.value == 2000

    actuator.calibrating = True
    writes = []
    monkeypatch.setattr(actuator, "write", writes.append)

    # A real unit change, so the swap branch runs and reaches the
    # write-0 that must now be held off while calibrating.
    actuator.set_control_config(
        ControlConfig(ControlMethod.manual, value=0),
    )

    assert writes == []


def test_control_period_rejects_a_non_positive_period(
    actuator: RandomActuator,
) -> None:
    """A zero or negative period would silently disable the volume guard.

    Regression: ``Dispenser.__init__`` raises on a non-positive
    ``control_period``, but the plain-attribute setter did not, so a
    bad value was accepted silently here and only blew up later, from
    an unrelated ``set_control_config`` call.
    """
    original = actuator.control_period

    with pytest.raises(ValueError, match="control_period"):
        actuator.control_period = 0.0

    assert actuator.control_period == original
    assert actuator.dispenser.control_period == original


def test_unit_swap_accrues_the_outgoing_dispensers_partial_interval(
    make_calibrated_actuator,
    clock,
) -> None:
    """Changing units must not drop volume still in flight.

    Regression: the outgoing dispenser's total was copied without
    forcing it to accrue first, so the interval since its last
    accounting point was silently dropped on every unit change.
    """
    actuator = make_calibrated_actuator()
    actuator.dispenser = Dispenser(
        OutputUnit.duty,
        actuator.channel,
        actuator.control_period,
        clock=clock,
    )
    actuator.set_control_config(
        ControlConfig(ControlMethod.manual, value=2000),
    )
    actuator.write_output(0)  # duty 2000 -> 20 mL/min, accruing
    clock.advance(0.3)  # 0.3 s at 20 mL/min -> 0.1 mL

    actuator.set_control_config(
        ControlConfig(
            ControlMethod.manual,
            value=20,
            output_unit=OutputUnit.flow,
        ),
    )

    assert actuator.dispenser.total_volume == pytest.approx(0.1)


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


def test_refresh_controller_limits_zeros_a_non_positive_range(
    make_calibrated_actuator,
) -> None:
    """A demand range that has gone non-positive is zeroed, not left
    stale.

    Regression: this method used to ``return`` on a non-positive range,
    leaving the controller's old (positive) limits in place. A clamped
    controller (PID) could then still command a positive demand against
    a calibration that could no longer deliver it, dividing by zero in
    ``Dispenser._start_bolus``.

    ``CalibrationRun.fit()``/``set_duties()``/``reload()`` all now
    refuse a calibration that would produce this range before it ever
    reaches the channel (``Calibration.installable_reason()``), so this
    path is not expected to be reachable through them any more. It is
    pinned here by installing the bad calibration directly - exactly
    what a bench script or a future install site that forgets to
    validate could still do - to verify this method's own behaviour
    independently of those guards.
    """
    actuator = make_calibrated_actuator()  # a=0.01, max_duty=4000
    actuator.set_control_config(
        ControlConfig(
            ControlMethod.pid,
            setpoint=1.0,
            output_unit=OutputUnit.flow,
        ),
    )
    controller = actuator.controller
    assert (controller.min_val, controller.max_val) == (0.0, 40.0)

    # Bypass every install-site guard and corrupt the live calibration
    # directly - flow mode's demand_limits() reads flow_at(max_duty),
    # which is negative for this line.
    actuator.channel.calibration = Calibration(
        "R0_pwm0",
        a=0.01,
        b=-50.0,
        max_duty=4000.0,
        fitted_at="2026-07-27T10:00:00+00:00",
    )

    actuator.refresh_controller_limits()

    assert (controller.min_val, controller.max_val) == (0.0, 0.0)
