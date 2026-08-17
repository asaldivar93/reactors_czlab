"""Charge-balance (reaction-invariant) pH process model for a phosphate-buffered bioreactor.

The model follows the reaction-invariant formulation of Gustafsson & Waller
(Chem. Eng. Sci. 1983, https://doi.org/10.1016/0009-2509(83)80157-2) and the
CSTR pH dynamics of McAvoy, Hsu & Lowenthal (Ind. Eng. Chem. Process Des. Dev.
1972, https://doi.org/10.1021/i260041a013).

State variables are chosen so that the fast acid-base equilibria leave them
invariant:

    V     - liquid volume                              [L]
    N_Z   = V * Z,  Z = [Na+] - [Cl-]                  [mol]   (strong-ion difference)
    N_P   = V * C_P, C_P = total phosphate             [mol]

All the nonlinearity lives in one algebraic equation - electroneutrality - which
maps (Z, C_P) at a given instant to [H+] and hence pH:

    Z + [H+] - Kw/[H+] - C_P * nbar([H+]) = 0

where nbar is the mean negative charge carried by the phosphate pool.

Titrants: NaOH (base) adds Na+  -> raises Z ;  HCl (acid) adds Cl- -> lowers Z.
A metabolic disturbance r(t) (mol acid produced per litre per second) also lowers Z.

Everything here is deterministic and unit-tested against the analytic phosphate
titration curve. Run as a script to regenerate the model figures.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import brentq

LN10 = np.log(10.0)

# --------------------------------------------------------------------------- #
# Equilibrium constants
# --------------------------------------------------------------------------- #
# Phosphoric acid dissociation, thermodynamic pKa at 25 C, I -> 0
#   H3PO4 <-> H+ + H2PO4-      pKa1 = 2.148
#   H2PO4- <-> H+ + HPO4^2-    pKa2 = 7.198
#   HPO4^2- <-> H+ + PO4^3-    pKa3 = 12.35
# Reference values: CRC Handbook / standard analytical-chemistry tables.
PKA_PHOSPHATE = (2.148, 7.198, 12.35)
PKW = 13.9965  # -log10(Kw) at 25 C  (Kw = 1.0e-14)


def ka_from_pka(pka: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(10.0 ** (-p) for p in pka)


@dataclass
class Chemistry:
    """Acid-base chemistry of the medium (phosphate + water autoionisation)."""

    pka: tuple[float, float, float] = PKA_PHOSPHATE
    pkw: float = PKW

    def __post_init__(self) -> None:
        self.K1, self.K2, self.K3 = ka_from_pka(self.pka)
        self.Kw = 10.0 ** (-self.pkw)

    # ---- phosphate speciation -------------------------------------------- #
    def alphas(self, h):
        """Fractional abundances (alpha0..alpha3) of H3PO4, H2PO4-, HPO4^2-, PO4^3-."""
        h = np.asarray(h, dtype=float)
        K1, K2, K3 = self.K1, self.K2, self.K3
        d = h**3 + K1 * h**2 + K1 * K2 * h + K1 * K2 * K3
        a0 = h**3 / d
        a1 = K1 * h**2 / d
        a2 = K1 * K2 * h / d
        a3 = K1 * K2 * K3 / d
        return np.stack([a0, a1, a2, a3], axis=0)

    def nbar(self, h):
        """Mean negative charge per phosphate (0*a0 + 1*a1 + 2*a2 + 3*a3)."""
        a = self.alphas(h)
        return a[1] + 2 * a[2] + 3 * a[3]

    def dnbar_dh(self, h: float) -> float:
        """d(nbar)/d[H+], analytic derivative used for the buffering intensity."""
        K1, K2, K3 = self.K1, self.K2, self.K3
        d = h**3 + K1 * h**2 + K1 * K2 * h + K1 * K2 * K3
        num = K1 * h**2 + 2 * K1 * K2 * h + 3 * K1 * K2 * K3  # numerator of nbar*d
        dd = 3 * h**2 + 2 * K1 * h + K1 * K2
        dnum = 2 * K1 * h + 2 * K1 * K2
        return (dnum * d - num * dd) / d**2


# --------------------------------------------------------------------------- #
# Electroneutrality solver:  (Z, C_P) -> pH
# --------------------------------------------------------------------------- #
def charge_residual(h: float, Z: float, C_P: float, chem: Chemistry) -> float:
    """Electroneutrality residual F(h) whose root gives [H+]."""
    return Z + h - chem.Kw / h - C_P * chem.nbar(h)


def ph_from_state(Z: float, C_P: float, chem: Chemistry) -> float:
    """Solve electroneutrality for pH given the invariants (Z, C_P).

    F(h) = Z + h - Kw/h - C_P*nbar(h) is strictly decreasing in h, so a bracketed
    Brent solve on h in [1e-14, 1] mol/L is unconditionally convergent.
    """
    f = lambda h: charge_residual(h, Z, C_P, chem)
    hi, lo = 1.0, 1e-14  # F(lo) > 0, F(hi) < 0  (F decreasing in h)
    h = brentq(f, lo, hi, xtol=1e-18, rtol=1e-14, maxiter=200)
    return -np.log10(h)


def state_from_ph(pH: float, C_P: float, chem: Chemistry) -> float:
    """Inverse map pH -> Z (the strong-ion difference needed to sit at this pH).

    Z = Kw/h - h + C_P*nbar(h). Closed form, no iteration.
    """
    h = 10.0 ** (-pH)
    return chem.Kw / h - h + C_P * chem.nbar(h)


# --------------------------------------------------------------------------- #
# Buffering intensity and process static gain
# --------------------------------------------------------------------------- #
def buffering_intensity(pH: float, C_P: float, chem: Chemistry) -> float:
    """beta = dZ/dpH  [mol L^-1 (pH unit)^-1]  (a.k.a. buffer capacity).

    Differentiate Z(pH) = Kw/h - h + C_P*nbar(h) with dh/dpH = -ln(10) h:

        dZ/dpH = ln(10) * ( h + Kw/h + C_P * (-h) * dnbar/dh )

    The three terms are the strong-acid, water and phosphate contributions.
    beta > 0 everywhere; it is maximal near a pKa (curve flattest, gain smallest).
    """
    h = 10.0 ** (-pH)
    water = h + chem.Kw / h
    phosphate = -h * chem.dnbar_dh(h) * C_P
    return LN10 * (water + phosphate)


def static_gain(pH: float, C_P: float, V: float, c_titrant: float, chem: Chemistry) -> float:
    """Process static gain  Kp = dpH / dV_titrant  [pH / L of titrant added].

    Adding dV of titrant at molarity c_titrant changes Z by dZ = c_titrant*dV/V,
    and dpH = dZ/beta, so

        Kp = dpH/dV = c_titrant / (V * beta(pH)).

    Small where beta is large (well buffered) -> the flat middle of the curve.
    Sign is positive for base (raises pH); the acid pump has -Kp.
    """
    beta = buffering_intensity(pH, C_P, chem)
    return c_titrant / (V * beta)


# --------------------------------------------------------------------------- #
# Dynamic plant (batch vessel + titrant additions + metabolic load + dilution)
# --------------------------------------------------------------------------- #
@dataclass
class PlantParams:
    V0: float = 5.0             # initial volume                          [L]
    C_P0: float = 0.014         # total phosphate                          [mol/L] (14 mM)
    Z0: float | None = None     # initial strong-ion diff; if None set for pH0
    pH0: float = 7.0            # initial pH (used to set Z0 when Z0 is None)
    c_base: float = 0.5         # NaOH molarity                            [mol/L]
    c_acid: float = 0.5         # HCl  molarity                            [mol/L]
    chem: Chemistry = field(default_factory=Chemistry)

    def initial_invariants(self) -> tuple[float, float, float]:
        C_P0 = self.C_P0
        Z0 = self.Z0 if self.Z0 is not None else state_from_ph(self.pH0, C_P0, self.chem)
        return self.V0, self.V0 * Z0, self.V0 * C_P0  # V, N_Z, N_P


class PhPlant:
    """Discrete-time simulation of the vessel invariants.

    step(q_base, q_acid, dt, r_metabolic) advances the state by one control
    period. q_base, q_acid are volumetric addition rates [L/s]; r_metabolic is
    the net metabolic acid-production rate [mol L^-1 s^-1] (positive = acidifying).
    """

    def __init__(self, params: PlantParams | None = None) -> None:
        self.p = params or PlantParams()
        self.V, self.N_Z, self.N_P = self.p.initial_invariants()
        self.t = 0.0

    @property
    def Z(self) -> float:
        return self.N_Z / self.V

    @property
    def C_P(self) -> float:
        return self.N_P / self.V

    @property
    def pH(self) -> float:
        return ph_from_state(self.Z, self.C_P, self.p.chem)

    def step(
        self,
        q_base: float = 0.0,
        q_acid: float = 0.0,
        dt: float = 10.0,
        r_metabolic: float = 0.0,
        q_out: float = 0.0,
    ) -> float:
        """Advance one period; returns the new pH.

        Exact mole balances over dt (titrant molarity constant within a step):
            dN_Z = c_base*q_base*dt - c_acid*q_acid*dt - r_metabolic*V*dt
            dN_P = -C_P * q_out * dt          (phosphate only leaves by outflow)
            dV   = (q_base + q_acid - q_out)*dt
        """
        p = self.p
        V = self.V
        C_P = self.C_P
        self.N_Z += (p.c_base * q_base - p.c_acid * q_acid - r_metabolic * V) * dt
        self.N_P += -C_P * q_out * dt
        self.V += (q_base + q_acid - q_out) * dt
        self.t += dt
        return self.pH


# --------------------------------------------------------------------------- #
# Validation + figures
# --------------------------------------------------------------------------- #
def analytic_titration_Vb(pH_grid, C_P, V0, c_base, chem: Chemistry):
    """Volume of strong base (from an all-acid start) to reach each pH.

    Independent cross-check of the electroneutrality solver: for a batch that
    starts strongly acidic, the base required to hit pH is (small-addition,
    dilution neglected) Vb ~ V0*(Z(pH)-Z_start)/c_base.
    """
    Z_start = state_from_ph(2.0, C_P, chem)  # strongly acidic start
    Z = np.array([state_from_ph(p, C_P, chem) for p in pH_grid])
    dZ = Z - Z_start
    Vb_nodil = V0 * dZ / c_base
    return Vb_nodil


def _make_figures(outdir: str = "figures") -> None:
    import os

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(outdir, exist_ok=True)
    chem = Chemistry()
    C_P = 0.014
    V0 = 5.0
    c_base = 0.5

    # ---- Fig 1: titration curve + speciation ----------------------------- #
    pH_grid = np.linspace(2.0, 12.0, 600)
    Vb = analytic_titration_Vb(pH_grid, C_P, V0, c_base, chem)  # L
    # Cross-check: recover pH from the forward solver at a few added-base points
    Z_start = state_from_ph(2.0, C_P, chem)
    check_pts = np.linspace(pH_grid.min(), pH_grid.max(), 25)
    Vb_pts = analytic_titration_Vb(check_pts, C_P, V0, c_base, chem)
    pH_recovered = []
    for vb in Vb_pts:
        Z = Z_start + c_base * vb / V0
        pH_recovered.append(ph_from_state(Z, C_P, chem))
    max_err = float(np.max(np.abs(np.array(pH_recovered) - check_pts)))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    ax1.plot(Vb * 1000.0, pH_grid, color="#1f4e79", lw=2)
    ax1.scatter(Vb_pts * 1000.0, pH_recovered, s=22, color="#c00000", zorder=5,
                label=f"solver recovery (max err {max_err:.1e} pH)")
    for pk in chem.pka:
        ax1.axhline(pk, color="0.7", ls=":", lw=1)
    ax1.set_xlabel("strong base added  [mL of 0.5 M NaOH]")
    ax1.set_ylabel("pH")
    ax1.set_title(f"Titration of {C_P*1e3:.0f} mM phosphate in {V0:.0f} L")
    ax1.legend(loc="lower right", fontsize=8)
    ax1.grid(alpha=0.3)

    a = chem.alphas(10.0 ** (-pH_grid))
    labels = ["H3PO4", "H2PO4-", "HPO4^2-", "PO4^3-"]
    for i in range(4):
        ax2.plot(pH_grid, a[i], lw=1.8, label=labels[i])
    for pk in chem.pka:
        ax2.axvline(pk, color="0.7", ls=":", lw=1)
    ax2.set_xlabel("pH")
    ax2.set_ylabel("fractional abundance alpha")
    ax2.set_title("Phosphate speciation")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(f"{outdir}/fig_titration_speciation.png", dpi=150)
    plt.close(fig)

    # ---- Fig 2: buffering intensity + static gain vs pH ------------------ #
    beta = np.array([buffering_intensity(p, C_P, chem) for p in pH_grid])
    Kp = np.array([static_gain(p, C_P, V0, c_base, chem) for p in pH_grid])
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    ax1.plot(pH_grid, beta * 1000.0, color="#1f4e79", lw=2)
    ax1.axvline(chem.pka[1], color="#c00000", ls="--", lw=1.2,
                label=f"pKa2 = {chem.pka[1]:.2f}")
    ax1.set_xlabel("pH")
    ax1.set_ylabel("buffering intensity beta  [mmol L^-1 pH^-1]")
    ax1.set_title("Buffer capacity (max at pKa2)")
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3)

    ax2.semilogy(pH_grid, np.abs(Kp), color="#1f4e79", lw=2)
    ax2.axvline(chem.pka[1], color="#c00000", ls="--", lw=1.2, label="pKa2")
    ax2.set_xlabel("pH")
    ax2.set_ylabel("|process static gain|  dpH/dV_base  [pH / L]")
    ax2.set_title("Process gain varies ~100x across the curve")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(f"{outdir}/fig_buffer_gain.png", dpi=150)
    plt.close(fig)

    print(f"[ph_process_model] figures written to {outdir}/")
    print(f"[ph_process_model] solver-vs-analytic max pH error = {max_err:.2e}")
    gain_ratio = float(np.max(np.abs(Kp)) / np.min(np.abs(Kp)))
    print(f"[ph_process_model] gain range across pH 2-12 = {gain_ratio:.1f}x")
    print(f"[ph_process_model] beta at pH 7.0 = {buffering_intensity(7.0, C_P, chem)*1e3:.3f} mmol/L/pH")
    print(f"[ph_process_model] Kp at pH 7.0   = {static_gain(7.0, C_P, V0, c_base, chem):.4f} pH/L")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--outdir", default="figures")
    args = ap.parse_args()
    _make_figures(args.outdir)
