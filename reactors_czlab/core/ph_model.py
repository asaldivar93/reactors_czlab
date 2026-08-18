"""Charge-balance pH model for a phosphate-buffered bioreactor."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import brentq

LN10 = np.log(10.0)
PKA_PHOSPHATE = (2.148, 7.198, 12.35)
PKW = 13.9965


def ka_from_pka(
    pka: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Convert acid dissociation constants from pKa to Ka."""
    return tuple(10.0 ** (-value) for value in pka)


@dataclass
class Chemistry:
    """Acid-base chemistry of phosphate and water at 25 degrees Celsius."""

    pka: tuple[float, float, float] = PKA_PHOSPHATE
    pkw: float = PKW
    K1: float = field(init=False)
    K2: float = field(init=False)
    K3: float = field(init=False)
    Kw: float = field(init=False)

    def __post_init__(self) -> None:
        """Derive equilibrium constants from the configured logarithms."""
        self.K1, self.K2, self.K3 = ka_from_pka(self.pka)
        self.Kw = 10.0 ** (-self.pkw)

    def alphas(self, h: float | np.ndarray) -> np.ndarray:
        """Return phosphate fractional abundances for hydrogen concentration."""
        h_array = np.asarray(h, dtype=float)
        denominator = (
            h_array**3
            + self.K1 * h_array**2
            + self.K1 * self.K2 * h_array
            + self.K1 * self.K2 * self.K3
        )
        return np.stack(
            [
                h_array**3 / denominator,
                self.K1 * h_array**2 / denominator,
                self.K1 * self.K2 * h_array / denominator,
                self.K1 * self.K2 * self.K3 / denominator,
            ],
            axis=0,
        )

    def nbar(self, h: float | np.ndarray) -> float | np.ndarray:
        """Return mean negative charge carried by one phosphate molecule."""
        alpha = self.alphas(h)
        return alpha[1] + 2.0 * alpha[2] + 3.0 * alpha[3]

    def dnbar_dh(self, h: float) -> float:
        """Return the analytic derivative of mean charge with respect to H+."""
        denominator = (
            h**3
            + self.K1 * h**2
            + self.K1 * self.K2 * h
            + self.K1 * self.K2 * self.K3
        )
        numerator = (
            self.K1 * h**2
            + 2.0 * self.K1 * self.K2 * h
            + 3.0 * self.K1 * self.K2 * self.K3
        )
        derivative_denominator = (
            3.0 * h**2 + 2.0 * self.K1 * h + self.K1 * self.K2
        )
        derivative_numerator = 2.0 * self.K1 * h + 2.0 * self.K1 * self.K2
        return (
            derivative_numerator * denominator
            - numerator * derivative_denominator
        ) / denominator**2


def charge_residual(
    h: float,
    z: float,
    phosphate_molar: float,
    chemistry: Chemistry,
) -> float:
    """Return the electroneutrality residual at hydrogen concentration ``h``."""
    return (
        z
        + h
        - chemistry.Kw / h
        - phosphate_molar * chemistry.nbar(h)
    )


def ph_from_state(
    z: float,
    phosphate_molar: float,
    chemistry: Chemistry,
) -> float:
    """Solve electroneutrality for pH from the reaction invariants."""

    def residual(h: float) -> float:
        return charge_residual(h, z, phosphate_molar, chemistry)

    h = brentq(residual, 1e-14, 1.0, xtol=1e-18, rtol=1e-14, maxiter=200)
    return float(-np.log10(h))


def state_from_ph(
    ph: float,
    phosphate_molar: float,
    chemistry: Chemistry,
) -> float:
    """Return the strong-ion difference required for a specified pH."""
    h = 10.0 ** (-ph)
    return float(
        chemistry.Kw / h
        - h
        + phosphate_molar * chemistry.nbar(h)
    )


def buffering_intensity(
    ph: float,
    phosphate_molar: float,
    chemistry: Chemistry | None = None,
) -> float:
    """Return buffer capacity ``dZ/dpH`` in mol/L per pH unit."""
    chem = chemistry or Chemistry()
    h = 10.0 ** (-ph)
    water = h + chem.Kw / h
    phosphate = -h * chem.dnbar_dh(h) * phosphate_molar
    return float(LN10 * (water + phosphate))


def static_gain(
    ph: float,
    phosphate_molar: float,
    volume_l: float,
    titrant_molar: float,
    chemistry: Chemistry | None = None,
) -> float:
    """Return the local pH gain per litre of strong titrant."""
    beta = buffering_intensity(ph, phosphate_molar, chemistry)
    return titrant_molar / (volume_l * beta)


def analytic_titration_volume(
    ph: float | np.ndarray,
    phosphate_molar: float,
    volume_l: float,
    base_molar: float,
    chemistry: Chemistry | None = None,
    *,
    initial_ph: float = 2.0,
) -> float | np.ndarray:
    """Return the no-dilution base volume needed to reach each requested pH."""
    chem = chemistry or Chemistry()
    ph_values = np.atleast_1d(np.asarray(ph, dtype=float))
    initial_z = state_from_ph(initial_ph, phosphate_molar, chem)
    z_values = np.array(
        [state_from_ph(value, phosphate_molar, chem) for value in ph_values]
    )
    volumes = volume_l * (z_values - initial_z) / base_molar
    return float(volumes[0]) if np.ndim(ph) == 0 else volumes


@dataclass
class PlantParams:
    """Physical and chemical parameters for :class:`PhPlant`."""

    V0: float = 5.0
    C_P0: float = 0.014
    Z0: float | None = None
    pH0: float = 7.0
    c_base: float = 0.5
    c_acid: float = 0.5
    chem: Chemistry = field(default_factory=Chemistry)

    def initial_invariants(self) -> tuple[float, float, float]:
        """Return initial volume, strong-ion moles, and phosphate moles."""
        z = (
            self.Z0
            if self.Z0 is not None
            else state_from_ph(self.pH0, self.C_P0, self.chem)
        )
        return self.V0, self.V0 * z, self.V0 * self.C_P0


class PhPlant:
    """Discrete batch-vessel model expressed in reaction invariants."""

    def __init__(self, params: PlantParams | None = None) -> None:
        """Initialize the plant at the configured pH and composition."""
        self.p = params or PlantParams()
        self.V, self.N_Z, self.N_P = self.p.initial_invariants()
        self.t = 0.0

    @property
    def Z(self) -> float:
        """Return current strong-ion difference in mol/L."""
        return self.N_Z / self.V

    @property
    def C_P(self) -> float:
        """Return current total phosphate concentration in mol/L."""
        return self.N_P / self.V

    @property
    def pH(self) -> float:
        """Return pH obtained from the current reaction invariants."""
        return ph_from_state(self.Z, self.C_P, self.p.chem)

    def step(
        self,
        q_base: float = 0.0,
        q_acid: float = 0.0,
        dt: float = 10.0,
        r_metabolic: float = 0.0,
        q_out: float = 0.0,
    ) -> float:
        """Advance the mole balances by one time step and return the new pH."""
        volume = self.V
        phosphate_molar = self.C_P
        self.N_Z += (
            self.p.c_base * q_base
            - self.p.c_acid * q_acid
            - r_metabolic * volume
        ) * dt
        self.N_P -= phosphate_molar * q_out * dt
        self.V += (q_base + q_acid - q_out) * dt
        self.t += dt
        return self.pH
