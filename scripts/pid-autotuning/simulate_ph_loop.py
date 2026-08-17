"""Closed-loop simulator: phosphate pH plant + split-range PID + dispenser.

Combines
  - the charge-balance plant  (ph_process_model.PhPlant),
  - a faithful replica of reactors_czlab.core.control._PidControl (positional
    parallel PID, derivative-on-measurement, clamped anti-windup band),
  - the split-range acid/base pair (one PID normal, one `backwards`),
  - the reactors_czlab.core.dispenser bolus / per-period quantisation behaviour,
into one rerunnable in-silico test bed. Measurement noise, a control-period
discretisation (Δt = 10 s), an actuation+mixing dead time, and a metabolic
acid/base disturbance are all modelled.

The PID law replicated here is exactly control.py's get_value():
    error   = direction*(setpoint - measurement),  direction = -1 if backwards
    p       = kp*error
    i_sum  <- clamp(i_sum + ki*error*dt, min_i, max_i)
    d       = -direction*kd*(measurement - last_measurement)/dt
    output  = clamp(p + i_sum + d, min_val, max_val)

Run as a script to produce the baseline sanity-check figure.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ph_process_model import Chemistry, PhPlant, PlantParams


# --------------------------------------------------------------------------- #
# Pump model: linear calibration flow = a*duty + b, bolus dispensing
# --------------------------------------------------------------------------- #
@dataclass
class Pump:
    """Peristaltic dosing pump with a linear flow calibration (mL/min)."""

    a: float = 5.0 / 4095.0     # slope [mL/min per count] -> ~5 mL/min at full duty
    b: float = 0.0              # intercept [mL/min]
    min_duty: float = 400.0     # stall floor [counts]
    max_duty: float = 4095.0    # [counts]
    dispense_duty: float = 4095.0  # duty used for volume boluses

    def flow_at(self, duty: float) -> float:
        return self.a * duty + self.b

    def per_period_max(self, control_period: float) -> float:
        """Max volume [mL] one control period can dispense (see Dispenser.demand_limits)."""
        duty = min(self.dispense_duty, self.max_duty)
        return self.flow_at(duty) * control_period / 60.0


# --------------------------------------------------------------------------- #
# Faithful replica of _PidControl (positional parallel PID)
# --------------------------------------------------------------------------- #
@dataclass
class PID:
    setpoint: float
    kp: float
    ki: float
    kd: float
    backwards: bool = False
    min_val: float = 0.0
    max_val: float = 1e9
    min_integral: float | None = None   # auto -> min_val (auto_integral_band=True)
    max_integral: float | None = None   # auto -> max_val
    _isum: float = field(default=0.0, init=False)
    _last_meas: float | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.min_integral is None:
            self.min_integral = self.min_val
        if self.max_integral is None:
            self.max_integral = self.max_val

    def clamp(self, x: float) -> float:
        return max(self.min_val, min(x, self.max_val))

    def reset(self) -> None:
        self._isum = 0.0
        self._last_meas = None

    def get_value(self, meas: float, dt: float) -> float:
        direction = -1.0 if self.backwards else 1.0
        error = direction * (self.setpoint - meas)
        p = self.kp * error
        self._isum = max(self.min_integral,
                         min(self._isum + self.ki * error * dt, self.max_integral))
        if self._last_meas is None:
            d = 0.0
        else:
            d = -direction * self.kd * (meas - self._last_meas) / dt
        self._last_meas = meas
        return self.clamp(p + self._isum + d)


# --------------------------------------------------------------------------- #
# Split-range controller: base (normal) + acid (backwards) on one setpoint
# --------------------------------------------------------------------------- #
@dataclass
class SplitRangeConfig:
    setpoint: float = 7.0
    kp: float = 5.0
    ki: float = 0.01
    kd: float = 0.0
    dead_band: float = 0.0       # +/- pH within which neither pump acts
    dt: float = 10.0
    base_pump: Pump = field(default_factory=Pump)
    acid_pump: Pump = field(default_factory=Pump)
    min_bolus: float = 0.0       # minimum dispensable volume [mL] (0 = continuous)


class SplitRangeController:
    def __init__(self, cfg: SplitRangeConfig) -> None:
        self.cfg = cfg
        vmax_b = cfg.base_pump.per_period_max(cfg.dt)
        vmax_a = cfg.acid_pump.per_period_max(cfg.dt)
        self.base = PID(cfg.setpoint, cfg.kp, cfg.ki, cfg.kd, backwards=False,
                        min_val=0.0, max_val=vmax_b)
        self.acid = PID(cfg.setpoint, cfg.kp, cfg.ki, cfg.kd, backwards=True,
                        min_val=0.0, max_val=vmax_a)

    def set_gains(self, kp: float, ki: float, kd: float) -> None:
        for pid in (self.base, self.acid):
            pid.kp, pid.ki, pid.kd = kp, ki, kd

    def outputs(self, meas: float, dt: float) -> tuple[float, float]:
        """Return (base_volume_mL, acid_volume_mL) demanded this period."""
        vb = self.base.get_value(meas, dt)
        va = self.acid.get_value(meas, dt)
        db = self.cfg.dead_band
        if db > 0:
            if meas < self.cfg.setpoint + db:
                va = 0.0  # not far enough above setpoint to justify acid
            if meas > self.cfg.setpoint - db:
                vb = 0.0  # not far enough below setpoint to justify base
        # Minimum-bolus quantisation guard
        mb = self.cfg.min_bolus
        if mb > 0:
            vb = 0.0 if vb < mb else vb
            va = 0.0 if va < mb else va
        return vb, va


# --------------------------------------------------------------------------- #
# Closed-loop run
# --------------------------------------------------------------------------- #
@dataclass
class SimResult:
    t: np.ndarray
    pH: np.ndarray
    base_mL: np.ndarray
    acid_mL: np.ndarray
    setpoint: np.ndarray
    cum_base: float
    cum_acid: float


def simulate(
    ctrl: SplitRangeController,
    plant: PhPlant,
    t_end: float = 3600.0,
    setpoint_fn=None,
    r_metabolic_fn=None,
    noise_pH: float = 0.005,
    dead_time: float = 10.0,
    delivery_gain: float = 1.0,
    seed: int = 0,
) -> SimResult:
    """Run the split-range loop against the plant.

    setpoint_fn(t) -> pH setpoint; r_metabolic_fn(t) -> metabolic load [mol/L/s].
    The delivered volume per period respects the dispenser's per-period cap: a
    demand above what one period can deliver is delivered at the cap (the next
    decision supersedes the bolus), matching Dispenser._start_bolus behaviour.
    `delivery_gain` scales the volume actually delivered relative to what the
    controller demanded, modelling a pump flow-calibration error (1.0 = exact).
    """
    rng = np.random.default_rng(seed)
    dt = ctrl.cfg.dt
    n = int(round(t_end / dt))
    delay = max(0, round(dead_time / dt))
    meas_buf = [plant.pH] * (delay + 1)

    T, PH, VB, VA, SP = [], [], [], [], []
    cum_b = cum_a = 0.0
    vmax_b = ctrl.cfg.base_pump.per_period_max(dt)
    vmax_a = ctrl.cfg.acid_pump.per_period_max(dt)

    for k in range(n):
        t = k * dt
        if setpoint_fn is not None:
            sp = setpoint_fn(t)
            ctrl.base.setpoint = ctrl.acid.setpoint = sp
            ctrl.cfg.setpoint = sp
        sp = ctrl.cfg.setpoint
        r = r_metabolic_fn(t) if r_metabolic_fn is not None else 0.0

        true_pH = plant.pH
        meas_buf.append(true_pH + rng.normal(0.0, noise_pH))
        measured = meas_buf.pop(0)

        vb, va = ctrl.outputs(measured, dt)
        # dispenser per-period delivery cap
        vb = min(vb, vmax_b)
        va = min(va, vmax_a)

        # pump calibration error: what is delivered differs from what was demanded
        vb_deliv = vb * delivery_gain
        va_deliv = va * delivery_gain
        q_base = (vb_deliv / 1000.0) / dt   # mL -> L over dt s
        q_acid = (va_deliv / 1000.0) / dt
        plant.step(q_base=q_base, q_acid=q_acid, dt=dt, r_metabolic=r)
        cum_b += vb_deliv
        cum_a += va_deliv

        T.append(t); PH.append(true_pH); VB.append(vb); VA.append(va); SP.append(sp)

    return SimResult(np.asarray(T), np.asarray(PH), np.asarray(VB), np.asarray(VA),
                     np.asarray(SP), cum_b, cum_a)


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def metrics(res: SimResult, dt: float) -> dict:
    e = res.pH - res.setpoint
    iae = float(np.sum(np.abs(e)) * dt)
    ise = float(np.sum(e**2) * dt)
    return {
        "IAE": iae,
        "ISE": ise,
        "max_abs_error": float(np.max(np.abs(e))),
        "final_error": float(e[-1]),
        "titrant_base_mL": res.cum_base,
        "titrant_acid_mL": res.cum_acid,
        "titrant_total_mL": res.cum_base + res.cum_acid,
    }


def settling_time(res: SimResult, band: float = 0.05) -> float:
    """Time [s] after the last setpoint change to stay within +/- band pH."""
    e = np.abs(res.pH - res.setpoint)
    dt = res.t[1] - res.t[0] if len(res.t) > 1 else 1.0
    outside = np.where(e > band)[0]
    if outside.size == 0:
        return 0.0
    return float((outside[-1] + 1) * dt)


# --------------------------------------------------------------------------- #
# Baseline demo
# --------------------------------------------------------------------------- #
def _demo(outdir: str = "figures", seed: int = 0) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(outdir, exist_ok=True)
    chem = Chemistry()
    params = PlantParams(V0=5.0, C_P0=0.014, pH0=7.4, c_base=0.5, c_acid=0.5, chem=chem)
    plant = PhPlant(params)

    cfg = SplitRangeConfig(setpoint=7.0, kp=5.83, ki=0.009, kd=0.0, dead_band=0.02, dt=10.0)
    ctrl = SplitRangeController(cfg)

    # setpoint step at t=0 (7.4 -> 7.0), metabolic acid load switching on at 30 min
    def r_fn(t):
        return 2e-7 if t > 1800 else 0.0

    res = simulate(ctrl, plant, t_end=3600.0, r_metabolic_fn=r_fn,
                   noise_pH=0.005, dead_time=10.0, seed=seed)
    m = metrics(res, cfg.dt)
    print("[simulate_ph_loop] baseline TL-PI run:")
    for k, v in m.items():
        print(f"   {k:18s} = {v:.4f}")

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    ax1.plot(res.t / 60.0, res.pH, color="#1f4e79", lw=1.4, label="pH")
    ax1.plot(res.t / 60.0, res.setpoint, color="0.5", ls="--", lw=1, label="setpoint")
    ax1.axvline(30.0, color="#c00000", ls=":", lw=1, label="metabolic load on")
    ax1.set_ylabel("pH")
    ax1.set_title("Baseline closed-loop: setpoint 7.4->7.0 then acid load")
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3)
    ax2.step(res.t / 60.0, res.base_mL, where="post", color="#1f4e79", lw=1.0, label="base (NaOH)")
    ax2.step(res.t / 60.0, -res.acid_mL, where="post", color="#c00000", lw=1.0, label="acid (HCl)")
    ax2.axhline(0, color="0.6", lw=0.8)
    ax2.set_ylabel("titrant per period\n(mL; base +, acid -)")
    ax2.set_xlabel("time [min]")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(f"{outdir}/fig_baseline_loop.png", dpi=150)
    plt.close(fig)
    print(f"[simulate_ph_loop] figure written to {outdir}/fig_baseline_loop.png")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--outdir", default="figures")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    _demo(args.outdir, args.seed)
