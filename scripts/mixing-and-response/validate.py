"""Validation and robustness study for the response-time and mixing-time estimators.

Runs the estimators against the in-silico models where the ground truth is known,
sweeps the operating conditions that matter for a coarsely sampled reactor, and emits
the evidence base the implementation spec's acceptance criteria are keyed to:

  figures/fig_sensor_validation.png   recovered tau/L vs sampling period + a fit overlay
  figures/fig_mixing_validation.png   raw vs deconvolved t95 vs true, + the lag-bias curve
  figures/fig_robustness.png          error heatmaps over (dt, noise) and (dt, tau/t_mix)
  response_metrics.csv                per-run sensor recovery numbers
  mixing_metrics.csv                  per-run mixing recovery numbers
  robustness_metrics.csv              the full sweep grid

Run ``python validate.py``. Deterministic (fixed seeds) so the numbers are the ones the
spec's acceptance tests quote.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from estimators import estimate_mixing_time, estimate_response_time  # noqa: E402
from response_mixing_model import (  # noqa: E402
    MixingModel,
    MixingParams,
    SensorModel,
    observe_mixing,
)

FIG_DIR = Path("figures")
OUT_DIR = Path(".")


def _true_t95(res, band: float = 0.05) -> float:
    """Ground-truth homogenization time from the noise-free, lag-free tracer."""
    z = res.z_probe
    z0, zf = z[0], res.z_final
    norm = (z - z0) / (zf - z0)
    within = np.abs(norm - 1.0) <= band
    last_out = 0
    for i in range(within.size):
        if not within[i]:
            last_out = i
    return float(res.t[last_out + 1]) if last_out < res.t.size - 1 else float("nan")


# --------------------------------------------------------------------------- #
def sensor_validation():
    """Recover a known FOPDT probe across sampling periods; return rows + arrays."""
    true_tau, true_L = 30.0, 5.0
    periods = [2, 5, 10, 15, 20, 30]
    rows = []
    for dt in periods:
        s = SensorModel(tau=true_tau, dead_time=true_L, dt=dt, noise_sigma=0.004)
        t, y = s.step_response(t_end=250.0, y0=0.0, y1=1.0, step_time=10.0, seed=3)
        est = estimate_response_time(t, y, step_time=10.0)
        rows.append({
            "dt_s": dt, "tau_true": true_tau, "tau_hat": round(est.tau, 2),
            "L_true": true_L, "L_hat": round(est.dead_time, 2),
            "T63": round(est.t63, 1), "T90": round(est.t90, 1),
            "rmse_norm": round(est.rmse_norm, 4), "n_rise": est.n_points_rise,
            "tau_err_pct": round(100 * (est.tau - true_tau) / true_tau, 1),
        })
    return rows, true_tau, true_L


def mixing_validation():
    """Recover a well-resolved mixing time with vs without deconvolution."""
    p = MixingParams(volume_l=5.0, n_zones=8, circulation_time_s=45.0, ph0=6.5)
    res = MixingModel(p).run(bolus_ml=4.0, base=True, t_end=600.0)
    gt = _true_t95(res)
    periods = [5, 10, 15, 20, 30]
    rows = []
    for dt in periods:
        win = max(5, int(round(40 / dt)) | 1)
        s = SensorModel(tau=30.0, dead_time=5.0, dt=dt, noise_sigma=0.001)
        tm, phm = observe_mixing(res, s, seed=11)
        er = estimate_mixing_time(tm, phm, p.phosphate_molar, pulse_time=0.0,
                                  tau_probe=0.0, smooth_window=win)
        ed = estimate_mixing_time(tm, phm, p.phosphate_molar, pulse_time=0.0,
                                  tau_probe=30.0, smooth_window=win)
        rows.append({
            "dt_s": dt, "t95_true": round(gt, 1),
            "t95_raw": round(er.t95, 1), "t95_deconv": round(ed.t95, 1),
            "bias_raw_s": round(er.t95 - gt, 1), "err_deconv_s": round(ed.t95 - gt, 1),
        })
    return rows, res, gt, p


def _median_abs_error(res, p, gt, *, dt, noise, tau, n_seeds=12):
    """Return (median |t95 error|, median SNR) over ``n_seeds`` noise realizations.

    Each cell of the robustness grid is evaluated over several independent read-noise
    realizations and reduced by the median, so a single unlucky trace cannot move the
    envelope boundary. NaN estimates (band never satisfied) are counted as failures
    and mapped to a large error so they dominate the median honestly.
    """
    win = max(5, int(round(40 / dt)) | 1)
    errs, snrs = [], []
    for seed in range(100, 100 + n_seeds):
        s = SensorModel(tau=tau, dead_time=5.0, dt=dt, noise_sigma=noise)
        tm, phm = observe_mixing(res, s, seed=seed)
        ed = estimate_mixing_time(tm, phm, p.phosphate_molar, pulse_time=0.0,
                                  tau_probe=tau, smooth_window=win)
        errs.append(abs(ed.t95 - gt) if np.isfinite(ed.t95) else 999.0)
        if ed.noise_sigma_z > 0:
            snrs.append(abs(0.05 * ed.final_offset) / ed.noise_sigma_z)
    return float(np.median(errs)), (float(np.median(snrs)) if snrs else float("inf"))


def robustness_sweep(p, res, gt):
    """Sweep (dt, noise) and (dt, tau/t_mix); median |error| over seeds per cell."""
    periods = [5, 10, 15, 20, 30]
    noises = [0.0005, 0.001, 0.002, 0.004]
    tau_over_tmix = [0.1, 0.25, 0.5, 1.0, 2.0]
    rows = []
    err_noise = np.full((len(noises), len(periods)), np.nan)
    for i, ns in enumerate(noises):
        for j, dt in enumerate(periods):
            med_err, med_snr = _median_abs_error(res, p, gt, dt=dt, noise=ns, tau=30.0)
            err_noise[i, j] = med_err
            rows.append({"sweep": "noise", "dt_s": dt, "noise_pH": ns,
                         "med_abs_err_s": round(med_err, 1),
                         "snr": round(med_snr, 2) if np.isfinite(med_snr) else "inf"})
    err_tau = np.full((len(tau_over_tmix), len(periods)), np.nan)
    for i, ratio in enumerate(tau_over_tmix):
        tau = ratio * gt
        for j, dt in enumerate(periods):
            med_err, _ = _median_abs_error(res, p, gt, dt=dt, noise=0.001, tau=tau)
            err_tau[i, j] = med_err
            rows.append({"sweep": "tau_ratio", "dt_s": dt,
                         "tau_over_tmix": ratio, "tau_s": round(tau, 1),
                         "med_abs_err_s": round(med_err, 1), "snr": ""})
    return rows, (noises, periods, err_noise), (tau_over_tmix, periods, err_tau)


# --------------------------------------------------------------------------- #
def _write_csv(path: Path, rows: list[dict]):
    if not rows:
        return
    keys = list({k for r in rows for k in r})
    order = ["sweep", "dt_s", "noise_pH", "tau_over_tmix", "tau_s",
             "tau_true", "tau_hat", "tau_err_pct", "L_true", "L_hat",
             "T63", "T90", "rmse_norm", "n_rise",
             "t95_true", "t95_raw", "t95_deconv", "bias_raw_s",
             "err_deconv_s", "med_abs_err_s", "err_s", "snr"]
    fieldnames = [k for k in order if k in keys] + [k for k in keys if k not in order]
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIG_DIR.mkdir(parents=True, exist_ok=True)

    sensor_rows, true_tau, true_L = sensor_validation()
    mixing_rows, res, gt, p = mixing_validation()
    robust_rows, noise_grid, tau_grid = robustness_sweep(p, res, gt)

    _write_csv(OUT_DIR / "response_metrics.csv", sensor_rows)
    _write_csv(OUT_DIR / "mixing_metrics.csv", mixing_rows)
    _write_csv(OUT_DIR / "robustness_metrics.csv", robust_rows)

    # ---- Sensor validation figure ----
    fig, (a0, a1) = plt.subplots(1, 2, figsize=(12, 4.4))
    dts = [r["dt_s"] for r in sensor_rows]
    a0.plot(dts, [r["tau_hat"] for r in sensor_rows], "o-", color="#1f4e79", label="τ̂")
    a0.axhline(true_tau, color="#1f4e79", ls=":", label="τ true")
    a0.plot(dts, [r["L_hat"] for r in sensor_rows], "s-", color="#c00000", label="L̂")
    a0.axhline(true_L, color="#c00000", ls=":", label="L true")
    a0.set_xlabel("sampling period Δt [s]")
    a0.set_ylabel("estimate [s]")
    a0.set_title("Sensor parameter recovery vs Δt")
    a0.legend(fontsize=8)
    a0.grid(alpha=0.3)
    s = SensorModel(tau=true_tau, dead_time=true_L, dt=10.0, noise_sigma=0.004)
    t, y = s.step_response(t_end=200.0, y0=0.0, y1=1.0, step_time=10.0, seed=3)
    est = estimate_response_time(t, y, step_time=10.0)
    tt = np.linspace(0, 200, 500)
    fit = np.where(tt - 10 - est.dead_time > 0,
                   est.gain * (1 - np.exp(-(tt - 10 - est.dead_time) / est.tau)), 0)
    a1.plot(t, y, "o", color="#1f4e79", ms=4, label="samples (Δt=10s)")
    a1.plot(tt, fit, color="#c00000", lw=1.3,
            label=f"FOPDT fit τ={est.tau:.1f}, L={est.dead_time:.1f}")
    a1.set_xlabel("time [s]")
    a1.set_ylabel("normalized response")
    a1.set_title("FOPDT fit overlay (Δt=10 s)")
    a1.legend(fontsize=8)
    a1.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig_sensor_validation.png", dpi=150)
    plt.close(fig)

    # ---- Mixing validation figure ----
    fig, (a0, a1) = plt.subplots(1, 2, figsize=(12, 4.4))
    dts = [r["dt_s"] for r in mixing_rows]
    a0.plot(dts, [r["t95_raw"] for r in mixing_rows], "s-", color="#999999",
            label="t95 raw (probe lag)")
    a0.plot(dts, [r["t95_deconv"] for r in mixing_rows], "o-", color="#1f4e79",
            label="t95 deconvolved")
    a0.axhline(gt, color="#c00000", ls="--", label=f"true t95={gt:.0f}s")
    a0.set_xlabel("sampling period Δt [s]")
    a0.set_ylabel("t95 [s]")
    a0.set_title("Mixing-time recovery vs Δt")
    a0.legend(fontsize=8)
    a0.grid(alpha=0.3)
    ratios = tau_grid[0]
    tmix_errs = [tau_grid[2][i, 1] for i in range(len(ratios))]  # dt=10 column
    a1.plot(ratios, tmix_errs, "o-", color="#1f4e79")
    a1.axhline(0, color="0.5", lw=0.8)
    a1.set_xlabel("probe lag ratio  τ_probe / t95")
    a1.set_ylabel("median |deconvolved t95 error| [s]")
    a1.set_title("Deconvolution residual vs lag ratio (Δt=10 s)")
    a1.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig_mixing_validation.png", dpi=150)
    plt.close(fig)

    # ---- Robustness heatmaps ----
    fig, (a0, a1) = plt.subplots(1, 2, figsize=(12, 4.6))
    noises, periods, en = noise_grid
    im0 = a0.imshow(np.abs(en), aspect="auto", origin="lower", cmap="YlOrRd",
                    vmin=0, vmax=60)
    a0.set_xticks(range(len(periods)))
    a0.set_xticklabels(periods)
    a0.set_yticks(range(len(noises)))
    a0.set_yticklabels(noises)
    a0.set_xlabel("sampling period Δt [s]")
    a0.set_ylabel("read noise [pH]")
    a0.set_title("|t95 error| [s] over (Δt, noise)")
    for i in range(len(noises)):
        for j in range(len(periods)):
            v = en[i, j]
            a0.text(j, i, "—" if not np.isfinite(v) else f"{v:+.0f}",
                    ha="center", va="center", fontsize=7)
    fig.colorbar(im0, ax=a0, shrink=0.85)
    ratios, periods2, et = tau_grid
    im1 = a1.imshow(np.abs(et), aspect="auto", origin="lower", cmap="YlOrRd",
                    vmin=0, vmax=60)
    a1.set_xticks(range(len(periods2)))
    a1.set_xticklabels(periods2)
    a1.set_yticks(range(len(ratios)))
    a1.set_yticklabels(ratios)
    a1.set_xlabel("sampling period Δt [s]")
    a1.set_ylabel("τ_probe / t95")
    a1.set_title("|t95 error| [s] over (Δt, lag ratio)")
    for i in range(len(ratios)):
        for j in range(len(periods2)):
            v = et[i, j]
            a1.text(j, i, "—" if not np.isfinite(v) else f"{v:+.0f}",
                    ha="center", va="center", fontsize=7)
    fig.colorbar(im1, ax=a1, shrink=0.85)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig_robustness.png", dpi=150)
    plt.close(fig)

    print("[validate] wrote figures and CSVs")
    print(f"  sensor: tau recovered within "
          f"{max(abs(r['tau_err_pct']) for r in sensor_rows):.0f}% up to Δt=20s")
    print(f"  mixing: true t95={gt:.0f}s; deconvolved error "
          f"{min(r['err_deconv_s'] for r in mixing_rows):+.0f}.."
          f"{max(r['err_deconv_s'] for r in mixing_rows):+.0f}s")


if __name__ == "__main__":
    main()
