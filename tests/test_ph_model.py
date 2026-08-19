"""Analytic tests for the packaged phosphate pH process model."""

import numpy as np
import pytest

from reactors_czlab.autotune.model import (
    Chemistry,
    PhPlant,
    PlantParams,
    buffering_intensity,
    ph_from_state,
    state_from_ph,
    static_gain,
)


def test_phosphate_fractions_sum_to_one() -> None:
    """Phosphate species partition the conserved phosphate pool."""
    chemistry = Chemistry()
    hydrogen = 10.0 ** (-np.linspace(2.0, 12.0, 101))

    fractions = chemistry.alphas(hydrogen)

    np.testing.assert_allclose(fractions.sum(axis=0), 1.0, atol=1e-14)
    assert np.all(fractions >= 0.0)


@pytest.mark.parametrize("ph", [2.5, 5.8, 7.0, 8.0, 11.5])
def test_state_and_ph_maps_are_inverses(ph: float) -> None:
    """The analytic state map inverts the electroneutrality root solve."""
    chemistry = Chemistry()
    phosphate_molar = 0.014

    state = state_from_ph(ph, phosphate_molar, chemistry)

    assert ph_from_state(state, phosphate_molar, chemistry) == pytest.approx(
        ph,
        abs=1e-8,
    )


def test_buffering_intensity_matches_state_derivative() -> None:
    """The closed-form buffer capacity agrees with a central difference."""
    chemistry = Chemistry()
    phosphate_molar = 0.014
    ph = 7.0
    step = 1e-5
    numerical = (
        state_from_ph(ph + step, phosphate_molar, chemistry)
        - state_from_ph(ph - step, phosphate_molar, chemistry)
    ) / (2.0 * step)

    beta = buffering_intensity(ph, phosphate_molar, chemistry)

    assert beta == pytest.approx(numerical, rel=1e-8)
    assert beta > buffering_intensity(5.8, phosphate_molar, chemistry)


def test_static_gain_follows_buffer_capacity_definition() -> None:
    """Local titrant gain is molarity divided by vessel buffer inventory."""
    chemistry = Chemistry()
    beta = buffering_intensity(7.0, 0.014, chemistry)

    gain = static_gain(7.0, 0.014, 5.0, 0.5, chemistry)

    assert gain == pytest.approx(0.5 / (5.0 * beta))


def test_plant_step_preserves_phosphate_moles_without_outflow() -> None:
    """Titrant additions dilute phosphate but do not create or destroy it."""
    plant = PhPlant(PlantParams(pH0=7.0))
    initial_phosphate_moles = plant.N_P

    ph = plant.step(q_base=2e-5, dt=10.0)

    assert plant.N_P == initial_phosphate_moles
    assert plant.V == pytest.approx(5.0002)
    assert ph > 7.0
