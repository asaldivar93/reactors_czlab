"""Tests for fitting, saving and loading pump calibrations."""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

import pytest

from reactors_czlab.core.calibration import (
    CALIBRATION_ENV,
    CalibrationRun,
    calibration_path,
    fit_line,
    load_calibration,
    load_into,
    save_calibration,
)
from reactors_czlab.core.data import (
    Calibration,
    Channel,
    ControlConfig,
    ControlMethod,
    OutputUnit,
)
from reactors_czlab.core.dispenser import Dispenser


@pytest.fixture(autouse=True)
def _cal_dir(tmp_path, monkeypatch) -> None:
    """Keep every test out of the operator's real calibration directory."""
    monkeypatch.setenv(CALIBRATION_ENV, str(tmp_path))


def test_fit_recovers_a_known_line() -> None:
    """Points taken from flow = 0.01 * duty - 2 fit back to it."""
    points = [(500.0, 3.0), (1500.0, 13.0), (2500.0, 23.0)]

    a, b, r2 = fit_line(points)

    assert a == pytest.approx(0.01)
    assert b == pytest.approx(-2.0)
    assert r2 == pytest.approx(1.0)


def test_fit_rejects_too_few_distinct_duties() -> None:
    """Two measurements at the same duty do not define a line."""
    with pytest.raises(ValueError, match="distinct"):
        fit_line([(1000.0, 5.0), (1000.0, 5.2)])


def test_fit_rejects_a_non_positive_slope() -> None:
    """More duty must mean more flow, or the pump is wired backwards."""
    with pytest.raises(ValueError, match="slope"):
        fit_line([(500.0, 20.0), (2500.0, 4.0)])


def test_save_then_load_round_trips() -> None:
    """A saved calibration comes back with its points as tuples."""
    cal = Calibration(
        "R0_pwm0",
        a=0.01,
        b=-2.0,
        min_duty=400.0,
        max_duty=4000.0,
        dispense_duty=2000.0,
        points=[(500.0, 3.0), (2500.0, 23.0)],
        fitted_at="2026-07-27T10:00:00+00:00",
        r2=1.0,
    )

    save_calibration(cal)
    loaded = load_calibration("R0_pwm0")

    assert loaded == cal
    assert loaded.points == [(500.0, 3.0), (2500.0, 23.0)]


def test_load_returns_none_when_there_is_no_file() -> None:
    """A pump that has never been calibrated is not an error."""
    assert load_calibration("R0_pwm0") is None


def test_load_survives_a_corrupt_file() -> None:
    """A truncated file must not take the server down."""
    calibration_path("R0_pwm0").write_text("{not json", encoding="utf-8")

    assert load_calibration("R0_pwm0") is None


def test_load_rejects_a_non_positive_slope_on_disk() -> None:
    """A hand-edited file cannot install a line that cannot be inverted."""
    calibration_path("R0_pwm0").write_text(
        json.dumps({"file": "R0_pwm0", "a": 0.0, "b": 1.0}),
        encoding="utf-8",
    )

    assert load_calibration("R0_pwm0") is None


def test_load_survives_a_non_numeric_slope_on_disk() -> None:
    """A slope of the wrong type cannot escape as an uncaught TypeError.

    Regression: the comparison ``cal.a <= 0`` used to sit outside the
    file's try/except, so a hand-edited "a" that is not a number raised
    straight out of ``load_calibration``.
    """
    calibration_path("R0_pwm0").write_text(
        json.dumps({"file": "R0_pwm0", "a": "oops", "b": 1.0}),
        encoding="utf-8",
    )

    assert load_calibration("R0_pwm0") is None


