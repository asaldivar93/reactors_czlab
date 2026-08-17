"""Relay-feedback PID autotuner for split-range pH control.

Implements the asymmetric relay-feedback experiment (Åström & Hägglund 1984,
https://doi.org/10.1016/0005-1098(84)90014-1; Shen, Wu & Yu 1996,
https://doi.org/10.1002/aic.690420431) adapted to the reactors_czlab split-range
acid/base architecture, plus the mapping from the identified (Ku, Pu) to the
discrete PID gains (kp, ki, kd) used by reactors_czlab.core.control._PidControl.

The relay element is realised through the *existing* split-range pair: instead of
a proportional controller, the loop switches between commanding the BASE pump (a
fixed volume bolus u+ per period, raising pH) and the ACID pump (a fixed bolus u-,
lowering pH), around the pH setpoint with a hysteresis band to reject probe noise.
The pH therefore executes a bounded limit cycle whose period is the ultimate
period Pu and whose amplitude gives the ultimate gain Ku via the relay describing
function.

Run as a script for a self-contained demonstration against the phosphate plant.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field

import numpy as np

# Make sibling modules importable regardless of how the script is launched
# (PYTHONSAFEPATH / -P suppress the automatic script-dir entry).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ph_process_model import (
    Chemistry,
    PhPlant,
    PlantParams,
    buffering_intensity,
)

# --------------------------------------------------------------------------- #
# Relay controller (split-range, asymmetric, with hysteresis)
# --------------------------------------------------------------------------- #
@dataclass
class RelayConfig:
    setpoint: float = 7.0
    # Relay amplitudes as VOLUME per control period [mL]. Asymmetric by design:
    # a one-sided-dosed, metabolically loaded loop settles into an asymmetric
    # cycle, and letting u_base != u_acid lets the experiment sit centred on the
    # setpoint instead of drifting.
    u_base: float = 0.20     # base (NaOH) bolus while "relay high"  [mL]
    u_acid: float = 0.20     # acid (HCl)  bolus while "relay low"   [mL]
    hysteresis: float = 0.02  # half-width of the switching band       [pH]
    dt: float = 10.0          # control period                         [s]
    dead_time: float = 10.0   # actuation+mixing+sensing lag           [s]
    max_cycles: int = 12      # stop after this many completed cycles
    settle_cycles: int = 2    # discard this many leading cycles (transient)
    max_steps: int = 4000     # hard cap on experiment length


class RelayController:
    """Asymmetric relay with hysteresis, output = signed volume demand [mL].

    Positive demand -> base pump (raise pH); negative -> acid pump (lower pH).
    The relay state persists between calls (hysteresis needs memory).
    """

    def __init__(self, cfg: RelayConfig) -> None:
        self.cfg = cfg
        self._high = True  # start commanding base

    def output(self, pH: float) -> float:
        sp, eps = self.cfg.setpoint, self.cfg.hysteresis
        # Switch only when the measurement crosses the far edge of the band:
        # rejects chatter from noise smaller than eps (Åström-Hägglund 1984).
        if self._high and pH > sp + eps:
            self._high = False
        elif (not self._high) and pH < sp - eps:
            self._high = True
        return self.cfg.u_base if self._high else -self.cfg.u_acid


# --------------------------------------------------------------------------- #
# Run the relay experiment against a plant (with dead-time and noise)
# --------------------------------------------------------------------------- #
@dataclass
class RelayResult:
    t: np.ndarray
    pH: np.ndarray
    u: np.ndarray                 # signed volume demand each step [mL]
    Ku: float
    Pu: float
    a_amp: float                  # zero-to-peak pH amplitude about cycle mean
    cycle_mean_pH: float
    cycles_used: int
    u_base: float
    u_acid: float
    switch_times: list = field(default_factory=list)


def run_relay_experiment(
    plant: PhPlant,
    cfg: RelayConfig,
    r_metabolic: float = 0.0,
    noise_pH: float = 0.005,
    seed: int = 0,
) -> RelayResult:
    """Drive `plant` with the relay until a steady limit cycle is established.

    A pure dead time is modelled by delaying the pH the controller sees by
    round(dead_time/dt) periods. Measurement noise is white Gaussian on the
    reported pH. Volume boluses are applied as an average flow over the period
    (q = u_mL/1000 / dt in L/s) so the invariant balance in PhPlant stays exact.
    """
    rng = np.random.default_rng(seed)
    ctrl = RelayController(cfg)
    delay = max(0, round(cfg.dead_time / cfg.dt))

    T, PH, U = [], [], []
    meas_buffer = [plant.pH] * (delay + 1)
    switch_times = []
    prev_high = ctrl._high

    for k in range(cfg.max_steps):
        true_pH = plant.pH
        meas_buffer.append(true_pH + rng.normal(0.0, noise_pH))
        measured = meas_buffer.pop(0)  # delayed measurement

        u = ctrl.output(measured)
        if ctrl._high != prev_high:
            switch_times.append(k * cfg.dt)
            prev_high = ctrl._high

        # Apply as base/acid flow over the period.
        q_base = q_acid = 0.0
        if u >= 0:
            q_base = (u / 1000.0) / cfg.dt         # mL -> L, over dt seconds
        else:
            q_acid = (-u / 1000.0) / cfg.dt
        plant.step(q_base=q_base, q_acid=q_acid, dt=cfg.dt, r_metabolic=r_metabolic)

        T.append(k * cfg.dt)
        PH.append(true_pH)
        U.append(u)

        # Stop once enough switching cycles (one cycle = two switches) are seen
        if len(switch_times) >= 2 * (cfg.max_cycles + cfg.settle_cycles):
            break

    T, PH, U = np.asarray(T), np.asarray(PH), np.asarray(U)
    Ku, Pu, a_amp, mean_pH, ncyc = _identify(T, PH, switch_times, cfg)
    return RelayResult(
        t=T, pH=PH, u=U, Ku=Ku, Pu=Pu, a_amp=a_amp,
        cycle_mean_pH=mean_pH, cycles_used=ncyc,
        u_base=cfg.u_base, u_acid=cfg.u_acid, switch_times=switch_times,
    )


def _identify(t, pH, switch_times, cfg: RelayConfig):
    """Extract Ku, Pu, amplitude from the steady portion of the limit cycle.

    - Pu: mean period between every-other switch (a full up+down cycle), taken
      over the settled cycles only.
    - a_amp: zero-to-peak pH amplitude about the cycle mean, from the settled
      window (robust to the asymmetric mean via peak/trough averaging).
    - Ku: asymmetric-relay describing function with hysteresis correction
        Ku = 2 (u_base + u_acid) / ( pi * sqrt(a_amp^2 - eps^2) )
      which reduces to the classical 4d/(pi*a) for a symmetric relay.
    """
    if len(switch_times) < 2 * (cfg.settle_cycles + 1):
        return float("nan"), float("nan"), float("nan"), float("nan"), 0

    # Full-cycle boundaries = every second switch; drop the leading transient.
    cyc_bounds = np.asarray(switch_times[:: 2])
    start_t = cyc_bounds[cfg.settle_cycles] if len(cyc_bounds) > cfg.settle_cycles else cyc_bounds[0]
    periods = np.diff(cyc_bounds)
    periods = periods[cfg.settle_cycles:] if len(periods) > cfg.settle_cycles else periods
    Pu = float(np.mean(periods)) if len(periods) else float("nan")

    mask = t >= start_t
    seg = pH[mask]
    if seg.size < 4:
        return float("nan"), Pu, float("nan"), float("nan"), len(periods)
    peak = np.mean(_local_extrema(seg, kind="max"))
    trough = np.mean(_local_extrema(seg, kind="min"))
    mean_pH = 0.5 * (peak + trough)
    a_amp = 0.5 * (peak - trough)

    eps = cfg.hysteresis
    corr = np.sqrt(max(a_amp**2 - eps**2, 1e-12))
    Ku = 2.0 * (cfg.u_base + cfg.u_acid) / (np.pi * corr)
    return float(Ku), Pu, float(a_amp), float(mean_pH), len(periods)


def _local_extrema(x, kind="max"):
    """Return interior local maxima or minima; fall back to global if none."""
    if kind == "max":
        idx = np.where((x[1:-1] > x[:-2]) & (x[1:-1] >= x[2:]))[0] + 1
        return x[idx] if idx.size else np.array([x.max()])
    idx = np.where((x[1:-1] < x[:-2]) & (x[1:-1] <= x[2:]))[0] + 1
    return x[idx] if idx.size else np.array([x.min()])


# --------------------------------------------------------------------------- #
# Tuning rules: (Ku, Pu) -> continuous (Kc, Ti, Td) -> code gains (kp, ki, kd)
# --------------------------------------------------------------------------- #
def tuning_rules(Ku: float, Pu: float) -> dict[str, tuple[float, float, float]]:
    """Continuous parallel-PID settings (Kc, Ti, Td) for several rule sets.

    Ti, Td in seconds (Pu is in seconds). Td = 0 marks a PI controller.
      - ZN (Ziegler & Nichols 1942, https://doi.org/10.1115/1.4019269)
      - TL-PI / TL-PID (Tyreus & Luyben 1992, https://doi.org/10.1021/ie00011a029)
    """
    return {
        "ZN-PID": (0.6 * Ku, 0.5 * Pu, 0.125 * Pu),
        "TL-PI": (Ku / 3.2, 2.2 * Pu, 0.0),
        "TL-PID": (Ku / 2.2, 2.2 * Pu, Pu / 6.3),
    }


def simc_pid(Ku: float, Pu: float, tau_c: float | None = None) -> tuple[float, float, float]:
    """SIMC settings for an integrating+dead-time model inferred from the relay.

    Skogestad 2003 (https://doi.org/10.1016/S0959-1524(02)00062-8). For an
    integrating process g(s)=k' e^{-θs}/s, SIMC gives Kc=1/(k'(τc+θ)),
    Ti=4(τc+θ), Td=0. We infer θ ≈ Pu/4 (the relay dead-time surrogate: at the
    ultimate frequency the process phase lag is π, of which the integrator
    contributes π/2, leaving π/2 = ωu·θ, i.e. θ = Pu/4) and the integrating
    slope k' from the ultimate point: |g(jωu)| = 1/Ku = k'/ωu -> k' = ωu/Ku.
    Default τc = θ (a moderate, robust choice).
    """
    wu = 2.0 * np.pi / Pu
    theta = Pu / 4.0
    kprime = wu / Ku
    if tau_c is None:
        tau_c = theta
    Kc = 1.0 / (kprime * (tau_c + theta))
    Ti = 4.0 * (tau_c + theta)
    return (Kc, Ti, 0.0)


def to_code_gains(Kc: float, Ti: float, Td: float) -> tuple[float, float, float]:
    """Map continuous parallel PID -> reactors_czlab _PidControl (kp, ki, kd).

    The code's step is  output = kp*e + Σ(ki*e*dt) + kd*(-Δmeas/dt), i.e. a
    positional parallel PID with
        kp = Kc,   ki = Kc/Ti,   kd = Kc*Td.
    Units: e is in pH, output in mL, so kp is mL/pH, ki mL/(pH·s), kd mL·s/pH.
    A PI rule (Ti finite, Td=0) yields kd = 0. Guard Ti>0.
    """
    kp = Kc
    ki = Kc / Ti if Ti > 0 else 0.0
    kd = Kc * Td
    return (kp, ki, kd)


# --------------------------------------------------------------------------- #
# Model-based gain scaling across the titration curve
# --------------------------------------------------------------------------- #
def scale_gains_to_setpoint(
    kp: float, ki: float, kd: float,
    pH_tuned: float, pH_target: float,
    C_P: float, chem: Chemistry,
) -> tuple[float, float, float]:
    """Rescale gains tuned at pH_tuned so the loop gain holds at pH_target.

    The loop gain is Kc·Kp with Kp ∝ 1/β(pH) (see process_model.md eq. 8). To
    keep Kc·Kp invariant when the operating point moves, scale every gain by
        s = Kp(pH_tuned)/Kp(pH_target) = β(pH_target)/β(pH_tuned).
    Because the whole controller output scales with Kc, all three gains scale by s.
    """
    beta_t = buffering_intensity(pH_tuned, C_P, chem)
    beta_x = buffering_intensity(pH_target, C_P, chem)
    s = beta_x / beta_t
    return (kp * s, ki * s, kd * s)


# --------------------------------------------------------------------------- #
# Static-gain check from cycle asymmetry (bonus identification)
# --------------------------------------------------------------------------- #
def static_gain_from_asymmetry(res: RelayResult, cfg: RelayConfig) -> float:
    """Estimate steady process gain from the relay-cycle asymmetry.

    A biased relay makes the on-times unequal; the ratio of base-on to acid-on
    time reflects the net load the loop works against (Shen, Wu & Yu 1996). Here
    we return the net titrant demand per unit time at the limit cycle, a proxy
    for the metabolic bias the tuned loop must reject. Positive -> net base.
    """
    u = res.u
    if u.size == 0:
        return float("nan")
    return float(np.mean(u)) / cfg.dt  # mean signed mL per second


# --------------------------------------------------------------------------- #
# Demo / self-test
# --------------------------------------------------------------------------- #
def _demo(outdir: str = "figures", seed: int = 0) -> None:
    import os

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(outdir, exist_ok=True)
    chem = Chemistry()
    params = PlantParams(V0=5.0, C_P0=0.014, pH0=7.0, c_base=0.5, c_acid=0.5, chem=chem)
    plant = PhPlant(params)
    cfg = RelayConfig(setpoint=7.0, u_base=0.30, u_acid=0.30, hysteresis=0.02,
                      dt=10.0, dead_time=10.0, max_cycles=10)
    res = run_relay_experiment(plant, cfg, r_metabolic=2e-7, noise_pH=0.005, seed=seed)

    rules = tuning_rules(res.Ku, res.Pu)
    rules["SIMC"] = simc_pid(res.Ku, res.Pu)

    print(f"[relay_autotune] Ku = {res.Ku:.3f} mL/pH, Pu = {res.Pu:.1f} s, "
          f"a = {res.a_amp:.4f} pH, cycle mean pH = {res.cycle_mean_pH:.3f}, "
          f"cycles = {res.cycles_used}")
    print("[relay_autotune] gains (kp, ki, kd) in code units:")
    for name, (Kc, Ti, Td) in rules.items():
        kp, ki, kd = to_code_gains(Kc, Ti, Td)
        print(f"   {name:8s}: Kc={Kc:8.3f} Ti={Ti:8.1f} Td={Td:7.2f}  ->  "
              f"kp={kp:8.3f} ki={ki:8.4f} kd={kd:8.3f}")

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    ax1.plot(res.t / 60.0, res.pH, color="#1f4e79", lw=1.3)
    ax1.axhline(cfg.setpoint, color="0.5", ls="--", lw=1, label="setpoint")
    ax1.axhline(cfg.setpoint + cfg.hysteresis, color="0.75", ls=":", lw=0.8)
    ax1.axhline(cfg.setpoint - cfg.hysteresis, color="0.75", ls=":", lw=0.8)
    ax1.set_ylabel("pH")
    ax1.set_title(f"Relay-feedback experiment: Ku={res.Ku:.2f} mL/pH, Pu={res.Pu:.0f} s")
    ax1.legend(fontsize=8, loc="upper right")
    ax1.grid(alpha=0.3)
    ax2.step(res.t / 60.0, res.u, where="post", color="#c00000", lw=1.0)
    ax2.axhline(0, color="0.5", lw=0.8)
    ax2.set_ylabel("relay output\n(signed volume, mL)")
    ax2.set_xlabel("time [min]")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(f"{outdir}/fig_relay_demo.png", dpi=150)
    plt.close(fig)
    print(f"[relay_autotune] figure written to {outdir}/fig_relay_demo.png")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--outdir", default="figures")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    _demo(args.outdir, args.seed)
