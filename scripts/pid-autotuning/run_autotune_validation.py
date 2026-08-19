"""End-to-end in-silico study: relay autotune -> gains -> closed-loop validation.

Pipeline
  1. Run the relay-feedback experiment against the phosphate plant at the
     operating setpoint; extract (Ku, Pu) and map to gains under every rule.
  2. Run closed-loop setpoint-tracking and disturbance-rejection tests with the
     autotuned gains (Tyreus-Luyben default) vs a deliberately detuned baseline.
  3. Emit a figure of the relay experiment with Ku/Pu annotated, a table of
     gains (gains_table.csv), closed-loop trajectory figures, and a metrics
     comparison (validation_metrics.csv).

Rerunnable and deterministic (fixed seeds). Run:  python run_autotune_validation.py
"""
from __future__ import annotations

import argparse
import csv
import os

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reactors_czlab.autotune.model import Chemistry, PhPlant, PlantParams
from reactors_czlab.autotune.relay import (
    RelayTuneConfig as RelayConfig,
)
from reactors_czlab.autotune.relay import (
    scale_gains_to_setpoint,
    simc_pid,
    to_code_gains,
    tuning_rules,
)
from reactors_czlab.autotune.simulation import (
    Pump,
    SplitRangeConfig,
    SplitRangeController,
    metrics,
    run_relay_experiment,
    settling_time,
    simulate,
)

SETPOINT = 7.0
DT = 10.0
V0 = 5.0
C_P = 0.014


def make_plant(pH0=7.0):
    chem = Chemistry()
    return PhPlant(PlantParams(V0=V0, C_P0=C_P, pH0=pH0, c_base=0.5, c_acid=0.5, chem=chem))


