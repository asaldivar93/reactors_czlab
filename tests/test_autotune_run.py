"""Safety and state-machine tests for live pH autotune runs."""

from __future__ import annotations

import math
from dataclasses import replace
from types import SimpleNamespace

import pytest

from reactors_czlab.core.autotune import (
    AutotuneContext,
    AutotuneCoordinator,
    AutotunePhase,
    CycleSummary,
    RelayTuneConfig,
    cycle_quality_reason,
    default_dose_budget_ml,
    period_quality_reason,
    robust_noise_sigma,
)
from reactors_czlab.core.calibration import CalibrationRun
from reactors_czlab.core.control import _PidControl
from reactors_czlab.core.data import (
    ERROR_VALUE,
    ControlConfig,
    ControlMethod,
    OutputUnit,
)
from reactors_czlab.core.dispenser import Dispenser
from reactors_czlab.core.ph_model import Chemistry, state_from_ph

NOISY_BASELINE = [7.0, 7.006, 6.994, 7.006, 6.994, 7.006, 6.994]


def _configured_pair(make_calibrated_actuator, clock, *, period: float = 10.0):
    base = make_calibrated_actuator("R0:base")
    acid = make_calibrated_actuator("R0:acid")
    for actuator, backwards in ((base, False), (acid, True)):
        actuator.control_period = period
        reason = actuator.set_control_config(
            ControlConfig(
                ControlMethod.pid,
                setpoint=7.0,
                output_unit=OutputUnit.volume,
                backwards=backwards,
            ),
        )
        assert reason is None
        actuator.dispenser = Dispenser(
            OutputUnit.volume,
            actuator.channel,
            period,
            clock=clock,
        )
    return base, acid


def _started_run(
    make_calibrated_actuator,
    clock,
    *,
    config: RelayTuneConfig | None = None,
    period: float = 10.0,
):
    base, acid = _configured_pair(make_calibrated_actuator, clock, period=period)
    sensor = SimpleNamespace(
        id="R0:ph",
        channels=[SimpleNamespace(units="pH")],
    )
    pairings = {"R0:ph": [(base.id, 0), (acid.id, 0)]}
    context = AutotuneContext(
        "R0",
        5.0,
        {sensor.id: sensor},
        {base.id: base, acid.id: acid},
        lambda: pairings,
    )
    live_config = config or RelayTuneConfig(acknowledge_other_loops=True)
    coordinator = AutotuneCoordinator(context, clock=clock)
    run = coordinator.start(sensor.id, base.id, acid.id, live_config)
    return run, coordinator, base, acid, pairings


def _finish_baseline(run, clock, values=None) -> None:
    readings = values or [7.0] * 7
    for index, ph in enumerate(readings):
        target = index * 10.0
        clock.advance(target - clock.now)
        run.sample(ph)


def _relay_step(run, clock, ph: float, *, period: float = 10.0) -> None:
    run.sample(ph)
    if run.is_active:
        # Mirror the real 20 Hz actuator loop until the owned dose ends.
        elapsed = 0.0
        while elapsed < period and (run.base.channel.value or run.acid.channel.value):
            step = min(0.05, period - elapsed)
            clock.advance(step)
            elapsed += step
            run.tick()
        clock.advance(period - elapsed)


def _drive_cycles(run, clock, *, high: float = 7.08, low: float = 6.92) -> None:
    sequence = [high, high, low, low] * 12
    clock.advance(10.0)
    for ph in sequence:
        _relay_step(run, clock, ph)
        if not run.is_active:
            break


def _cycle(period: float = 40.0, amplitude: float = 0.08, ratio: float = 1.0) -> CycleSummary:
    return CycleSummary(
        0.0,
        period,
        period,
        7.0 + amplitude,
        7.0 - amplitude,
        amplitude,
        20.0,
        20.0 / ratio,
        ratio,
        0.4,
        0.4,
        0.4,
        0.4,
        2,
        2,
    )