def test_load_rejects_a_non_numeric_field_other_than_slope() -> None:
    """A wrong-typed field besides "a" cannot slip through as valid.

    Regression: only "a" was ever compared or coerced inside
    ``load_calibration``, so a bad ``min_duty`` used to come back as a
    ``Calibration`` object carrying a string where a float belongs,
    instead of being rejected like any other malformed file.
    """
    calibration_path("R0_pwm0").write_text(
        json.dumps(
            {
                "file": "R0_pwm0",
                "a": 0.01,
                "b": -2.0,
                "min_duty": "oops",
                "fitted_at": "2026-07-27T10:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    assert load_calibration("R0_pwm0") is None


def test_load_survives_an_oversized_integer_field() -> None:
    """A JSON integer too big for a C double cannot escape as OverflowError.

    Regression: ``float()`` raises ``OverflowError`` - not ``ValueError``
    - converting an integer JSON can represent but a C double cannot, and
    the guard used to only catch ``(OSError, ValueError, TypeError)``.
    """
    calibration_path("R0_pwm0").write_text(
        json.dumps({"file": "R0_pwm0", "a": 10**400, "b": 1.0}),
        encoding="utf-8",
    )

    assert load_calibration("R0_pwm0") is None


def test_load_survives_a_directory_creation_failure(monkeypatch) -> None:
    """A permission error making the calibration dir cannot crash it.

    Regression: ``calibration_path()`` (and the ``mkdir`` inside it) used
    to run before the try/except in ``load_calibration``, so a full disk
    or a permission error raised straight out of the function.
    """

    def _boom(self, *args, **kwargs) -> None:
        error_message = "permission denied"
        raise OSError(error_message)

    monkeypatch.setattr(Path, "mkdir", _boom)

    assert load_calibration("R0_pwm0") is None


def test_load_into_installs_the_stored_calibration() -> None:
    """A channel picks up what was saved under its calibration name."""
    save_calibration(
        Calibration("R0_pwm0", a=0.01, fitted_at="2026-07-27T10:00:00+00:00"),
    )
    channel = Channel("pwm0", "pwm", calibration=Calibration("R0_pwm0"))

    assert load_into(channel) is True
    assert channel.calibration.is_fitted is True
    assert channel.calibration.a == 0.01


def test_load_into_keeps_the_unfitted_calibration_when_absent() -> None:
    """With no stored file the channel keeps its placeholder calibration."""
    channel = Channel("pwm0", "pwm", calibration=Calibration("R0_pwm0"))

    assert load_into(channel) is False
    assert channel.calibration.is_fitted is False


def test_load_into_ignores_a_channel_with_no_calibration() -> None:
    """Channels that are not pumps are skipped, not crashed on."""
    assert load_into(Channel("pwm1", "pwm")) is False


class _FakeSleep:
    """Records how long the run asked to sleep and advances a clock."""

    def __init__(self, clock) -> None:
        self.clock = clock
        self.slept: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)
        # Real sleeps overshoot; the run must use the measured time.
        self.clock.advance(seconds * 1.1)


async def test_a_point_runs_the_pump_and_then_stops_it(
    make_calibrated_actuator,
    clock,
) -> None:
    """The pump is driven, then zeroed, and the interlock is released."""
    actuator = make_calibrated_actuator()
    run = CalibrationRun(actuator, clock=clock, sleep=_FakeSleep(clock))

    await run.calibrate_point(2000.0, 30.0)

    assert actuator.channel.value == 0
    assert actuator.calibrating is False


async def test_a_point_uses_the_measured_elapsed_time(
    make_calibrated_actuator,
    clock,
) -> None:
    """asyncio.sleep drifts; that drift must not enter the flow estimate."""
    actuator = make_calibrated_actuator()
    run = CalibrationRun(actuator, clock=clock, sleep=_FakeSleep(clock))

    await run.calibrate_point(2000.0, 30.0)
    run.record_point(16.5)

    # 33 s actually elapsed, so 16.5 mL is 30 mL/min, not 33.
    assert run.points == [(2000.0, pytest.approx(30.0))]


async def test_recording_without_a_run_is_refused(
    make_calibrated_actuator,
    clock,
) -> None:
    """A volume with no pending point has nothing to attach to."""
    run = CalibrationRun(make_calibrated_actuator(), clock=clock)

    assert "no point" in run.record_point(5.0).lower()
    assert run.points == []


async def test_a_run_rejects_an_out_of_range_duration(
    make_calibrated_actuator,
    clock,
) -> None:
    """Bounded so a fat finger cannot run a pump dry for an hour."""
    actuator = make_calibrated_actuator()
    run = CalibrationRun(actuator, clock=clock, sleep=_FakeSleep(clock))

    assert "seconds" in await run.calibrate_point(2000.0, 6000.0)
    assert actuator.calibrating is False


async def test_a_run_releases_the_pump_when_it_raises(
    make_calibrated_actuator,
    clock,
) -> None:
    """A crashed run must never leave a pump running."""

    async def boom(_seconds: float) -> None:
        error_message = "bus fell over"
        raise OSError(error_message)

    actuator = make_calibrated_actuator()
    run = CalibrationRun(actuator, clock=clock, sleep=boom)

    with pytest.raises(OSError, match="bus fell over"):
        await run.calibrate_point(2000.0, 30.0)

    assert actuator.calibrating is False
    assert actuator.channel.value == 0


async def test_fit_installs_and_stores_the_line(
    make_calibrated_actuator,
    clock,
) -> None:
    """A fit lands on the channel and on disk."""
    actuator = make_calibrated_actuator(fitted=False)
    run = CalibrationRun(actuator, clock=clock, sleep=_FakeSleep(clock))

    for duty, volume in ((1000.0, 5.0), (3000.0, 15.0)):
        await run.calibrate_point(duty, 60.0)
        run.record_point(volume)

    run.fit()

    # 5 mL and 15 mL over 2000 counts would be a slope of 0.005, but the
    # fake sleep overshoots by 10%, so the measured flows are 1/1.1 of that.
    cal = actuator.channel.calibration
    assert cal.is_fitted is True
    assert cal.a == pytest.approx(0.005 / 1.1)
    assert load_calibration("R0_pwm0").a == pytest.approx(cal.a)


async def test_fit_is_refused_with_one_point(
    make_calibrated_actuator,
    clock,
) -> None:
    """A single measurement does not define a line; keep the old one."""
    actuator = make_calibrated_actuator(fitted=False)
    run = CalibrationRun(actuator, clock=clock, sleep=_FakeSleep(clock))
    await run.calibrate_point(2000.0, 60.0)
    run.record_point(20.0)

    result = run.fit()

    assert "distinct" in result
    assert actuator.channel.calibration.is_fitted is False


async def test_fit_needs_a_calibration_slot_on_the_channel(clock) -> None:
    """A channel with no Calibration has no file to store under."""
    from reactors_czlab.core.actuator import RandomActuator
    from reactors_czlab.core.data import PhysicalInfo, PlcOutput

    info = PhysicalInfo(
        model="pwm",
        address=0,
        type=PlcOutput.pwm,
        channels=[Channel("pwm1", "pwm", pin="Q1.5")],
    )
    run = CalibrationRun(RandomActuator("R0:pwm1", info), clock=clock)

    assert "no calibration" in run.fit().lower()


async def test_clear_points_keeps_the_installed_calibration(
    make_calibrated_actuator,
    clock,
) -> None:
    """Restarting a run must not disturb the pump that is already good."""
    actuator = make_calibrated_actuator()
    run = CalibrationRun(actuator, clock=clock, sleep=_FakeSleep(clock))
    await run.calibrate_point(2000.0, 60.0)
    run.record_point(20.0)

    run.clear_points()

    assert run.points == []
    assert actuator.channel.calibration.is_fitted is True


async def test_reload_reinstalls_the_stored_calibration(
    make_calibrated_actuator,
    clock,
) -> None:
    """The runtime reload path."""
    actuator = make_calibrated_actuator(fitted=False)
    save_calibration(
        Calibration("R0_pwm0", a=0.02, fitted_at="2026-07-27T10:00:00+00:00"),
    )
    run = CalibrationRun(actuator, clock=clock)

    run.reload()

    assert actuator.channel.calibration.a == 0.02


async def test_set_duties_stores_the_bench_knobs(
    make_calibrated_actuator,
    clock,
) -> None:
    """min_duty and dispense_duty are adjustable without a refit."""
    actuator = make_calibrated_actuator()
    run = CalibrationRun(actuator, clock=clock)

    run.set_duties(500.0, 1500.0)

    assert actuator.channel.calibration.min_duty == 500.0
    assert actuator.channel.calibration.dispense_duty == 1500.0
    assert load_calibration("R0_pwm0").dispense_duty == 1500.0


async def test_set_duties_rejects_a_dispense_duty_below_the_floor(
    make_calibrated_actuator,
    clock,
) -> None:
    """Dispensing below the stall floor would never finish a bolus."""
    actuator = make_calibrated_actuator()
    run = CalibrationRun(actuator, clock=clock)

    result = run.set_duties(1500.0, 500.0)

    assert "stall" in result
    assert actuator.channel.calibration.dispense_duty == 2000.0


async def test_fit_re_derives_a_running_pid_controllers_limits(
    make_calibrated_actuator,
    clock,
) -> None:
    """A refit must not leave a flow-mode PID clamped to the old range.

    Regression: refitting a new slope changes what
    ``dispenser.demand_limits()`` returns, but nothing rebuilt the
    controller for it, so a running PID kept clamping to the range
    computed from the calibration it was built with.
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
    controller._integral_sum = 12.5  # pretend the PID has been running
    assert actuator.dispenser.demand_limits() == (0.0, 40.0)

    run = CalibrationRun(actuator, clock=clock, sleep=_FakeSleep(clock))
    for duty, volume in ((1000.0, 22.0), (3000.0, 66.0)):
        await run.calibrate_point(duty, 60.0)
        run.record_point(volume)

    run.fit()

    assert actuator.channel.calibration.a == pytest.approx(0.02)
    # The controller was not rebuilt: same object, runtime state intact.
    assert actuator.controller is controller
    assert controller._integral_sum == 12.5
    # But its clamp range followed the new calibration.
    assert (controller.min_val, controller.max_val) == (0.0, 80.0)


async def test_fit_re_derives_the_pid_integral_band_too(
    make_calibrated_actuator,
    clock,
) -> None:
    """A refit must move the anti-windup band with the clamp, not just
    the clamp itself - and a byte-identical config afterwards must not
    rebuild the controller or zero the integral it accrued.

    Regression: ``refresh_controller_limits()`` moved ``min_val``/
    ``max_val`` but not the PID's derived ``min_integral``/
    ``max_integral``. Those are ordinary compare=True fields, so a
    ControlFactory-built replacement for the SAME config then differed
    from the running controller only in the stale band, and
    ``set_control_config`` rebuilt it - zeroing the integral for a
    write that changed nothing.
    """
    actuator = make_calibrated_actuator()  # a=0.01, max_duty=4000
    config = ControlConfig(
        ControlMethod.pid,
        setpoint=1.0,
        output_unit=OutputUnit.flow,
    )
    actuator.set_control_config(config)
    controller = actuator.controller
    controller._integral_sum = 12.5
    assert (controller.min_integral, controller.max_integral) == (0.0, 40.0)

    run = CalibrationRun(actuator, clock=clock, sleep=_FakeSleep(clock))
    for duty, volume in ((1000.0, 22.0), (3000.0, 66.0)):
        await run.calibrate_point(duty, 60.0)
        run.record_point(volume)
    run.fit()

    # Consequence 1: the anti-windup band followed the new clamp.
    assert (controller.min_integral, controller.max_integral) == (
        0.0,
        80.0,
    )

    # Consequence 2: a byte-identical config must still compare equal
    # to the running controller, so it is not rebuilt and the integral
    # survives.
    actuator.set_control_config(config)
    assert actuator.controller is controller
    assert controller._integral_sum == 12.5


async def test_refit_reclamps_an_integral_above_the_shrunk_band(
    make_calibrated_actuator,
    clock,
) -> None:
    """A refit that lowers the achievable output must not leave the
    integral pinned above what the output can express.

    Without an immediate reclamp, the stored sum stays above the new
    band until the next control step catches up - a window in which
    the controller sits saturated, which is exactly the overdosing
    anti-windup exists to prevent.
    """
    actuator = make_calibrated_actuator()  # a=0.01 -> max flow 40 mL/min
    actuator.set_control_config(
        ControlConfig(
            ControlMethod.pid,
            setpoint=1.0,
            output_unit=OutputUnit.flow,
        ),
    )
    controller = actuator.controller
    controller._integral_sum = 35.0

    run = CalibrationRun(actuator, clock=clock, sleep=_FakeSleep(clock))
    # A shallower line: a=0.005 -> max flow at duty 4000 = 20 mL/min.
    for duty, volume in ((1000.0, 5.5), (3000.0, 16.5)):
        await run.calibrate_point(duty, 60.0)
        run.record_point(volume)

    run.fit()

    assert controller.max_integral == pytest.approx(20.0)
    assert controller._integral_sum == pytest.approx(20.0)


async def test_fit_refuses_a_stall_floor_above_the_dispense_duty(
    make_calibrated_actuator,
    clock,
) -> None:
    """A refit must not silently invert the live controller's clamp.

    Two high-duty points fit a line whose stall floor sits above the
    dispense duty already configured on the pump - installing it would
    make ``demand_limits()`` return an inverted (min > max) range for
    volume mode, and ``refresh_controller_limits()`` would push that
    straight onto a live controller. ``set_duties()`` already refuses
    this invariant; ``fit()`` must refuse it too, keeping the old
    calibration.
    """
    actuator = make_calibrated_actuator()  # dispense_duty defaults to 2000
    old_cal = actuator.channel.calibration
    run = CalibrationRun(actuator, clock=clock, sleep=_FakeSleep(clock))
    # flow = 0.015 * duty - 40 -> stall floor ~2667, above dispense_duty.
    for duty, volume in ((3000.0, 5.5), (4000.0, 22.0)):
        await run.calibrate_point(duty, 60.0)
        run.record_point(volume)

    result = run.fit()

    assert "stall" in result.lower()
    assert actuator.channel.calibration is old_cal


async def test_reload_does_not_push_an_inverted_range_onto_the_controller(
    make_calibrated_actuator,
    clock,
) -> None:
    """A hand-edited stored calibration cannot invert a live clamp.

    ``fit()`` and ``set_duties()`` both enforce dispense_duty >=
    min_duty, but a calibration file on disk is operator-editable and
    ``load_calibration`` only checks the slope's sign.
    ``refresh_controller_limits()`` is the last line of defense against
    installing ``min_val > max_val`` on a live controller.
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
    before = (controller.min_val, controller.max_val)

    save_calibration(
        Calibration(
            "R0_pwm0",
            a=0.015,
            b=-40.0,
            min_duty=2666.7,
            max_duty=2000.0,  # flow_at(2000) = -10: inverted for flow mode
            dispense_duty=2000.0,
            fitted_at="2026-07-27T10:00:00+00:00",
        ),
    )
    run = CalibrationRun(actuator, clock=clock)

    run.reload()

    assert (controller.min_val, controller.max_val) == before


async def test_set_duties_rejects_a_negative_min_duty(
    make_calibrated_actuator,
    clock,
) -> None:
    """A negative stall floor would disable the floor entirely."""
    actuator = make_calibrated_actuator()
    run = CalibrationRun(actuator, clock=clock)

    result = run.set_duties(-500.0, 100.0)

    assert "min duty" in result.lower()
    assert actuator.channel.calibration.min_duty == 400.0  # unchanged


async def test_stall_floor_is_bounded_by_max_duty(
    make_calibrated_actuator,
    clock,
) -> None:
    """A zero-flow point above max_duty must not push the floor past it.

    Regression: an operator's zero-flow measurement at a duty above the
    pump's configured ceiling was adopted as the stall floor verbatim,
    which could make min_duty > max_duty on the installed calibration -
    an inverted band that refresh_controller_limits() would then hand
    straight to a live controller.
    """
    actuator = make_calibrated_actuator()  # max_duty = 4000
    run = CalibrationRun(actuator, clock=clock, sleep=_FakeSleep(clock))
    # Raise dispense_duty first so the fit is not itself refused by the
    # min_duty <= dispense_duty invariant once the floor clamps to 4000.
    run.set_duties(0.0, 4000.0)
    # flow = 0.0032 * duty + 7.64 (positive slope), plus a zero-flow
    # reading at 4050 - ABOVE the pump's configured 4000 ceiling.
    for duty, volume in (
        (500.0, 5.5),
        (2000.0, 22.0),
        (3900.0, 42.9),
        (4050.0, 0.0),
    ):
        await run.calibrate_point(duty, 60.0)
        run.record_point(volume)

    result = run.fit()

    assert "fitted flow" in result  # the fit was accepted, not refused
    assert actuator.channel.calibration.min_duty == pytest.approx(4000.0)


async def test_stall_floor_uses_a_measured_zero_reading_as_evidence(
    make_calibrated_actuator,
    clock,
) -> None:
    """A point that measured no flow at all overrides the fitted
    x-intercept - it is direct evidence the pump does not turn there.

    Regression: without the override, the stall floor came only from
    the fitted line's x-intercept, ignoring a zero-flow measurement
    that puts the true floor higher.
    """
    actuator = make_calibrated_actuator(fitted=False)
    run = CalibrationRun(actuator, clock=clock, sleep=_FakeSleep(clock))
    # The line's own x-intercept sits below 800, but 800 measured no
    # flow at all: the floor must follow the direct evidence.
    for duty, volume in (
        (800.0, 0.0),
        (2000.0, 13.2),
        (4000.0, 30.8),
    ):
        await run.calibrate_point(duty, 60.0)
        run.record_point(volume)

    run.fit()

    assert actuator.channel.calibration.min_duty == pytest.approx(800.0)


async def test_calibrate_point_uses_the_calibrating_property(
    make_calibrated_actuator,
    clock,
) -> None:
    """calibrate_point must set the interlock through the property, not
    a private attribute - only the property banks the dispenser's
    accrual clock on each edge.

    Regression: setting ``self.actuator._calibrating`` directly instead
    of ``self.actuator.calibrating`` bypasses ``Dispenser.reset()``, so
    the run's duration gets attributed to whatever duty was running
    immediately before it started.
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

    run = CalibrationRun(actuator, clock=clock, sleep=_FakeSleep(clock))
    await run.calibrate_point(500.0, 30.0)

    actuator.write_output(0)  # first decision after the run

    assert actuator.dispenser.total_volume == pytest.approx(0.0)


async def test_calibrate_point_resets_old_value_so_the_next_write_lands(
    make_calibrated_actuator,
    clock,
) -> None:
    """After a run, the control loop must rewrite the resumed duty.

    Regression: ``write()`` bypasses the change guard and would leave
    ``channel.old_value`` stale at whatever duty was running before the
    calibration point if that reset line were dropped. If the
    controller's next decision happens to match that stale value,
    ``_write_if_changed`` would skip the write even though the pin is
    actually sitting at 0 from the run.
    """
    actuator = make_calibrated_actuator()
    actuator.set_control_config(
        ControlConfig(ControlMethod.manual, value=500),
    )
    actuator.write_output(0)  # pin at 500, old_value == 500
    assert actuator.channel.value == 500

    run = CalibrationRun(actuator, clock=clock, sleep=_FakeSleep(clock))
    await run.calibrate_point(500.0, 30.0)  # same duty, then zeros the pin
    assert actuator.channel.value == 0

    actuator.write_output(0)  # the control loop's next decision

    assert actuator.channel.value == 500  # must land, not be skipped


async def test_reload_re_derives_the_controllers_limits(
    make_calibrated_actuator,
    clock,
) -> None:
    """reload() must also refresh a live controller's clamp range.

    Regression: only fit() called refresh_controller_limits(); reload()
    installed the stored calibration on the channel but left a running
    flow-mode controller clamped to the old range.
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

    save_calibration(
        Calibration(
            "R0_pwm0",
            a=0.02,
            max_duty=4000.0,
            dispense_duty=2000.0,
            fitted_at="2026-07-27T10:00:00+00:00",
        ),
    )
    run = CalibrationRun(actuator, clock=clock)

    run.reload()

    assert (controller.min_val, controller.max_val) == (0.0, 80.0)


async def test_set_duties_re_derives_the_controllers_limits(
    make_calibrated_actuator,
    clock,
) -> None:
    """set_duties() must also refresh a live controller's clamp range.

    Regression: only fit() called refresh_controller_limits();
    set_duties() changed dispense_duty - which volume-mode
    demand_limits() depends on - but left a running volume-mode
    controller clamped to the old range.
    """
    actuator = make_calibrated_actuator()  # dispense_duty=2000 -> 20 mL/min
    actuator.control_period = 10.0
    actuator.set_control_config(
        ControlConfig(
            ControlMethod.pid,
            setpoint=1.0,
            output_unit=OutputUnit.volume,
        ),
    )
    controller = actuator.controller
    expected_before = 20.0 * 10.0 / 60.0
    assert controller.max_val == pytest.approx(expected_before)

    run = CalibrationRun(actuator, clock=clock)
    run.set_duties(400.0, 4000.0)  # dispense_duty raised -> 40 mL/min

    expected_after = 40.0 * 10.0 / 60.0
    assert controller.max_val == pytest.approx(expected_after)


async def test_calibrate_point_refuses_to_run_while_already_running(
    make_calibrated_actuator,
    clock,
) -> None:
    """A second point cannot start while one is already driving the pump.

    Regression: without the ``self._running`` guard, two concurrent
    ``calibrate_point`` calls could both reach ``actuator.write()``,
    racing the pump between two different duties.
    """
    actuator = make_calibrated_actuator()
    started = asyncio.Event()

    async def slow_sleep(_seconds: float) -> None:
        started.set()
        await asyncio.Event().wait()  # never completes on its own

    run = CalibrationRun(actuator, clock=clock, sleep=slow_sleep)
    first = asyncio.create_task(run.calibrate_point(500.0, 30.0))
    await started.wait()

    result = await asyncio.wait_for(
        run.calibrate_point(1000.0, 30.0),
        timeout=1.0,
    )

    assert "already calibrating" in result.lower()
    assert actuator.channel.value == 500  # only the first write landed

    first.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await first
