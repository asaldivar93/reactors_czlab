"""Sampling-time (Δt) sensitivity of the autotuning method.

The reactors_czlab PID integrates and differentiates with the *actual elapsed*
time each step (control.py: ``dt = perf_counter() - last_time``; ``i_term =
ki*error*dt``; ``d_term = -kd*Δmeas/dt``). Because the stored gains are
continuous-time quantities (ki = Kc/Ti [s^-1], kd = Kc*Td [s]), a change of Δt
should NOT by itself require re-tuning -- the discrete law tracks the continuous
one for any Δt. What Δt DOES change is (a) the relay experiment's per-period
bolus, and (b) the sample-and-hold phase lag (~Δt/2 of added dead time), which
sets an upper Δt beyond which hot gains lose margin.

This script quantifies both effects so the design criterion "Δt may change" rests
on evidence.

Experiment 1 -- gain dt-invariance: tune once at Δt=10 s, then run the closed
    loop (disturbance rejection) at Δt in {2,5,10,20,40} s WITHOUT re-tuning.
Experiment 2 -- identification vs Δt: re-run the relay autotune at each Δt and
    report Ku, Pu, and the TL-PI gains. The relay bolus is held at a fixed FLOW
    (mL/min) so the physical dosing rate is Δt-independent; the per-period bolus
    is u = flow*Δt/60.

Outputs: fig_sampling_time.png, sampling_time_metrics.csv. Run: python sampling_time_study.py
"""
from __future__ import annotations

import argparse
import csv
import os

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reactors_czlab.core.autotune import (
    Pump,
    SplitRangeConfig,
    SplitRangeController,
    metrics,
    run_relay_experiment,
    settling_time,
    simulate,
    to_code_gains,
    tuning_rules,
)
from reactors_czlab.core.autotune import (
    RelayTuneConfig as RelayConfig,
)
from reactors_czlab.core.ph_model import Chemistry, PhPlant, PlantParams

CHEM = Chemistry()
DT_VALS = [2.0, 5.0, 10.0, 20.0, 40.0]
RELAY_FLOW_ML_MIN = 0.20 * 60.0 / 10.0   # the reference: 0.20 mL per 10 s period -> 1.2 mL/min
DEAD_TIME = 10.0                          # physical actuation+mixing+sensing lag [s], Δt-independent


def make_plant(pH0=7.0):
    return PhPlant(PlantParams(V0=5.0, C_P0=0.014, pH0=pH0, c_base=0.5, c_acid=0.5, chem=CHEM))


def autotune_at_dt(dt, seed=0):
    """Relay autotune at sample period dt; bolus held at a fixed physical flow."""
    u = RELAY_FLOW_ML_MIN * dt / 60.0     # mL per period at this Δt
    plant = make_plant(pH0=7.0)
    cfg = RelayConfig(setpoint=7.0, u_base=u, u_acid=u, hysteresis=0.02,
                      dt=dt, dead_time=DEAD_TIME, max_cycles=10)
    res = run_relay_experiment(plant, cfg, r_metabolic=2e-7, noise_pH=0.005, seed=seed)
    Kc, Ti, Td = tuning_rules(res.Ku, res.Pu)["TL-PI"]
    return to_code_gains(Kc, Ti, Td), res.Ku, res.Pu


def score_at_dt(gains, dt, seed=1):
    """Disturbance-rejection performance of `gains` at sample period dt."""
    kp, ki, kd = gains
    plant = make_plant(pH0=7.0)
    cfg = SplitRangeConfig(setpoint=7.0, kp=kp, ki=ki, kd=kd, dead_band=0.02, dt=dt,
                           base_pump=Pump(), acid_pump=Pump())
    ctrl = SplitRangeController(cfg)

    def r_fn(t):
        return 3e-7 if t > 900 else 0.0
    res = simulate(ctrl, plant, t_end=3600.0, r_metabolic_fn=r_fn, noise_pH=0.005,
                   dead_time=DEAD_TIME, seed=seed)
    m = metrics(res, dt)
    m["settling_time_s"] = settling_time(res, band=0.05)
    return m, res


