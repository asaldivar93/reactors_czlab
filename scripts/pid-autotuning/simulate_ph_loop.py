"""Run the packaged split-range closed-loop simulation demonstration."""

from __future__ import annotations

import argparse
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reactors_czlab.autotune.model import Chemistry, PhPlant, PlantParams
from reactors_czlab.autotune.simulation import (
    SplitRangeConfig,
    SplitRangeController,
    metrics,
    simulate,
)


def main(outdir: str = "figures", seed: int = 0) -> None:
    """Run the baseline TL-PI loop and write its trace figure."""
    os.makedirs(outdir, exist_ok=True)
    plant = PhPlant(
        PlantParams(
            V0=5.0,
            C_P0=0.014,
            pH0=7.4,
            c_base=0.5,
            c_acid=0.5,
            chem=Chemistry(),
        )
    )
    config = SplitRangeConfig(
        setpoint=7.0,
        kp=5.83,
        ki=0.009,
        kd=0.0,
        dead_band=0.02,
        dt=10.0,
    )
    controller = SplitRangeController(config)

    def metabolic_load(time: float) -> float:
        return 2e-7 if time > 1800 else 0.0

    result = simulate(
        controller,
        plant,
        t_end=3600.0,
        r_metabolic_fn=metabolic_load,
        noise_pH=0.005,
        dead_time=10.0,
        seed=seed,
    )
    print("[simulate_ph_loop] baseline TL-PI run:")
    for name, value in metrics(result, config.dt).items():
        print(f"   {name:18s} = {value:.4f}")

    figure, (ph_axis, demand_axis) = plt.subplots(
        2,
        1,
        figsize=(9, 6),
        sharex=True,
    )
    ph_axis.plot(result.t / 60.0, result.pH, color="#1f4e79", lw=1.4)
    ph_axis.plot(
        result.t / 60.0,
        result.setpoint,
        color="0.5",
        ls="--",
        lw=1,
    )
    ph_axis.axvline(30.0, color="#c00000", ls=":", lw=1)
    ph_axis.set_ylabel("pH")
    ph_axis.grid(alpha=0.3)
    demand_axis.step(
        result.t / 60.0,
        result.base_mL,
        where="post",
        color="#1f4e79",
        lw=1.0,
        label="base",
    )
    demand_axis.step(
        result.t / 60.0,
        -result.acid_mL,
        where="post",
        color="#c00000",
        lw=1.0,
        label="acid",
    )
    demand_axis.set_ylabel("titrant/period [mL]")
    demand_axis.set_xlabel("time [min]")
    demand_axis.legend(fontsize=8)
    demand_axis.grid(alpha=0.3)
    figure.tight_layout()
    figure.savefig(f"{outdir}/fig_baseline_loop.png", dpi=150)
    plt.close(figure)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", default="figures")
    parser.add_argument("--seed", type=int, default=0)
    arguments = parser.parse_args()
    main(arguments.outdir, arguments.seed)
