"""In-silico plant and sensor models for response-time and mixing-time studies.

This module is the *behavioural reference* for the two characterization features.
It is deliberately self-contained (numpy + scipy + the repo's charge-balance pH
chemistry) so the estimators in ``response_time.py`` and ``mixing_time.py`` can be
validated against data with a KNOWN ground truth before any production code is
written.

Two models live here:

``SensorModel``
    A first-order-plus-dead-time (FOPDT) probe: ``tau`` (s) time constant, ``dead_time``
    (s) transport delay, followed by a zero-order sample-and-hold at the server's
    sampling period ``dt`` and additive Gaussian read noise. This is the established
    picture of an electrochemical/optical probe (Kok & Zajic 1975; Linek et al. 1987):
    the measured signal is the true process value seen through a first-order filter.

``MixingModel``
    A circulation-loop (tanks-in-series) compartment model of a stirred vessel. The
    liquid is split into ``n_zones`` well-mixed compartments connected head-to-tail by a
    circulation flow ``Q``; a titrant bolus is injected into one zone and a pH probe
    reads another. The state carried per zone is the strong-ion difference ``Z`` (mol/L),
    which is *linear* in added strong titrant, so the tracer is exact; pH is recovered
    per zone from the repo's ``ph_from_state``. A probe away from the injection zone
    therefore sees the stepped, non-exponential approach the literature describes
    (Levenspiel et al. 1970; Grenville & Nienow 2003), not a clean single exponential.

Run ``python response_mixing_model.py`` to emit ``figures/fig_models.png``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# The repo's charge-balance pH chemistry is the single source of truth for the
# pH <-> composition mapping, shared with the existing autotuning feature.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from reactors_czlab.autotune.model import (  # noqa: E402
    Chemistry,
    ph_from_state,
    state_from_ph,
)

SECONDS_PER_MINUTE = 60.0


# --------------------------------------------------------------------------- #
# Sensor model
# --------------------------------------------------------------------------- #
@dataclass
class SensorModel:
    """First-order-plus-dead-time probe with sample-and-hold and read noise.

    Parameters
    ----------
    tau:
        First-order time constant in seconds (the probe's own lag).
    dead_time:
        Pure transport delay in seconds before the probe begins to respond.
    dt:
        Sampling period in seconds. The continuous FOPDT response is integrated
        on a fine internal grid and then reported only on this coarse grid, which
        reproduces the server's sample-and-hold behaviour.
    noise_sigma:
        Standard deviation of additive Gaussian noise on each reported sample,
        in the units of the measured variable (e.g. pH units).
    micro_dt:
        Fine integration step in seconds for the continuous first-order ODE.
    """

    tau: float = 30.0
    dead_time: float = 5.0
    dt: float = 10.0
    noise_sigma: float = 0.005
    micro_dt: float = 0.25

    def sample(
        self,
        t_true: np.ndarray,
        y_true: np.ndarray,
        *,
        seed: int = 0,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(t_samples, y_measured)`` for a true input trajectory.

        The true trajectory ``y_true(t_true)`` is resampled onto a fine grid,
        delayed by ``dead_time``, passed through the first-order lag, then decimated
        to the sampling period and corrupted with read noise.
        """
        rng = np.random.default_rng(seed)
        t0, t1 = float(t_true[0]), float(t_true[-1])
        fine_t = np.arange(t0, t1 + self.micro_dt, self.micro_dt)
        # Delay the input, holding the initial value during the dead time.
        delayed = np.interp(fine_t - self.dead_time, t_true, y_true)
        # Integrate the first-order lag  tau*dy/dt = u - y  (exact per micro-step).
        y = np.empty_like(fine_t)
        y[0] = delayed[0]
        if self.tau <= 0.0:
            y = delayed.copy()
        else:
            alpha = np.exp(-self.micro_dt / self.tau)
            for i in range(1, fine_t.size):
                y[i] = alpha * y[i - 1] + (1.0 - alpha) * delayed[i]
        # Sample-and-hold onto the coarse grid.
        t_samples = np.arange(t0, t1 + self.dt, self.dt)
        y_held = np.interp(t_samples, fine_t, y)
        y_meas = y_held + rng.normal(0.0, self.noise_sigma, size=y_held.shape)
        return t_samples, y_meas

    def step_response(
        self,
        t_end: float,
        *,
        y0: float = 0.0,
        y1: float = 1.0,
        step_time: float = 0.0,
        seed: int = 0,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return a sampled step response from ``y0`` to ``y1`` at ``step_time``.

        This is the trace an operator records for the sensor-response feature:
        a clean step in the true process value, observed through the probe.
        """
        t_true = np.array([0.0, step_time, step_time + 1e-6, t_end])
        y_true = np.array([y0, y0, y1, y1])
        return self.sample(t_true, y_true, seed=seed)


# --------------------------------------------------------------------------- #
# Mixing model
# --------------------------------------------------------------------------- #
@dataclass
class MixingParams:
    """Geometry and operating point of the circulation-loop mixing model."""

    volume_l: float = 5.0
    n_zones: int = 6
    circulation_time_s: float = 20.0
    phosphate_molar: float = 0.014
    ph0: float = 7.0
    inject_zone: int = 0
    probe_zone: int | None = None  # default: farthest zone from injection
    titrant_molar: float = 0.5
    chem: Chemistry = field(default_factory=Chemistry)

    def resolved_probe_zone(self) -> int:
        """Return the probe zone, defaulting to the one opposite the injection."""
        if self.probe_zone is not None:
            return self.probe_zone
        return (self.inject_zone + self.n_zones // 2) % self.n_zones


@dataclass
class MixingResult:
    """True-signal trace from a simulated acid/base pulse."""

    t: np.ndarray
    ph_probe: np.ndarray          # true pH at the probe zone (no sensor lag)
    z_probe: np.ndarray           # true strong-ion difference at the probe zone
    z_zones: np.ndarray           # (n_steps, n_zones) full field, for diagnostics
    z_final: float                # homogeneous equilibrium strong-ion difference
    ph_final: float
    injected_mol: float
    params: MixingParams


class MixingModel:
    """Circulation-loop compartment model of a stirred vessel.

    The vessel is ``n_zones`` equal well-mixed compartments in a ring. A circulation
    flow ``Q = V / circulation_time`` carries liquid head-to-tail, so the mean time for
    a fluid parcel to traverse the loop once is ``circulation_time``. A titrant bolus
    injected into ``inject_zone`` spreads by this circulation, and a probe in another
    zone sees a delayed, stepped approach to the mixed value. Full homogenization takes
    several circulation times, matching the tanks-in-series picture.
    """

    def __init__(self, params: MixingParams | None = None) -> None:
        """Store parameters and initialise every zone at the resting pH."""
        self.p = params or MixingParams()
        self.v_zone = self.p.volume_l / self.p.n_zones
        self.q = self.p.volume_l / self.p.circulation_time_s  # L/s
        z0 = state_from_ph(self.p.ph0, self.p.phosphate_molar, self.p.chem)
        self.n_z = np.full(self.p.n_zones, z0 * self.v_zone)  # moles of Z per zone

    def run(
        self,
        *,
        bolus_ml: float = 0.5,
        base: bool = True,
        t_end: float = 300.0,
        micro_dt: float = 0.1,
    ) -> MixingResult:
        """Inject a bolus at t=0 and integrate the circulation balances.

        Parameters
        ----------
        bolus_ml:
            Volume of titrant injected as a near-instant pulse, in mL.
        base:
            True injects strong base (raises pH); False injects strong acid.
        t_end:
            Simulated duration in seconds.
        micro_dt:
            Integration step in seconds (fine relative to the sampling period).
        """
        p = self.p
        probe = p.resolved_probe_zone()
        sign = 1.0 if base else -1.0
        injected_mol = sign * p.titrant_molar * (bolus_ml / 1000.0)
        # The bolus lands in the injection zone as an instantaneous mole change.
        self.n_z[p.inject_zone] += injected_mol

        n_steps = int(round(t_end / micro_dt)) + 1
        t = np.arange(n_steps) * micro_dt
        z_zones = np.empty((n_steps, p.n_zones))
        # Ring-circulation transport matrix acting on zone concentrations.
        prev = np.arange(p.n_zones) - 1  # upstream neighbour index (wraps)
        for k in range(n_steps):
            conc = self.n_z / self.v_zone
            z_zones[k] = conc
            # dN_i/dt = Q*(conc_{i-1} - conc_i): inflow from upstream, outflow downstream.
            dn = self.q * (conc[prev] - conc) * micro_dt
            self.n_z = self.n_z + dn
        z_final = float(self.n_z.sum() / p.volume_l)
        ph_final = ph_from_state(z_final, p.phosphate_molar, p.chem)
        z_probe = z_zones[:, probe]
        ph_probe = np.array(
            [ph_from_state(z, p.phosphate_molar, p.chem) for z in z_probe]
        )
        return MixingResult(
            t=t,
            ph_probe=ph_probe,
            z_probe=z_probe,
            z_zones=z_zones,
            z_final=z_final,
            ph_final=ph_final,
            injected_mol=injected_mol,
            params=p,
        )


def observe_mixing(
    result: MixingResult,
    sensor: SensorModel,
    *,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(t_samples, ph_measured)`` for a mixing run seen through a probe.

    This is what the operator actually records: the true probe-zone pH passed
    through the sensor's FOPDT lag, sample-and-hold, and read noise.
    """
    return sensor.sample(result.t, result.ph_probe, seed=seed)


# --------------------------------------------------------------------------- #
# Demonstration figure
# --------------------------------------------------------------------------- #
def _demo(outdir: str = "figures") -> None:
    """Render a step response and a pulse-mixing trace to ``fig_models.png``."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    Path(outdir).mkdir(parents=True, exist_ok=True)

    sensor = SensorModel(tau=30.0, dead_time=5.0, dt=10.0, noise_sigma=0.004)
    t_step, y_step = sensor.step_response(t_end=200.0, y0=0.0, y1=1.0, step_time=10.0)
    # A fine "truth" curve for the overlay.
    fine = SensorModel(tau=30.0, dead_time=5.0, dt=0.5, noise_sigma=0.0)
    tf, yf = fine.step_response(t_end=200.0, y0=0.0, y1=1.0, step_time=10.0)

    model = MixingModel(MixingParams(volume_l=5.0, n_zones=6, circulation_time_s=20.0))
    res = model.run(bolus_ml=4.0, base=True, t_end=300.0)
    t_meas, ph_meas = observe_mixing(res, sensor, seed=1)

    figure, (ax_step, ax_mix) = plt.subplots(1, 2, figsize=(12, 4.2))

    ax_step.plot(tf, yf, color="0.6", lw=1.2, label="true (FOPDT, fine)")
    ax_step.plot(t_step, y_step, "o", color="#1f4e79", ms=5, label=f"sampled Δt={sensor.dt:.0f}s")
    ax_step.axhline(0.632, color="#c00000", ls=":", lw=1)
    ax_step.text(2, 0.65, "63.2% (T63)", color="#c00000", fontsize=8)
    ax_step.axhline(0.9, color="#2e7d32", ls=":", lw=1)
    ax_step.text(2, 0.92, "90% (T90)", color="#2e7d32", fontsize=8)
    ax_step.set_xlabel("time [s]")
    ax_step.set_ylabel("normalized sensor response")
    ax_step.set_title("Sensor step response (τ=30 s, L=5 s)")
    ax_step.legend(fontsize=8)
    ax_step.grid(alpha=0.3)

    ax_mix.plot(res.t, res.ph_probe, color="0.6", lw=1.2, label="true probe-zone pH")
    ax_mix.plot(t_meas, ph_meas, "o-", color="#1f4e79", ms=3, lw=0.8,
                label=f"measured Δt={sensor.dt:.0f}s")
    ax_mix.axhline(res.ph_final, color="#c00000", ls="--", lw=1, label="homogeneous pH")
    ax_mix.set_xlabel("time [s]")
    ax_mix.set_ylabel("pH")
    ax_mix.set_title("Acid/base pulse: mixing seen through the probe")
    ax_mix.legend(fontsize=8)
    ax_mix.grid(alpha=0.3)

    figure.tight_layout()
    figure.savefig(f"{outdir}/fig_models.png", dpi=150)
    plt.close(figure)
    print(f"[response_mixing_model] wrote {outdir}/fig_models.png")
    print(f"   sensor T63≈{sensor.dead_time + sensor.tau:.1f}s  "
          f"T90≈{sensor.dead_time + 2.303 * sensor.tau:.1f}s")
    print(f"   mixing: circulation_time={model.p.circulation_time_s:.0f}s  "
          f"ph0={model.p.ph0:.2f} -> ph_final={res.ph_final:.3f}")


if __name__ == "__main__":
    _demo()