def test_normal_run_identifies_from_actual_delivered_doses(
    make_calibrated_actuator,
    clock,
) -> None:
    run, _, base, acid, _ = _started_run(make_calibrated_actuator, clock)
    _finish_baseline(run, clock)
    _drive_cycles(run, clock)

    assert run.phase is AutotunePhase.identified
    assert run.result is not None
    cycles = run.result.cycles
    actual_base = sum(cycle.actual_base_ml for cycle in cycles) / sum(cycle.base_requests for cycle in cycles)
    actual_acid = sum(cycle.actual_acid_ml for cycle in cycles) / sum(cycle.acid_requests for cycle in cycles)
    expected_ku = 2.0 * (actual_base + actual_acid) / (math.pi * (0.08**2 - 0.02**2) ** 0.5)
    assert run.result.identification.Ku == pytest.approx(expected_ku)
    assert run.result.identification.Pu == pytest.approx(40.0)
    assert run.result.identification.cycles_used == 4
    assert run.result.actual_dose_ml > 0
    assert base.channel.value == acid.channel.value == 0.0
    assert base.autotune_owner is acid.autotune_owner is None
    assert not base.calibrating and not acid.calibrating


def test_baseline_detrends_and_rejects_hysteresis_below_twice_noise(
    make_calibrated_actuator,
    clock,
) -> None:
    times = [index * 10.0 for index in range(7)]
    drift = [7.0 + 0.002 * value for value in times]
    assert robust_noise_sigma(times, drift) == pytest.approx(0.0, abs=1e-12)

    run, *_ = _started_run(make_calibrated_actuator, clock)
    noise = [0.0, 0.015, -0.012, 0.020, -0.018, 0.011, -0.005]
    noisy_drift = [value + noise[index] for index, value in enumerate(drift)]
    _finish_baseline(run, clock, noisy_drift)

    assert run.phase is AutotunePhase.failed
    assert "2*sigma" in run.message


def test_adaptation_scales_both_doses_and_preserves_ratio(
    make_calibrated_actuator,
    clock,
) -> None:
    config = RelayTuneConfig(
        base_dose_ml=0.10,
        acid_dose_ml=0.20,
        acknowledge_other_loops=True,
    )
    run, *_ = _started_run(make_calibrated_actuator, clock, config=config)
    _finish_baseline(run, clock, NOISY_BASELINE)
    # The cycle clears hysteresis but not 3*sigma, so it is not yet
    # distinguishable enough from the measured baseline noise.
    clock.advance(10.0)
    for ph in [7.025, 7.025, 6.975, 6.975] * 3:
        _relay_step(run, clock, ph)
        if run.phase is AutotunePhase.adapting:
            break

    assert run.phase is AutotunePhase.adapting
    assert run.base_dose_ml == pytest.approx(0.20)
    assert run.acid_dose_ml == pytest.approx(0.40)
    assert run.acid_dose_ml / run.base_dose_ml == pytest.approx(2.0)


def test_adaptation_fails_when_required_dose_is_not_deliverable(
    make_calibrated_actuator,
    clock,
) -> None:
    config = RelayTuneConfig(
        base_dose_ml=0.30,
        acid_dose_ml=0.30,
        acknowledge_other_loops=True,
    )
    run, *_ = _started_run(make_calibrated_actuator, clock, config=config, period=1.0)
    _finish_baseline(run, clock, NOISY_BASELINE)
    clock.advance(1.0)
    for ph in [7.025, 7.025, 6.975, 6.975] * 3:
        _relay_step(run, clock, ph, period=1.0)
        if not run.is_active:
            break

    assert run.phase is AutotunePhase.failed
    assert "cannot be delivered" in run.message


def test_adaptation_fails_after_the_configured_attempt_limit(
    make_calibrated_actuator,
    clock,
) -> None:
    config = RelayTuneConfig(
        base_dose_ml=0.10,
        acid_dose_ml=0.10,
        max_adaptations=1,
        acknowledge_other_loops=True,
    )
    run, *_ = _started_run(make_calibrated_actuator, clock, config=config)
    _finish_baseline(run, clock, NOISY_BASELINE)
    clock.advance(10.0)

    for ph in [7.025, 7.025, 6.975, 6.975] * 12:
        _relay_step(run, clock, ph)
        if not run.is_active:
            break

    assert run.phase is AutotunePhase.failed
    assert "adequate relay amplitude could not be reached" in run.message


