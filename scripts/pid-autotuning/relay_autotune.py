"""Run the packaged relay-feedback model and render its demonstration plot."""

from __future__ import annotations

import argparse
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reactors_czlab.autotune.model import Chemistry, PhPlant, PlantParams
from reactors_czlab.autotune.relay import (
    RelayTuneConfig,
    simc_pid,
    to_code_gains,
    tuning_rules,
)
from reactors_czlab.autotune.simulation import run_relay_experiment


def main(outdir: str = "figures", seed: int = 0) -> None:
    """Run the deterministic reference experiment and write its figure."""
    os.makedirs(outdir, exist_ok=True)
    plant = PhPlant(
        PlantParams(
            V0=5.0,
            C_P0=0.014,
            pH0=7.0,
            c_base=0.5,
            c_acid=0.5,
            chem=Chemistry(),
        )
    )
    config = RelayTuneConfig(
        setpoint=7.0,
        base_dose_ml=0.30,
        acid_dose_ml=0.30,
        hysteresis=0.02,
        dt=10.0,
        dead_time=10.0,
        max_cycles=10,
    )
    result = run_relay_experiment(
        plant,
        config,
        r_metabolic=2e-7,
        noise_pH=0.005,
        seed=seed,
    )
    rules = tuning_rules(result.Ku, result.Pu)
    rules["SIMC"] = simc_pid(result.Ku, result.Pu)

    print(
        f"[relay_autotune] Ku = {result.Ku:.3f} mL/pH, "
        f"Pu = {result.Pu:.1f} s, a = {result.a_amp:.4f} pH, "
        f"cycle mean pH = {result.cycle_mean_pH:.3f}, "
        f"cycles = {result.cycles_used}"
    )
    print("[relay_autotune] gains (kp, ki, kd) in code units:")
    for name, settings in rules.items():
        kc, ti, td = settings
        kp, ki, kd = to_code_gains(kc, ti, td)
        print(
            f"   {name:8s}: Kc={kc:8.3f} Ti={ti:8.1f} "
            f"Td={td:7.2f}  -> kp={kp:8.3f} ki={ki:8.4f} kd={kd:8.3f}"
        )

    figure, (ph_axis, demand_axis) = plt.subplots(
        2,
        1,
        figsize=(9, 6),
        sharex=True,
    )
    ph_axis.plot(result.t / 60.0, result.pH, color="#1f4e79", lw=1.3)
    ph_axis.axhline(config.setpoint, color="0.5", ls="--", lw=1)
    ph_axis.axhline(
        config.setpoint + config.hysteresis,
        color="0.75",
        ls=":",
        lw=0.8,
    )
    ph_axis.axhline(
        config.setpoint - config.hysteresis,
        color="0.75",
        ls=":",
        lw=0.8,
    )
    ph_axis.set_ylabel("pH")
    ph_axis.set_title(
        f"Relay-feedback experiment: Ku={result.Ku:.2f} mL/pH, "
        f"Pu={result.Pu:.0f} s"
    )
    ph_axis.grid(alpha=0.3)
    demand_axis.step(
        result.t / 60.0,
        result.u,
        where="post",
        color="#c00000",
        lw=1.0,
    )
    demand_axis.axhline(0, color="0.5", lw=0.8)
    demand_axis.set_ylabel("signed volume [mL]")
    demand_axis.set_xlabel("time [min]")
    demand_axis.grid(alpha=0.3)
    figure.tight_layout()
    figure.savefig(f"{outdir}/fig_relay_demo.png", dpi=150)
    plt.close(figure)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", default="figures")
    parser.add_argument("--seed", type=int, default=0)
    arguments = parser.parse_args()
    main(arguments.outdir, arguments.seed)