def main(outdir="figures"):
    os.makedirs(outdir, exist_ok=True)
    rows = []

    # --- Experiment 1: gains tuned once at Δt=10, applied at every Δt ---
    gains10, Ku10, Pu10 = autotune_at_dt(10.0, seed=0)
    fixed = {}
    for dt in DT_VALS:
        m, res = score_at_dt(gains10, dt, seed=1)
        fixed[dt] = (m, res)
        rows.append({"experiment": "fixed_gains_dt10", "dt_s": dt,
                     "Ku": Ku10, "Pu": Pu10, "kp": gains10[0], "ki": gains10[1], "kd": gains10[2],
                     "IAE": m["IAE"], "max_abs_error": m["max_abs_error"],
                     "settling_time_s": m["settling_time_s"]})

    # --- Experiment 2: re-tune at each Δt ---
    retuned = {}
    for dt in DT_VALS:
        g, Ku, Pu = autotune_at_dt(dt, seed=0)
        m, res = score_at_dt(g, dt, seed=1)
        retuned[dt] = (g, Ku, Pu, m)
        rows.append({"experiment": "retuned_per_dt", "dt_s": dt,
                     "Ku": Ku, "Pu": Pu, "kp": g[0], "ki": g[1], "kd": g[2],
                     "IAE": m["IAE"], "max_abs_error": m["max_abs_error"],
                     "settling_time_s": m["settling_time_s"]})

    # --- figure ---
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    dts = np.array(DT_VALS)

    # (a) IAE vs Δt: fixed gains vs re-tuned
    iae_fixed = [fixed[d][0]["IAE"] for d in DT_VALS]
    iae_ret = [retuned[d][3]["IAE"] for d in DT_VALS]
    axes[0, 0].plot(dts, iae_fixed, "o-", color="#c00000", label="gains fixed (tuned @Δt=10 s)")
    axes[0, 0].plot(dts, iae_ret, "s--", color="#1f4e79", label="re-tuned at each Δt")
    axes[0, 0].axvline(10, color="0.6", ls=":", lw=1)
    axes[0, 0].set_xlabel("sample period Δt [s]"); axes[0, 0].set_ylabel("disturbance IAE")
    axes[0, 0].set_title("(a) Performance vs Δt")
    axes[0, 0].legend(fontsize=8); axes[0, 0].grid(alpha=0.3)

    # (b) max error vs Δt
    me_fixed = [fixed[d][0]["max_abs_error"] for d in DT_VALS]
    me_ret = [retuned[d][3]["max_abs_error"] for d in DT_VALS]
    axes[0, 1].plot(dts, me_fixed, "o-", color="#c00000", label="gains fixed")
    axes[0, 1].plot(dts, me_ret, "s--", color="#1f4e79", label="re-tuned")
    axes[0, 1].axvline(10, color="0.6", ls=":", lw=1)
    axes[0, 1].set_xlabel("sample period Δt [s]"); axes[0, 1].set_ylabel("max |pH error|")
    axes[0, 1].set_title("(b) Peak deviation vs Δt")
    axes[0, 1].legend(fontsize=8); axes[0, 1].grid(alpha=0.3)

    # (c) identified Ku, Pu vs Δt (re-tuned). Flag cells where the relay cycle
    # amplitude collapsed toward the hysteresis band (identification unreliable):
    # a small per-period bolus at small Δt gives a ~ h and Ku ~ 4d/(pi*sqrt(a^2-h^2)) blows up.
    Kus = np.array([retuned[d][1] for d in DT_VALS])
    Pus = np.array([retuned[d][2] for d in DT_VALS])
    KU_MAX_PLAUSIBLE = 200.0  # mL/pH; anything above is a failed identification here
    ok = Kus <= KU_MAX_PLAUSIBLE
    ax = axes[1, 0]
    ax.plot(dts[ok], Kus[ok], "o-", color="#1f4e79", label="Ku [mL/pH]")
    ax.set_xlabel("sample period Δt [s]"); ax.set_ylabel("Ku [mL/pH]", color="#1f4e79")
    ax.set_ylim(0, max(60.0, Kus[ok].max() * 1.15))
    for d, ku in zip(dts, Kus):
        if ku > KU_MAX_PLAUSIBLE:
            ax.annotate(f"Δt={d:.0f} s: identification\nunreliable (a≈h), Ku≈{ku:.0f}",
                        xy=(d, ax.get_ylim()[1] * 0.9), fontsize=7, color="#c00000",
                        ha="left", va="top")
            ax.plot([d], [ax.get_ylim()[1] * 0.97], "x", color="#c00000", ms=8)
    ax2 = ax.twinx()
    ax2.plot(dts, Pus, "s--", color="#e69138", label="Pu [s]")
    ax2.set_ylabel("Pu [s]", color="#e69138")
    ax.set_title("(c) Identified Ku, Pu vs Δt")
    ax.grid(alpha=0.3)

    # (d) trajectories at extreme Δt with fixed gains
    ax = axes[1, 1]
    for dt, c in [(2.0, "#1f4e79"), (10.0, "#38761d"), (40.0, "#c00000")]:
        res = fixed[dt][1]
        ax.plot(res.t / 60.0, res.pH, lw=1.1, color=c, label=f"Δt={dt:.0f} s")
    ax.axhline(7.0, color="0.5", ls="--", lw=1)
    ax.axvline(15, color="0.6", ls=":", lw=1)
    ax.set_xlabel("time [min]"); ax.set_ylabel("pH")
    ax.set_title("(d) Disturbance rejection, gains fixed @Δt=10 s")
    ax.set_xlim(12, 30)
    ax.legend(fontsize=8); ax.grid(alpha=0.3)

    fig.suptitle("Sampling-time (Δt) sensitivity of the relay-autotuned pH loop", y=1.01, fontsize=13)
    fig.tight_layout()
    fig.savefig(f"{outdir}/fig_sampling_time.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    # --- CSV ---
    fields = ["experiment", "dt_s", "Ku", "Pu", "kp", "ki", "kd",
              "IAE", "max_abs_error", "settling_time_s"]
    with open(f"{outdir}/sampling_time_metrics.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(r[k], 5) if isinstance(r[k], float) else r[k]) for k in fields})

    # --- console summary ---
    print("[Δt study] Exp 1 -- gains fixed (tuned @Δt=10 s):")
    print(f"  {'Δt[s]':>6} {'IAE':>8} {'maxErr':>8} {'settle[s]':>10}")
    for d in DT_VALS:
        m = fixed[d][0]
        print(f"  {d:6.0f} {m['IAE']:8.2f} {m['max_abs_error']:8.3f} {m['settling_time_s']:10.0f}")
    print("[Δt study] Exp 2 -- re-tuned at each Δt:")
    print(f"  {'Δt[s]':>6} {'Ku':>8} {'Pu':>8} {'kp':>8} {'ki':>8} {'IAE':>8}")
    for d in DT_VALS:
        g, Ku, Pu, m = retuned[d]
        print(f"  {d:6.0f} {Ku:8.2f} {Pu:8.1f} {g[0]:8.3f} {g[1]:8.4f} {m['IAE']:8.2f}")
    print(f"[Δt study] metrics -> {outdir}/sampling_time_metrics.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--outdir", default="figures")
    args = ap.parse_args()
    main(args.outdir)
