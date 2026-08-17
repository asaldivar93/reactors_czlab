"""Render figures from the packaged phosphate charge-balance model."""

from __future__ import annotations

import argparse
import os

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reactors_czlab.core.ph_model import (
    Chemistry,
    analytic_titration_volume,
    buffering_intensity,
    ph_from_state,
    state_from_ph,
    static_gain,
)


def main(outdir: str = "figures") -> None:
    """Write titration, speciation, buffering, and process-gain figures."""
    os.makedirs(outdir, exist_ok=True)
    chemistry = Chemistry()
    phosphate_molar = 0.014
    volume_l = 5.0
    base_molar = 0.5
    ph_grid = np.linspace(2.0, 12.0, 600)
    base_volume = analytic_titration_volume(
        ph_grid,
        phosphate_molar,
        volume_l,
        base_molar,
        chemistry,
    )
    check_points = np.linspace(ph_grid.min(), ph_grid.max(), 25)
    check_volume = analytic_titration_volume(
        check_points,
        phosphate_molar,
        volume_l,
        base_molar,
        chemistry,
    )
    initial_z = state_from_ph(2.0, phosphate_molar, chemistry)
    recovered = np.array(
        [
            ph_from_state(
                initial_z + base_molar * added / volume_l,
                phosphate_molar,
                chemistry,
            )
            for added in check_volume
        ]
    )
    max_error = float(np.max(np.abs(recovered - check_points)))

    figure, (curve_axis, speciation_axis) = plt.subplots(
        1,
        2,
        figsize=(11, 4.2),
    )
    curve_axis.plot(base_volume * 1000.0, ph_grid, color="#1f4e79", lw=2)
    curve_axis.scatter(
        check_volume * 1000.0,
        recovered,
        s=22,
        color="#c00000",
        label=f"solver recovery (max error {max_error:.1e} pH)",
    )
    curve_axis.set_xlabel("strong base added [mL of 0.5 M NaOH]")
    curve_axis.set_ylabel("pH")
    curve_axis.legend(fontsize=8)
    curve_axis.grid(alpha=0.3)
    fractions = chemistry.alphas(10.0 ** (-ph_grid))
    labels = ["H3PO4", "H2PO4-", "HPO4^2-", "PO4^3-"]
    for index, label in enumerate(labels):
        speciation_axis.plot(ph_grid, fractions[index], lw=1.8, label=label)
    speciation_axis.set_xlabel("pH")
    speciation_axis.set_ylabel("fractional abundance")
    speciation_axis.legend(fontsize=8)
    speciation_axis.grid(alpha=0.3)
    figure.tight_layout()
    figure.savefig(f"{outdir}/fig_titration_speciation.png", dpi=150)
    plt.close(figure)

    beta = np.array(
        [
            buffering_intensity(value, phosphate_molar, chemistry)
            for value in ph_grid
        ]
    )
    gain = np.array(
        [
            static_gain(
                value,
                phosphate_molar,
                volume_l,
                base_molar,
                chemistry,
            )
            for value in ph_grid
        ]
    )
    figure, (buffer_axis, gain_axis) = plt.subplots(1, 2, figsize=(11, 4.2))
    buffer_axis.plot(ph_grid, beta * 1000.0, color="#1f4e79", lw=2)
    buffer_axis.set_xlabel("pH")
    buffer_axis.set_ylabel("buffering intensity [mmol/L/pH]")
    buffer_axis.grid(alpha=0.3)
    gain_axis.semilogy(ph_grid, np.abs(gain), color="#1f4e79", lw=2)
    gain_axis.set_xlabel("pH")
    gain_axis.set_ylabel("process static gain [pH/L]")
    gain_axis.grid(alpha=0.3, which="both")
    figure.tight_layout()
    figure.savefig(f"{outdir}/fig_buffer_gain.png", dpi=150)
    plt.close(figure)

    print(f"[ph_process_model] figures written to {outdir}/")
    print(
        "[ph_process_model] solver-vs-analytic max pH error = "
        f"{max_error:.2e}"
    )
    print(
        "[ph_process_model] beta at pH 7.0 = "
        f"{buffering_intensity(7.0, phosphate_molar, chemistry) * 1e3:.3f} "
        "mmol/L/pH"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", default="figures")
    arguments = parser.parse_args()
    main(arguments.outdir)
