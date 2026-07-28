"""Tests for fitting, saving and loading pump calibrations."""

from __future__ import annotations

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
