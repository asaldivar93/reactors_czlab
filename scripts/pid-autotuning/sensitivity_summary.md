# Sensitivity of the relay-feedback autocalibration to process parameters

**Question.** When growing different strains — different working volumes, different medium buffer
concentrations, different NaOH/HCl molarities, different target pH — will the autocalibration
procedure itself have to be changed from the one already delivered?

**Answer.** No. The relay-feedback autotuner is self-adapting: it measures the process gain at the
live operating point every run, so a change in volume, buffer, or titrant strength is absorbed
automatically and the same procedure serves all strains. The **one** requirement is that the relay
must size its own bolus so the limit cycle clears the hysteresis band — a rule already mandated in
`implementation_spec.md` §6. Hard-coding a fixed bolus is the only thing that would force per-strain
retuning, and only in one corner (dilute titrant + high buffer + large volume).

This note draws entirely on results already produced (`robustness_metrics.csv`,
`scripts/robustness_sweep.py`); no new experiments were run.

## 1. Why the procedure is strain-agnostic

Everything the experiment needs is contained in the process static gain, given in closed form by the
process model (`process_model.md`, eq. 8):

```
    Kp(pH) = dpH/dV = c / ( V · β(pH) )        [pH per litre of titrant]
```

Every parameter that changes between strains enters through this single number:

- titrant molarity `c` — numerator (stronger titrant → larger gain),
- working volume `V` — denominator (bigger vessel → smaller gain),
- buffer intensity `β(pH)`, set by phosphate concentration `C_P` and the setpoint — denominator
  (more buffer → smaller gain).

The relay experiment **measures `Kp` directly** at the operating point; it assumes none of these
values. When `Kp` shifts, the identified ultimate gain `Ku` shifts with it, and the tuning rules
(Tyreus–Luyben by default) produce correspondingly rescaled `kp, ki, kd`. Re-identifying in situ is
the entire reason relay autotuning was chosen over a fixed gain table.

## 2. Evidence: the robustness sweep

`scripts/robustness_sweep.py` re-ran the full relay autotune in situ across the operating envelope
and scored disturbance rejection. Identification tracks the physics (Ku moves >10×, as eq. 8
predicts), while closed-loop performance stays in a tight band.

| Parameter varied | Range tested | Ku range (mL/pH) | Disturbance IAE | Max \|pH error\| | Stable? |
|---|---|---|---|---|---|
| Buffer C_P (× setpoint grid) | 7–28 mM, pH 5.8–8.0 | 2.5 → 36 | 27–38 | 0.017–0.033 | all |
| Working volume | 2–10 L | 9.6 → 35.5 | 28–35 | ~0.018 | all |
| Titrant molarity | 0.25–1.0 M | 10 → 38 | 28–36 | 0.018–0.020 | all |
| Pump-calibration error | 0.5–1.5× | 17.8 → 23.1 | 24–40 | 0.018–0.021 | all |

Scaling of the identified `Ku` matches the model: roughly **∝ V**, **∝ C_P** (stiffer plant needs
more gain), and **∝ 1/c**. Despite that >10× spread in `Ku`, IAE stays ~28–40 and peak deviation
under ~0.03 pH everywhere.

A further robustness property: even a **±50% pump-calibration error** stays stable, because the relay
is tuned on the *same* mis-calibrated pump it will later drive, so the error partially cancels.

![Robustness grid — identified Ku/Pu and closed-loop performance across buffer concentration × setpoint. Performance stays in a tight band because the tuner re-identifies at each condition.]({{artifact:55116e70-b078-443d-b4b6-90a168ecef05}})

![Robustness 1-D sweeps — working volume, titrant molarity, and pump-calibration error.]({{artifact:9a261dd8-e5ea-4667-89d1-bdd86044776f}})

## 3. The one thing that must adapt: relay bolus size

The single failure in the whole sweep is diagnostic: **0.1 M titrant produced Ku ≈ 3.8×10⁵** —
meaningless. It is the same failure mode as the Δt = 2 s case in the sampling-time study, with an
identical cause. The describing function is

```
    Ku = 4·d / ( π·√(a² − h²) )
```

If the per-period bolus `d` delivers too few titrant equivalents relative to what the buffer absorbs,
the pH limit-cycle amplitude `a` collapses toward the hysteresis half-width `h`, the denominator → 0,
and `Ku` diverges. The trigger is a **combination**: dilute titrant **and/or** high buffer **and/or**
large volume **and/or** too-small a bolus. This is not a control-law failure — it is an
under-powered *experiment*.

The fix is already a hard requirement in `implementation_spec.md` §6: the autotuner sizes its relay
bolus from the live operating condition so the cycle amplitude clears the hysteresis band
(target `a ≳ 3h`), rather than using a fixed mL bolus. Written that way, the procedure covers the
entire envelope above with no per-strain intervention, and it must reject a run (or enlarge the
bolus) when a limit cycle of sufficient amplitude cannot be established.

## 4. Practical guidance per strain

1. **Different target pH (strain optimum):** covered. The grid spans pH 5.8–8.0; the model-based
   β-scaling in `autocalibration_method.md` rescales gains for setpoint moves toward the buffer
   edges. No procedure change.
2. **Different medium buffer (7–28 mM) or vessel volume (2–10 L):** covered — the relay re-measures
   `Kp` each run. No procedure change.
3. **Different titrant concentration:** covered *provided the bolus is auto-sized*. The caution:
   pairing a **dilute titrant (≤ 0.1 M) with a strongly buffered, large-volume culture** is the
   corner where the bolus-sizing guard earns its place. Confirm that guard and the
   amplitude-clearance / "cannot establish limit cycle" abort are implemented, rather than
   hard-coding a fixed dose.

## Bottom line

Implement the relay-amplitude auto-sizing (already specified) and the same autocalibration procedure
serves all strains — different volumes, buffers, titrant molarities, and setpoints are handled by
in-situ re-identification. Only a hard-coded fixed bolus would force per-strain retuning, and only in
the dilute-titrant / high-buffer / large-volume corner.

## References

- Process model and static-gain law: `process_model.md` (eq. 8).
- Method, tuning rules, β-based gain scaling: `autocalibration_method.md`.
- Bolus auto-sizing requirement and acceptance tests: `implementation_spec.md` §6.
- Data and reproduction: `robustness_metrics.csv`, `scripts/robustness_sweep.py`;
  amplitude/`dt` failure mode: `scripts/sampling_time_study.py`.