# --------------------------------------------------------------------------- #
# Stage 1: relay experiment + gains
# --------------------------------------------------------------------------- #
def stage1_autotune(outdir: str, seed: int = 0):
    plant = make_plant(pH0=SETPOINT)
    cfg = RelayConfig(setpoint=SETPOINT, base_dose_ml=0.30, acid_dose_ml=0.30, hysteresis=0.02,
                      dt=DT, dead_time=10.0, max_cycles=10)
    res = run_relay_experiment(plant, cfg, r_metabolic=2e-7, noise_pH=0.005, seed=seed)

    rules = tuning_rules(res.Ku, res.Pu)
    rules["SIMC"] = simc_pid(res.Ku, res.Pu)

    rows = []
    for name, (Kc, Ti, Td) in rules.items():
        kp, ki, kd = to_code_gains(Kc, Ti, Td)
        rows.append({"rule": name, "Kc": Kc, "Ti_s": Ti, "Td_s": Td,
                     "kp": kp, "ki": ki, "kd": kd})

    with open(f"{outdir}/gains_table.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["rule", "Kc", "Ti_s", "Td_s", "kp", "ki", "kd"])
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items()})

    # Annotated relay figure
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    ax1.plot(res.t / 60.0, res.pH, color="#1f4e79", lw=1.3)
    ax1.axhline(SETPOINT, color="0.5", ls="--", lw=1, label="setpoint")
    ax1.axhline(SETPOINT + cfg.hysteresis, color="0.75", ls=":", lw=0.8)
    ax1.axhline(SETPOINT - cfg.hysteresis, color="0.75", ls=":", lw=0.8)
    # annotate one period
    if len(res.switch_times) >= 4:
        st = np.asarray(res.switch_times)
        t0 = st[len(st)//2]
        ax1.annotate("", xy=((t0 + res.Pu) / 60.0, SETPOINT + res.a_amp),
                     xytext=(t0 / 60.0, SETPOINT + res.a_amp),
                     arrowprops={"arrowstyle": "<->", "color": "#c00000"})
        ax1.text((t0 + res.Pu/2)/60.0, SETPOINT + res.a_amp*1.15,
                 f"Pu = {res.Pu:.0f} s", color="#c00000", ha="center", fontsize=9)
    ax1.set_ylabel("pH")
    ax1.set_title(f"Relay autotune @ pH {SETPOINT}: "
                  f"Ku={res.Ku:.2f} mL/pH, Pu={res.Pu:.0f} s, a={res.a_amp:.3f} pH")
    ax1.legend(fontsize=8, loc="upper right")
    ax1.grid(alpha=0.3)
    ax2.step(res.t / 60.0, res.u, where="post", color="#c00000", lw=1.0)
    ax2.axhline(0, color="0.5", lw=0.8)
    ax2.set_ylabel("relay output\n(signed mL)")
    ax2.set_xlabel("time [min]")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(f"{outdir}/fig_relay_experiment.png", dpi=150)
    plt.close(fig)

    return res, rows


# --------------------------------------------------------------------------- #
# Stage 2: closed-loop validation
# --------------------------------------------------------------------------- #
def _run_disturbance(gains, seed, pH0=7.0, setpoint=SETPOINT, r_load=3e-7, t_on=900.0):
    """Constant setpoint; metabolic acid load steps on at t_on."""
    kp, ki, kd = gains
    plant = make_plant(pH0=pH0)
    cfg = SplitRangeConfig(setpoint=setpoint, kp=kp, ki=ki, kd=kd,
                           dead_band=0.02, dt=DT, base_pump=Pump(), acid_pump=Pump())
    ctrl = SplitRangeController(cfg)

    def r_fn(t):
        return r_load if t > t_on else 0.0
    return simulate(ctrl, plant, t_end=3600.0, r_metabolic_fn=r_fn,
                    noise_pH=0.005, dead_time=10.0, seed=seed)


# ---- Experiment A: robustness — autotuned sits between too-hot and too-cold ---- #
def stage2a_robustness(autotuned_gains, outdir: str, seed: int = 1):
    kp0, ki0, kd0 = autotuned_gains
    controllers = {
        "too hot (8x kp,ki)":  (8.0 * kp0, 8.0 * ki0, kd0),
        "autotuned (TL-PI)":   (kp0, ki0, kd0),
        "too cold (0.15x)":    (0.15 * kp0, 0.15 * ki0, kd0),
    }
    runs, rows = {}, []
    for cname, g in controllers.items():
        res = _run_disturbance(g, seed)
        m = metrics(res, DT)
        m.update(controller=cname, scenario="disturbance@pH7",
                 settling_time_s=settling_time(res, band=0.02))
        rows.append(m)
        runs[cname] = res

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6.2), sharex=True)
    colors = {"too hot (8x kp,ki)": "#c00000", "autotuned (TL-PI)": "#1f4e79",
              "too cold (0.15x)": "#e69138"}
    for cname in controllers:
        res = runs[cname]
        ax1.plot(res.t / 60.0, res.pH, lw=1.3, color=colors[cname], label=cname)
    ax1.axhline(SETPOINT, color="0.4", ls="--", lw=1, label="setpoint")
    ax1.axvline(15.0, color="0.6", ls=":", lw=1)
    ax1.set_ylabel("pH")
    ax1.set_title("Robustness at pH 7: autotuned gains vs mis-tuned guesses\n"
                  "(acid metabolic load steps on at 15 min)")
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3)
    for cname in controllers:
        res = runs[cname]
        ax2.step(res.t / 60.0, res.base_mL, where="post", color=colors[cname], lw=1.0,
                 label=f"{cname} base/period")
    ax2.set_ylabel("base per period [mL]")
    ax2.set_xlabel("time [min]")
    ax2.legend(fontsize=7)
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(f"{outdir}/fig_validation_robustness.png", dpi=150)
    plt.close(fig)
    return rows


