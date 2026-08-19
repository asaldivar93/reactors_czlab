"""Analytic tests for relay identification and PID gain calculations."""

import numpy as np
import pytest

from reactors_czlab.autotune.model import (
    Chemistry,
    PhPlant,
    PlantParams,
    buffering_intensity,
)
from reactors_czlab.autotune.relay import (
    RelayTuneConfig,
    from_code_gains,
    identify_ku_pu,
    scale_gains,
    scale_gains_to_setpoint,
    simc_pid,
    to_code_gains,
    tuning_rules,
)
from reactors_czlab.autotune.simulation import run_relay_experiment


def test_identify_ku_pu_for_analytic_triangle_wave() -> None:
    """Relay describing function and switch spacing recover known values."""
    dt = 1.0
    period = 40.0
    amplitude = 0.10
    relay_amplitude = 0.30
    hysteresis = 0.02
    time = np.arange(0.0, 201.0, dt)
    phase = np.mod(time, period) / period
    ph = np.where(
        phase < 0.25,
        4.0 * amplitude * phase,
        np.where(
            phase < 0.75,
            2.0 * amplitude - 4.0 * amplitude * phase,
            -4.0 * amplitude + 4.0 * amplitude * phase,
        ),
    )
    demand = np.where(phase < 0.5, relay_amplitude, -relay_amplitude)
    expected_ku = 4.0 * relay_amplitude / (
        np.pi * np.sqrt(amplitude**2 - hysteresis**2)
    )

    ku, pu = identify_ku_pu(
        ph,
        demand,
        dt,
        relay_amplitude,
        hysteresis,
    )

    assert ku == pytest.approx(expected_ku)
    assert pu == pytest.approx(period)


def test_identify_ku_pu_rejects_amplitude_inside_hysteresis() -> None:
    """A near-flat relay trace must not turn a tiny denominator into Ku."""
    ph = [7.00, 7.01, 7.00, 6.99] * 4
    demand = [0.04, 0.04, -0.04, -0.04] * 4

    with pytest.raises(ValueError, match="clear hysteresis"):
        identify_ku_pu(ph, demand, 2.0, 0.04, 0.02)


def test_tuning_rules_match_published_coefficients() -> None:
    """Every exposed rule maps Ku and Pu with its documented constants."""
    ku = 18.6
    pu = 293.0

    rules = tuning_rules(ku, pu)

    assert rules["ZN-PID"] == pytest.approx(
        (0.6 * ku, 0.5 * pu, 0.125 * pu)
    )
    assert rules["TL-PI"] == pytest.approx((ku / 3.2, 2.2 * pu, 0.0))
    assert rules["TL-PID"] == pytest.approx(
        (ku / 2.2, 2.2 * pu, pu / 6.3)
    )


def test_code_gain_conversion_round_trips() -> None:
    """Continuous and code gain representations preserve all three terms."""
    continuous = (5.8125, 644.6, 46.5)

    code = to_code_gains(*continuous)

    assert code == pytest.approx(
        (continuous[0], continuous[0] / continuous[1], 270.28125)
    )
    assert from_code_gains(*code) == pytest.approx(continuous)


def test_simc_default_has_expected_closed_form() -> None:
    """Default tau_c=theta reduces the inferred SIMC rule analytically."""
    ku = 18.6
    pu = 293.0

    kc, ti, td = simc_pid(ku, pu)

    assert kc == pytest.approx(ku / np.pi)
    assert ti == pytest.approx(2.0 * pu)
    assert td == 0.0


def test_gain_scaling_uses_target_over_tuned_buffering() -> None:
    """Scaling follows beta(target)/beta(tuned), not its inverse."""
    chemistry = Chemistry()
    phosphate_molar = 0.014
    tuned_ph = 7.0
    target_ph = 5.8
    ratio = buffering_intensity(
        target_ph,
        phosphate_molar,
        chemistry,
    ) / buffering_intensity(tuned_ph, phosphate_molar, chemistry)
    gains = (5.8, 0.009, 200.0)

    scaled = scale_gains_to_setpoint(
        *gains,
        tuned_ph,
        target_ph,
        phosphate_molar,
        chemistry,
    )

    assert scaled == pytest.approx(scale_gains(*gains, ratio))
    assert ratio < 1.0


def test_reference_relay_fixture_uses_point_three_ml() -> None:
    """The deterministic phosphate fixture reproduces accepted Ku and Pu."""
    config = RelayTuneConfig(
        setpoint=7.0,
        base_dose_ml=0.30,
        acid_dose_ml=0.30,
        hysteresis=0.02,
        dt=10.0,
        dead_time=10.0,
        max_cycles=10,
    )
    plant = PhPlant(
        PlantParams(
            V0=5.0,
            C_P0=0.014,
            pH0=7.0,
            c_base=0.5,
            c_acid=0.5,
        )
    )

    result = run_relay_experiment(
        plant,
        config,
        r_metabolic=2e-7,
        noise_pH=0.005,
        seed=0,
    )

    assert result.Ku == pytest.approx(18.6, rel=0.02)
    assert result.Pu == pytest.approx(293.0, rel=0.02)
    kc, ti, td = tuning_rules(result.Ku, result.Pu)["TL-PI"]
    assert to_code_gains(kc, ti, td) == pytest.approx(
        (5.83, 0.0090, 0.0),
        rel=0.02,
        abs=1e-12,
    )