@pytest.mark.parametrize(
    ("reading", "message"),
    [(ERROR_VALUE, "ERROR_VALUE"), (float("nan"), "non-finite")],
)
def test_immediate_sensor_aborts(
    make_calibrated_actuator,
    clock,
    reading,
    message,
) -> None:
    run, *_ = _started_run(make_calibrated_actuator, clock)
    run.sample(reading)
    assert run.phase is AutotunePhase.aborted
    assert message in run.message


def test_timeout_configuration_pairing_and_two_sample_band_aborts(
    make_calibrated_actuator,
    clock,
) -> None:
    timeout_config = RelayTuneConfig(max_minutes=1.1, acknowledge_other_loops=True)
    timeout, *_ = _started_run(make_calibrated_actuator, clock, config=timeout_config)
    clock.advance(66.0)
    timeout.sample(7.0)
    assert timeout.phase is AutotunePhase.aborted
    assert "timed out" in timeout.message

    clock.now = 0.0
    config_loss, _, base, _, _ = _started_run(make_calibrated_actuator, clock)
    base.controller = _PidControl(setpoint=8.0)
    config_loss.sample(7.0)
    assert config_loss.phase is AutotunePhase.aborted
    assert "configuration loss" in config_loss.message

    clock.now = 0.0
    pairing_loss, _, _, _, pairings = _started_run(make_calibrated_actuator, clock)
    pairings["R0:ph"].clear()
    pairing_loss.sample(7.0)
    assert pairing_loss.phase is AutotunePhase.aborted
    assert "pairing loss" in pairing_loss.message

    clock.now = 0.0
    band, *_ = _started_run(make_calibrated_actuator, clock)
    band.sample(5.9)
    clock.advance(10.0)
    band.sample(5.9)
    assert band.phase is AutotunePhase.aborted
    assert "twice" in band.message


def test_actual_dose_exhaustion_and_operator_abort_share_cleanup(
    make_calibrated_actuator,
    clock,
) -> None:
    config = RelayTuneConfig(
        dose_budget_ml=0.4,
        acknowledge_budget_override=True,
        acknowledge_other_loops=True,
    )
    run, _, base, acid, _ = _started_run(make_calibrated_actuator, clock, config=config)
    _finish_baseline(run, clock)
    clock.advance(10.0)
    _relay_step(run, clock, 7.08)
    _relay_step(run, clock, 7.08)
    _relay_step(run, clock, 7.08)
    assert run.phase is AutotunePhase.aborted
    assert "dose budget" in run.message
    assert base.channel.value == acid.channel.value == 0.0

    clock.now = 0.0
    operator, _, base, acid, _ = _started_run(make_calibrated_actuator, clock)
    operator.abort()
    assert operator.phase is AutotunePhase.aborted
    assert base.autotune_owner is acid.autotune_owner is None


def test_exception_cleanup_banks_partial_delivery(
    make_calibrated_actuator,
    clock,
    monkeypatch,
) -> None:
    run, _, base, acid, _ = _started_run(make_calibrated_actuator, clock)
    _finish_baseline(run, clock)
    clock.advance(10.0)
    run.sample(7.08)
    clock.advance(0.3)

    def explode(_owner, _volume):
        raise RuntimeError("injected demand failure")

    monkeypatch.setattr(base, "autotune_demand", explode)
    run.sample(6.92)

    assert run.phase is AutotunePhase.failed
    assert "injected demand failure" in run.message
    assert run.actual_dose_ml == pytest.approx(0.1)
    assert base.channel.value == acid.channel.value == 0.0
    assert base.autotune_owner is acid.autotune_owner is None