# ---- Experiment B: beta-scaling across the titration curve ---- #
def stage2b_scaling(autotuned_gains, res_relay, outdir: str, seed: int = 2):
    """Move the operating setpoint toward the buffer edge (pH 5.8) where the
    process gain is much higher. Compare the tuned gains applied as-is vs
    rescaled by beta(pH_target)/beta(pH_tuned)."""
    chem = Chemistry()
    kp0, ki0, kd0 = autotuned_gains
    pH_tuned, pH_target = SETPOINT, 5.8
    kp_s, ki_s, kd_s = scale_gains_to_setpoint(kp0, ki0, kd0, pH_tuned, pH_target, C_P, chem)
    s = kp_s / kp0

    controllers = {
        "unscaled gains":            (kp0, ki0, kd0),
        "beta-scaled gains":         (kp_s, ki_s, kd_s),
    }
    runs, rows = {}, []
    for cname, g in controllers.items():
        # start at plateau pH 7.0, step setpoint down to 5.8 at t=5 min
        kp, ki, kd = g
        plant = make_plant(pH0=7.0)
        cfg = SplitRangeConfig(setpoint=7.0, kp=kp, ki=ki, kd=kd,
                               dead_band=0.02, dt=DT, base_pump=Pump(), acid_pump=Pump())
        ctrl = SplitRangeController(cfg)

        def sp_fn(t):
            return 7.0 if t < 300 else pH_target
        res = simulate(ctrl, plant, t_end=3600.0, setpoint_fn=sp_fn,
                       noise_pH=0.005, dead_time=10.0, seed=seed)
        m = metrics(res, DT)
        # settling measured only after the setpoint move
        post = res.t >= 300
        e = np.abs(res.pH[post] - res.setpoint[post])
        outside = np.where(e > 0.05)[0]
        m["settling_time_s"] = float((outside[-1] + 1) * DT) if outside.size else 0.0
        m.update(controller=cname, scenario=f"step pH7->{pH_target}")
        rows.append(m)
        runs[cname] = res

    fig, ax = plt.subplots(figsize=(9, 4.6))
    colors = {"unscaled gains": "#c00000", "beta-scaled gains": "#1f4e79"}
    for cname in controllers:
        res = runs[cname]
        ax.plot(res.t / 60.0, res.pH, lw=1.4, color=colors[cname], label=cname)
    ax.plot(runs["beta-scaled gains"].t / 60.0, runs["beta-scaled gains"].setpoint,
            color="0.4", ls="--", lw=1, label="setpoint")
    ax.set_ylabel("pH")
    ax.set_xlabel("time [min]")
    ax.set_title(f"Gain scaling across the titration curve: setpoint moved to the buffer "
                 f"edge (pH {pH_target})\nscale factor s = beta(pH{pH_target})/beta(pH7) = {s:.2f}")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(f"{outdir}/fig_validation_scaling.png", dpi=150)
    plt.close(fig)
    return rows, s


def stage2_validate(autotuned_gains, res_relay, outdir: str, seed: int = 1):
    rows_a = stage2a_robustness(autotuned_gains, outdir, seed=seed)
    rows_b, s = stage2b_scaling(autotuned_gains, res_relay, outdir, seed=seed + 1)
    all_metrics = rows_a + rows_b

    fields = ["controller", "scenario", "IAE", "ISE", "max_abs_error",
              "final_error", "settling_time_s", "titrant_total_mL",
              "titrant_base_mL", "titrant_acid_mL"]
    with open(f"{outdir}/validation_metrics.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for m in all_metrics:
            w.writerow({k: (round(m[k], 4) if isinstance(m.get(k), float) else m.get(k, ""))
                        for k in fields})
    return all_metrics, s


def main(outdir: str = "figures", seed: int = 0):
    os.makedirs(outdir, exist_ok=True)
    res, rows = stage1_autotune(outdir, seed=seed)
    print(f"[autotune] Ku={res.Ku:.3f} mL/pH  Pu={res.Pu:.1f} s  a={res.a_amp:.4f} pH")
    print("[autotune] gains table -> gains_table.csv")
    for r in rows:
        print(f"   {r['rule']:8s} kp={r['kp']:8.3f} ki={r['ki']:8.4f} kd={r['kd']:8.3f}")

    tl_pi = next(r for r in rows if r["rule"] == "TL-PI")
    autotuned = (tl_pi["kp"], tl_pi["ki"], tl_pi["kd"])
    mets, s = stage2_validate(autotuned, res, outdir, seed=seed + 1)
    print(f"\n[validation] beta-scale factor pH7->5.8: s = {s:.3f}")
    print("[validation] metrics -> validation_metrics.csv")
    print(f"{'controller':22s} {'scenario':18s} {'IAE':>9s} {'maxerr':>8s} {'settle_s':>9s}")
    for m in mets:
        print(f"{m['controller']:22s} {m['scenario']:18s} {m['IAE']:9.2f} "
              f"{m['max_abs_error']:8.3f} {m['settling_time_s']:9.0f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--outdir", default="figures")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    main(args.outdir, args.seed)
