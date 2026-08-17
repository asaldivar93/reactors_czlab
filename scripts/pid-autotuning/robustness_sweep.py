"""Robustness study: does relay-autotune-in-situ hold across operating conditions?

For each operating condition the tuner is re-run in place (relay experiment ->
TL-PI gains), then a disturbance-rejection test scores the resulting loop. This
tests the *procedure*, not one fixed gain set: a good autotuner should deliver a
consistent closed-loop performance across conditions because it re-identifies
(Ku, Pu) each time.

Sweeps
  A. 2D grid: buffer concentration C_P (7-28 mM) x setpoint (5.8-8.0)
     -> heatmap of disturbance-rejection IAE (autotuned in situ).
  B. 1D: working volume V (2-10 L).
  C. 1D: titrant molarity (0.1-1.0 M).
  D. 1D: pump flow-calibration error (delivery gain 0.5-1.5) -- gains tuned on
     the *erroneous* pump, so the error is partly absorbed by the relay identify.

Outputs: fig_robustness_grid.png, fig_robustness_1d.png, robustness_metrics.csv.
Deterministic (fixed seeds). Run: python robustness_sweep.py
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from ph_process_model import Chemistry, PhPlant, PlantParams
from relay_autotune import RelayConfig, run_relay_experiment, to_code_gains, tuning_rules
from simulate_ph_loop import Pump, SplitRangeConfig, SplitRangeController, metrics, settling_time, simulate

DT = 10.0
CHEM = Chemistry()


def make_plant(C_P, V, setpoint, c_titrant, pH0=None):
    return PhPlant(PlantParams(V0=V, C_P0=C_P, pH0=(pH0 if pH0 is not None else setpoint),
                               c_base=c_titrant, c_acid=c_titrant, chem=CHEM))


def autotune_in_situ(C_P, V, setpoint, c_titrant, u_amp=0.30, seed=0):
    """Run the relay experiment at this condition; return TL-PI code gains + (Ku,Pu)."""
    plant = make_plant(C_P, V, setpoint, c_titrant, pH0=setpoint)
    cfg = RelayConfig(setpoint=setpoint, u_base=u_amp, u_acid=u_amp, hysteresis=0.02,
                      dt=DT, dead_time=10.0, max_cycles=10)
    res = run_relay_experiment(plant, cfg, r_metabolic=2e-7, noise_pH=0.005, seed=seed)
    Kc, Ti, Td = tuning_rules(res.Ku, res.Pu)["TL-PI"]
    return to_code_gains(Kc, Ti, Td), res.Ku, res.Pu


def score_loop(gains, C_P, V, setpoint, c_titrant, delivery_gain=1.0, seed=1):
    """Disturbance-rejection IAE + max error for the tuned loop at this condition."""
    kp, ki, kd = gains
    plant = make_plant(C_P, V, setpoint, c_titrant, pH0=setpoint)
    cfg = SplitRangeConfig(setpoint=setpoint, kp=kp, ki=ki, kd=kd, dead_band=0.02, dt=DT,
                           base_pump=Pump(), acid_pump=Pump())
    ctrl = SplitRangeController(cfg)
    # acid load scaled to buffer so the pH challenge is comparable across C_P
    r_load = 3e-7 * (C_P / 0.014)

    def r_fn(t):
        return r_load if t > 900 else 0.0
    res = simulate(ctrl, plant, t_end=3600.0, r_metabolic_fn=r_fn, noise_pH=0.005,
                   dead_time=10.0, delivery_gain=delivery_gain, seed=seed)
    m = metrics(res, DT)
    m["settling_time_s"] = settling_time(res, band=0.05)
    return m


# --------------------------------------------------------------------------- #
def sweep_grid(outdir):
    C_P_vals = np.array([0.007, 0.010, 0.014, 0.020, 0.028])
    sp_vals = np.array([5.8, 6.4, 7.0, 7.6, 8.0])
    IAE = np.full((len(C_P_vals), len(sp_vals)), np.nan)
    MAXE = np.full_like(IAE, np.nan)
    rows = []
    for i, C_P in enumerate(C_P_vals):
        for j, sp in enumerate(sp_vals):
            gains, Ku, Pu = autotune_in_situ(C_P, 5.0, sp, 0.5, seed=0)
            m = score_loop(gains, C_P, 5.0, sp, 0.5, seed=1)
            IAE[i, j] = m["IAE"]
            MAXE[i, j] = m["max_abs_error"]
            rows.append({"sweep": "grid", "C_P_mM": C_P * 1e3, "setpoint": sp,
                         "V_L": 5.0, "c_titrant_M": 0.5, "delivery_gain": 1.0,
                         "Ku": Ku, "Pu": Pu, "kp": gains[0], "ki": gains[1],
                         "IAE": m["IAE"], "max_abs_error": m["max_abs_error"],
                         "settling_time_s": m["settling_time_s"]})

    fig, (axa, axb) = plt.subplots(1, 2, figsize=(12, 4.6))
    for ax, Z, title, cmap in [(axa, IAE, "Disturbance-rejection IAE", "viridis"),
                               (axb, MAXE, "Max |pH error|", "magma")]:
        im = ax.imshow(Z, origin="lower", aspect="auto", cmap=cmap,
                       extent=[sp_vals[0], sp_vals[-1], C_P_vals[0]*1e3, C_P_vals[-1]*1e3])
        ax.set_xlabel("setpoint [pH]")
        ax.set_ylabel("buffer conc. [mM phosphate]")
        ax.set_title(title)
        # annotate cells
        for i, C_P in enumerate(C_P_vals):
            for j, sp in enumerate(sp_vals):
                val = Z[i, j]
                ax.text(sp, C_P*1e3, f"{val:.2g}", ha="center", va="center",
                        color="white", fontsize=7)
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.suptitle("Robustness grid: relay-autotune re-run in situ at each condition", y=1.02)
    fig.tight_layout()
    fig.savefig(f"{outdir}/fig_robustness_grid.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return rows


def sweep_1d(outdir):
    rows = []
    # B. volume
    V_vals = np.array([2.0, 3.5, 5.0, 7.0, 10.0])
    iae_V = []
    for V in V_vals:
        gains, Ku, Pu = autotune_in_situ(0.014, V, 7.0, 0.5, seed=0)
        m = score_loop(gains, 0.014, V, 7.0, 0.5, seed=1)
        iae_V.append(m["IAE"])
        rows.append({"sweep": "volume", "C_P_mM": 14.0, "setpoint": 7.0, "V_L": V,
                     "c_titrant_M": 0.5, "delivery_gain": 1.0, "Ku": Ku, "Pu": Pu,
                     "kp": gains[0], "ki": gains[1], "IAE": m["IAE"],
                     "max_abs_error": m["max_abs_error"], "settling_time_s": m["settling_time_s"]})
    # C. titrant molarity
    M_vals = np.array([0.1, 0.25, 0.5, 0.75, 1.0])
    iae_M = []
    for cM in M_vals:
        gains, Ku, Pu = autotune_in_situ(0.014, 5.0, 7.0, cM, seed=0)
        m = score_loop(gains, 0.014, 5.0, 7.0, cM, seed=1)
        iae_M.append(m["IAE"])
        rows.append({"sweep": "titrant_M", "C_P_mM": 14.0, "setpoint": 7.0, "V_L": 5.0,
                     "c_titrant_M": cM, "delivery_gain": 1.0, "Ku": Ku, "Pu": Pu,
                     "kp": gains[0], "ki": gains[1], "IAE": m["IAE"],
                     "max_abs_error": m["max_abs_error"], "settling_time_s": m["settling_time_s"]})
    # D. pump calibration error: tune on the erroneous pump, score on it
    g_vals = np.array([0.5, 0.75, 1.0, 1.25, 1.5])
    iae_g = []
    for dg in g_vals:
        # the relay experiment itself is affected: model by scaling the amplitude
        # actually delivered -> equivalently scale the relay bolus by dg.
        gains, Ku, Pu = autotune_in_situ(0.014, 5.0, 7.0, 0.5, u_amp=0.30 * dg, seed=0)
        m = score_loop(gains, 0.014, 5.0, 7.0, 0.5, delivery_gain=dg, seed=1)
        iae_g.append(m["IAE"])
        rows.append({"sweep": "pump_cal_err", "C_P_mM": 14.0, "setpoint": 7.0, "V_L": 5.0,
                     "c_titrant_M": 0.5, "delivery_gain": dg, "Ku": Ku, "Pu": Pu,
                     "kp": gains[0], "ki": gains[1], "IAE": m["IAE"],
                     "max_abs_error": m["max_abs_error"], "settling_time_s": m["settling_time_s"]})

    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    axes[0].plot(V_vals, iae_V, "o-", color="#1f4e79")
    axes[0].set_xlabel("working volume [L]"); axes[0].set_ylabel("disturbance IAE")
    axes[0].set_title("Volume")
    axes[1].plot(M_vals, iae_M, "o-", color="#1f4e79")
    axes[1].set_xlabel("titrant molarity [M]"); axes[1].set_title("Titrant molarity")
    axes[2].plot(g_vals, iae_g, "o-", color="#1f4e79")
    axes[2].axvline(1.0, color="0.6", ls=":")
    axes[2].set_xlabel("pump delivery gain (1 = exact)"); axes[2].set_title("Pump calibration error")
    for ax in axes:
        ax.grid(alpha=0.3)
    fig.suptitle("Robustness 1-D sweeps: IAE of the in-situ-autotuned loop", y=1.02)
    fig.tight_layout()
    fig.savefig(f"{outdir}/fig_robustness_1d.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return rows


def main(outdir="figures"):
    os.makedirs(outdir, exist_ok=True)
    rows = sweep_grid(outdir) + sweep_1d(outdir)
    fields = ["sweep", "C_P_mM", "setpoint", "V_L", "c_titrant_M", "delivery_gain",
              "Ku", "Pu", "kp", "ki", "IAE", "max_abs_error", "settling_time_s"]
    with open(f"{outdir}/robustness_metrics.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(r[k], 5) if isinstance(r[k], float) else r[k]) for k in fields})
    # concise summary
    grid = [r for r in rows if r["sweep"] == "grid"]
    iae = np.array([r["IAE"] for r in grid])
    maxe = np.array([r["max_abs_error"] for r in grid])
    print(f"[robustness] grid cells = {len(grid)}")
    print(f"[robustness] IAE  min/med/max = {iae.min():.1f} / {np.median(iae):.1f} / {iae.max():.1f}")
    print(f"[robustness] maxE min/med/max = {maxe.min():.3f} / {np.median(maxe):.3f} / {maxe.max():.3f}")
    worst = grid[int(np.argmax(iae))]
    print(f"[robustness] worst grid cell: C_P={worst['C_P_mM']:.0f} mM, sp={worst['setpoint']}, "
          f"IAE={worst['IAE']:.1f}, maxE={worst['max_abs_error']:.3f}")
    print(f"[robustness] metrics -> {outdir}/robustness_metrics.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--outdir", default="figures")
    args = ap.parse_args()
    main(args.outdir)