def test_ownership_loss_recovers_orphaned_delivery_and_cleans_both_pumps(
    make_calibrated_actuator,
    clock,
) -> None:
    run, _, base, acid, _ = _started_run(make_calibrated_actuator, clock)
    _finish_baseline(run, clock)
    clock.advance(10.0)
    run.sample(7.08)  # switches to acid and starts a 0.2 mL dose
    clock.advance(0.3)  # 0.1 mL has physically run
    acid._autotune_owner = None

    run.sample(7.0)

    assert run.phase is AutotunePhase.aborted
    assert "ownership was lost" in run.message
    assert run.actual_dose_ml == pytest.approx(0.1)
    for actuator in (base, acid):
        assert actuator.channel.value == 0.0
        assert actuator.channel.old_value == 0.0
        assert actuator.autotune_owner is None
        assert not actuator.calibrating


def test_terminal_status_preserves_cleanup_errors_and_attempts_both_releases(
    make_calibrated_actuator,
    clock,
    monkeypatch,
) -> None:
    run, _, base, acid, _ = _started_run(make_calibrated_actuator, clock)
    attempted: list[str] = []
    release_base = base.release_autotune
    release_acid = acid.release_autotune

    def failing_release(owner) -> None:
        attempted.append(base.id)
        release_base(owner)
        raise RuntimeError("injected release failure")

    def recorded_release(owner) -> None:
        attempted.append(acid.id)
        release_acid(owner)

    monkeypatch.setattr(base, "release_autotune", failing_release)
    monkeypatch.setattr(acid, "release_autotune", recorded_release)

    run.abort()

    assert run.phase is AutotunePhase.aborted
    assert attempted == [base.id, acid.id]
    assert "cleanup errors" in run.message
    assert "injected release failure" in run.message
    assert base.autotune_owner is acid.autotune_owner is None


def test_actuator_owner_only_uses_volume_dispenser_while_calibrating(
    make_calibrated_actuator,
    clock,
) -> None:
    base, _ = _configured_pair(make_calibrated_actuator, clock)
    owner = object()
    stale = object()
    base.claim_autotune(owner)

    with pytest.raises(PermissionError):
        base.release_autotune(stale)
    assert base.autotune_owner is owner
    with pytest.raises(PermissionError):
        base.autotune_demand(stale, 0.2)
    base.autotune_demand(owner, 0.2)
    assert base.calibrating
    assert base.channel.value == base.channel.calibration.dispense_duty
    base.write_output(6.0)
    base.tick()
    assert base.channel.value == base.channel.calibration.dispense_duty
    with pytest.raises(RuntimeError, match="autotune owner"):
        base.calibrating = False
    clock.advance(0.6)
    base.autotune_tick(owner)
    assert base.dispenser.total_volume == pytest.approx(0.2)
    base.release_autotune(owner)
    with pytest.raises(PermissionError):
        base.autotune_tick(owner)


@pytest.mark.asyncio
async def test_pump_calibration_cannot_steal_autotune_owner(
    make_calibrated_actuator,
    clock,
) -> None:
    run, _, base, _, _ = _started_run(make_calibrated_actuator, clock)
    calibration = CalibrationRun(base, clock=clock)
    result = await calibration.calibrate_point(500.0, 1.0)

    assert "active autotune" in result
    assert base.autotune_owner is run


def test_coordinator_refuses_a_second_active_run(
    make_calibrated_actuator,
    clock,
) -> None:
    _, coordinator, base, acid, _ = _started_run(make_calibrated_actuator, clock)
    with pytest.raises(RuntimeError, match="active autotune"):
        coordinator.start("R0:ph", base.id, acid.id, RelayTuneConfig(acknowledge_other_loops=True))


def test_cycle_quality_rejects_every_bad_shape() -> None:
    assert "non-finite" in cycle_quality_reason(replace(_cycle(), amplitude=float("nan")), 0.02, 0.005)
    assert "amplitude" in cycle_quality_reason(_cycle(amplitude=0.02), 0.02, 0.005)
    assert "asymmetry" in cycle_quality_reason(_cycle(ratio=0.1), 0.02, 0.005)
    assert "asymmetry" in cycle_quality_reason(_cycle(ratio=6.0), 0.02, 0.005)
    assert cycle_quality_reason(_cycle(), 0.02, 0.005) is None

    stable = [_cycle(period=value) for value in (40.0, 40.0, 40.0, 40.0)]
    variable = [_cycle(period=value) for value in (40.0, 40.0, 40.0, 80.0)]
    assert period_quality_reason(stable) is None
    assert "25%" in period_quality_reason(variable)


def test_control_period_warning_and_deliverability_bounds(
    make_calibrated_actuator,
    clock,
) -> None:
    run, *_ = _started_run(make_calibrated_actuator, clock, period=40.0)
    assert len(run.warnings) == 2
    run.abort()

    clock.now = 0.0
    too_small = RelayTuneConfig(
        base_dose_ml=0.001,
        acid_dose_ml=0.2,
        acknowledge_other_loops=True,
    )
    base, acid = _configured_pair(make_calibrated_actuator, clock)
    sensor = SimpleNamespace(id="R0:ph", channels=[SimpleNamespace(units="pH")])
    context = AutotuneContext(
        "R0",
        5.0,
        {sensor.id: sensor},
        {base.id: base, acid.id: acid},
        lambda: {sensor.id: [(base.id, 0), (acid.id, 0)]},
    )
    with pytest.raises(ValueError, match="deliverable range"):
        AutotuneCoordinator(context, clock=clock).start(sensor.id, base.id, acid.id, too_small)


def test_preflight_requires_acknowledgements_and_fitted_pairing(
    make_calibrated_actuator,
    clock,
) -> None:
    base, acid = _configured_pair(make_calibrated_actuator, clock)
    sensor = SimpleNamespace(id="R0:ph", channels=[SimpleNamespace(units="pH")])
    pairings = {sensor.id: [(base.id, 0), (acid.id, 0)]}
    context = AutotuneContext(
        "R0",
        5.0,
        {sensor.id: sensor},
        {base.id: base, acid.id: acid},
        lambda: pairings,
    )
    with pytest.raises(ValueError, match="other loops"):
        AutotuneCoordinator(context, clock=clock).start(sensor.id, base.id, acid.id)

    override = RelayTuneConfig(
        dose_budget_ml=10.0,
        acknowledge_other_loops=True,
    )
    with pytest.raises(ValueError, match="override requires"):
        AutotuneCoordinator(context, clock=clock).start(sensor.id, base.id, acid.id, override)


def test_default_budget_uses_effective_endpoints_and_each_titrant_molarity() -> None:
    volume_l = 5.0
    phosphate_molar = 0.014
    setpoint = 9.5
    base_molar = 0.25
    acid_molar = 1.0
    chemistry = Chemistry()
    safe_low = max(4.0, setpoint - 1.0)
    safe_high = min(10.0, setpoint + 1.0)
    state = state_from_ph(setpoint, phosphate_molar, chemistry)
    expected_base_ml = (
        1000.0
        * volume_l
        * (state_from_ph(safe_high, phosphate_molar, chemistry) - state)
        / base_molar
    )
    expected_acid_ml = (
        1000.0
        * volume_l
        * (state - state_from_ph(safe_low, phosphate_molar, chemistry))
        / acid_molar
    )

    budget = default_dose_budget_ml(
        volume_l,
        phosphate_molar,
        setpoint,
        base_molar,
        acid_molar,
        chemistry,
    )

    assert budget == pytest.approx(expected_base_ml + expected_acid_ml)
    assert default_dose_budget_ml(
        volume_l,
        phosphate_molar,
        setpoint,
        base_molar * 2.0,
        acid_molar,
        chemistry,
    ) == pytest.approx(expected_base_ml / 2.0 + expected_acid_ml)
    assert default_dose_budget_ml(
        volume_l,
        phosphate_molar,
        setpoint,
        base_molar,
        acid_molar * 2.0,
        chemistry,
    ) == pytest.approx(expected_base_ml + expected_acid_ml / 2.0)
