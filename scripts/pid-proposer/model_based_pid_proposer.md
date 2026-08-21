# Model-Based PID Proposer — Methodology & Implementation Instructions

**Audience:** the coding agent who will implement this feature.
**Scope:** add a *model-only* PID proposal path to the existing `reactors_czlab.autotune`
subpackage. Given pump **calibrations** + user **chemistry**, propose PID gains
**without any experimental run**; then propose a **hysteresis** and **dose volume**
for the optional relay autotune the operator may run afterwards.

**Status of this document:** methodology + verified prototype + build instructions.
No feature code was written. Every API referenced below was checked against the
installed package (signatures in Appendix A). The prototype in Appendix B runs
against the real `autotune` modules and reproduces every number quoted here.

---

## 1. The question, answered

> Can we get PID values from the model alone, before any experimental autotune run?

**Yes — for this plant, to within ~1% of what a live relay run would produce.**

The pH loop here is a well-characterised **integrator + dead-time** process, and its
plant model (`autotune/model.py`) is fully determined by quantities the operator
already supplies for autotuning: reactor **volume**, **phosphate buffer concentration**,
**titrant molarities**, **setpoint**, **control period `dt`**, and an estimated **dead
time**. The pump **calibration** supplies the last missing piece — how a duty command
becomes a titrant flow — which fixes the *actuator gain* and the *minimum resolvable
dose*. With those, we can run the identification/tuning pipeline that already exists,
purely in simulation, and read out PID gains.

There are two ways to produce the gains, and the recommendation is to ship both:

1. **Model-relay identification (primary).** Build a `PhPlant` from the chemistry +
   calibration, run the *existing* `run_relay_experiment(...)` with **noise = 0**, and
   feed the resulting `Ku, Pu` into the *existing* `tuning_rules(...)` → `to_code_gains(...)`.
   This reuses the entire tested identification and tuning path; the only difference from
   a real autotune is that the plant is the model instead of the hardware. Verified: gains
   from this path match gains from a noisy "experimental" relay to ~1% (§4).

2. **Closed-form describing-function seed (secondary / cross-check).** A pen-and-paper
   `Ku, Pu` from integrator+dead-time relay theory (§3). It carries a constant bias
   (+18% on `Ku`, −14% on `Pu` vs the model relay) but needs no simulation, so it is
   ideal for (a) sanity-bounding the model-relay output and (b) *choosing the dose and
   hysteresis* that the model relay and the real relay will use.

![Model chemistry sets the local process gain the controller must invert]({{artifact:86cc4db1-8ed6-4b44-9805-b6f7db46bd3a}})

*Figure 1. The plant model is not a black box: the titration curve (a), the buffer
intensity β(pH) (b), and the resulting static process gain dpH/dmL (c) are all closed
functions of chemistry. Gain peaks where buffering is weakest (away from pKa₂ ≈ 7.2) —
this is exactly the nonlinearity the proposer must account for.*

---

## 2. Why this works — the physics the code already encodes

`autotune/model.py` models pH via a **charge-balance / strong-ion invariant** `Z`.
Adding titrant moves `Z` permanently; pH then follows from `Z` and the phosphate
buffer system. Two model quantities do all the work:

- **Buffer intensity** `β(pH) = dZ/dpH` — `buffering_intensity(ph, phosphate_molar)`.
  Units mol·L⁻¹·pH⁻¹. Large β ⇒ pH barely moves per mole of titrant.
- **Static process gain** `g = dpH/d(mL)` — `static_gain(ph, phosphate_molar, volume_l,
  titrant_molar)`. Derivable in closed form:

  ```
  g(pH) = c_titrant / (1000 · V · β(pH))        [pH per mL]
  ```

  (`c_titrant` in mol/L, `V` in L; the 1000 converts mL→L.) This is the single most
  important number for control: it is the plant gain the PID must invert, and it
  **changes with setpoint** because β does.

Because titrant integrates into `Z`, the open-loop plant behaves as a pure **integrator**
of gain `g` (pH per mL) in series with the loop **dead time** θ (sensor lag + mixing +
one control period). Integrator+dead-time is the textbook case where relay feedback has
**closed-form** ultimate gain and period — so we can predict the relay experiment instead
of running it.

**Where the calibration enters.** The model speaks in *mL of titrant*. The hardware speaks
in *pump duty*. The `Calibration` object bridges them:

- `flow_at(duty) -> mL/min` and `duty_for(flow) -> duty` fix the actuator map.
- `min_duty` (smallest duty that produces flow) fixes the **minimum resolvable dose per
  period**: `dose_min = flow_at(min_duty) · dt / 60` mL. The proposed dose must exceed
  this, or the relay cannot deliver a clean square wave.
- `installable_reason()` must return `None` for both pumps before the proposer trusts a
  calibration (already the gate used in `validate_autotune_selection`).

So: **chemistry → plant gain**, **calibration → actuator gain + dose floor**. Together
they close the loop model with no free parameters left to fit experimentally.

---

## 3. Closed-form seed (the secondary path and the dose/hysteresis basis)

For a symmetric hysteresis relay of half-amplitude `d` mL/period on an integrator of gain
`g` (pH/mL) with dead time θ and hysteresis band ±h:

```
ramp rate while dosing:   k1 = g · d / dt                         [pH/s]
limit-cycle amplitude:    a  = h + k1 · θ_eff                     [pH]
ultimate period:          Pu = 4h / k1 + 4·θ_eff                  [s]
ultimate gain (DF):       Ku = 4d / (π · sqrt(a² − h²))           [mL/pH]
```

with **effective dead time** `θ_eff = θ + dt` — the extra `dt` is the discrete
detect-then-act delay (measured empirically; see §4). These are implemented and tested in
Appendix B.

**Accuracy of the closed form** (vs the model relay, over a 3×3 grid of phosphate ∈
{7,14,28} mM and setpoint ∈ {6,7,8}): `Ku` biased **+17–18%**, `Pu` biased **−14%**, both
essentially *constant* across the grid. That constancy is what makes it useful: it is a
reliable *bound*, not a random error. Do **not** ship gains straight from the closed form;
use it to seed and to bound.

![The noise-free model relay reproduces the identification pipeline; the closed form is a biased but tight bound]({{artifact:f0321e07-b209-4d53-aced-99ab12b9c3ce}})

*Figure 2. (a) A model-relay limit cycle at pH 7, 14 mM, giving Ku ≈ 28.9 mL/pH,
Pu ≈ 141 s. (b) Closed-form Ku vs model-relay Ku across the 9-point chemistry grid — a
tight, constant +18% offset. (c) Pu prediction; all nine chemistries collapse to one point
because the dose proposer holds limit-cycle amplitude fixed, which makes Pu
chemistry-invariant (a useful design property, §5).*

---

## 4. Verification — model gains actually control the plant

The decisive test: derive gains from the model relay, then run them **closed-loop against
the model plant under a metabolic acid disturbance**, and compare to gains from a *noisy*
("experimental") relay and to deliberately de-tuned gains.

Operating point: pH 7.0, 14 mM phosphate, 5 L, 0.5 M titrant, `dt` 10 s, dead time 10 s,
TL-PI rule.

| Gain source            | kp     | ki      | IAE (pH·s) | max |error| | notes                    |
|------------------------|--------|---------|-----------:|-----------:|--------------------------|
| **model relay** (noise 0)      | 9.107  | 0.0296  | 23.7       | 0.054      | the proposal             |
| experimental relay (noisy)     | 8.75   | 0.0275  | 25.1       | 0.056      | what a real run gives    |
| hot (3× model)                 | 27.3   | 0.089   | 17.4*      | 0.031      | *lower IAE but oscillatory / 17% more titrant |
| cold (0.3× model)              | 2.73   | 0.0089  | 84.1       | 0.133      | sluggish                 |

**Model-derived gains match live-relay gains to ~1%** (kp 9.107 vs 8.75; ki 0.0296 vs
0.0275) and land in the well-behaved regime between the ringing "hot" and sluggish "cold"
guesses. The "hot" IAE is nominally lower only because it reacts faster to the step; it
does so by overshooting and spending more titrant, which is the wrong trade for a
bioreactor. The model relay is, for this plant, effectively a **virtual autotune**.

![Model-derived gains match live-relay gains and beat both detuned guesses]({{artifact:2ab4a842-8ca5-4eac-b1a1-a84083f0a8a5}})

*Figure 3. (a) Closed-loop pH under a metabolic acid load applied at t = 10 min. Model and
experimental traces are nearly indistinguishable; cold drifts, hot rings. (b) Integrated
absolute error.*

---

## 5. Proposing hysteresis and dose for the experimental run

If the operator chooses to run a real relay autotune afterwards, the proposer also hands
them the two parameters that most affect run quality. Both follow from the same model.

**Hysteresis `h` — from measurement noise.** The band must sit above the noise floor or
the relay chatters (false crossings). Estimate σ from a short baseline using the existing
`robust_noise_sigma(timestamps, values)` (MAD-based), then:

```
h_floor = 2·σ        (hard minimum)
h_reco  = 4·σ        (recommended; clean crossings with margin)
```

**Dose `d` — from buffering, targeting a fixed limit-cycle amplitude.** Invert the
amplitude relation `a = h + (g·d/dt)·θ_eff` for a target amplitude `a* = 3h` (i.e. the
limit cycle swings to 3× the hysteresis band — large enough to identify cleanly, small
enough to stay near setpoint):

```
d = (a* − h) · dt / (g · θ_eff) = 2h · dt / (g(pH) · θ_eff)
```

Because `g ∝ 1/(V·β)`, this gives `d ∝ V · β(pH) / c_titrant` — **larger reactors and
stronger buffers need bigger doses**, weaker titrant needs bigger doses. Targeting a fixed
amplitude is what made `Pu` chemistry-invariant in Fig 2c: a deliberate, convenient
property.

**Two constraints clamp `d`:**
1. **Calibration floor:** `d ≥ dose_min = flow_at(min_duty)·dt/60`. If the computed `d`
   is below the pump's minimum resolvable dose, raise `d` (and warn that amplitude will
   exceed target) or lengthen `dt`.
2. **Dose budget:** total titrant over the run must stay within
   `default_dose_budget_ml(V, phosphate_molar, setpoint, base_molar, acid_molar)`
   (already implemented; returns e.g. 112.6 mL at the reference point). Reject/flag if the
   proposed dose × expected cycles would breach it.

![Proposer outputs: dose volume from buffering, hysteresis from measurement noise]({{artifact:16f51da9-4e20-41e0-b89a-73ccac51915c}})

*Figure 4. (a) Proposed dose vs setpoint for three buffer strengths — peaks near pKa₂ where
buffering is strongest. (b) Hysteresis floor (2σ) and recommendation (4σ) vs measured pH
noise; the shaded region below 2σ is where the relay chatters.*

---

## 6. Proposed API and module layout

Add one module, **`reactors_czlab/autotune/proposer.py`**. It must not import GUI or
hardware code; it depends only on `model`, `relay`, `runtime` (for the helpers), and the
`Calibration` type. Keep it a pure function of dataclasses in / dataclass out, matching the
style of the rest of the subpackage.

```python
# autotune/proposer.py  (interface sketch — implement against Appendix A signatures)

@dataclass(frozen=True)
class ChemistrySpec:
    setpoint: float
    phosphate_molar: float
    volume_l: float
    base_molar: float
    acid_molar: float
    dt: float
    dead_time: float                 # operator estimate (sensor lag + mixing + dt)
    chemistry: Chemistry = Chemistry()

@dataclass(frozen=True)
class PumpSpec:
    calibration: Calibration         # must have installable_reason() is None
    # min resolvable dose derived internally from calibration.min_duty + dt

@dataclass(frozen=True)
class GainProposal:
    kp: float; ki: float; kd: float          # code gains, ready for SplitRangeConfig
    rule: str                                # 'TL-PI' | 'TL-PID' | 'ZN-PID' | 'SIMC'
    Ku: float; Pu: float                     # from the model relay
    Ku_closed_form: float; Pu_closed_form: float
    source: str = "model-relay"
    warnings: tuple[str, ...] = ()

@dataclass(frozen=True)
class ExperimentProposal:
    hysteresis: float                        # 4σ recommendation
    hysteresis_floor: float                  # 2σ hard minimum
    dose_ml: float                           # clamped by calibration + budget
    expected_amplitude_pH: float
    dose_budget_ml: float
    noise_sigma: float | None                # None if no baseline provided
    warnings: tuple[str, ...] = ()

def propose_gains(chem: ChemistrySpec, base: PumpSpec, acid: PumpSpec,
                  *, rule: str = "TL-PI") -> GainProposal: ...

def propose_experiment(chem: ChemistrySpec, base: PumpSpec, acid: PumpSpec,
                       *, baseline: Sequence[tuple[float, float]] | None = None
                       ) -> ExperimentProposal: ...
```

**`propose_gains` algorithm:**
1. Validate both calibrations (`installable_reason() is None`); collect warnings.
2. Compute `θ_eff = dead_time + dt`, `β = buffering_intensity(setpoint, phosphate_molar)`,
   `g = static_gain(...)`.
3. Compute the closed-form seed dose (§5) → `Ku_cf, Pu_cf` (§3).
4. Build `PhPlant(PlantParams(V0=volume_l, C_P0=phosphate_molar, pH0=setpoint,
   c_base=base_molar, c_acid=acid_molar, chem=chemistry))`.
5. Run `run_relay_experiment(plant, RelayTuneConfig(...), noise_pH=0.0)` → `Ku, Pu`.
   (Use the seed dose and a hysteresis of a few mpH; noise-free, so hysteresis only sets
   amplitude.)
6. `kc, ti, td = tuning_rules(Ku, Pu)[rule]` (or `simc_pid(Ku, Pu)` for SIMC);
   `kp, ki, kd = to_code_gains(kc, ti, td)`.
7. Cross-check: if `Ku` is not within a sane band of `Ku_cf / 1.18`, warn.
8. Return `GainProposal`.

**`propose_experiment` algorithm:**
1. If `baseline` given, `σ = robust_noise_sigma(ts, vals)`; else σ = None, use a default
   (e.g. 5 mpH) and warn.
2. `h = 4σ` (`h_floor = 2σ`).
3. `d = 2h·dt / (g·θ_eff)`; clamp to `dose_min` (calibration) and check against
   `default_dose_budget_ml(...)`; append warnings.
4. `expected_amplitude = h + (g·d/dt)·θ_eff`.
5. Return `ExperimentProposal`. The `dose_ml` and `hysteresis` map directly onto
   `RelayTuneConfig.base_dose_ml/acid_dose_ml/hysteresis` for the real run.

**Setpoint retuning for free.** If the operator later changes setpoint, do not re-propose
from scratch — use the existing `scale_gains_to_setpoint(kp, ki, kd, ph_tuned, ph_target,
phosphate_molar)`, which rescales by `β(target)/β(tuned)`. Mention this in the GUI.

---

## 7. Integration points (verified against the codebase)

- **Reuse, do not reinvent:** `run_relay_experiment`, `tuning_rules`, `simc_pid`,
  `to_code_gains`, `scale_gains_to_setpoint`, `robust_noise_sigma`,
  `default_dose_budget_ml`, `validate_autotune_selection` all already exist and are tested.
  The proposer is thin glue over them.
- **Gains are "code gains"** (`kp, ki, kd`) in the same convention `SplitRangeConfig`
  and the production `_PidControl` consume — no unit surprises. `to_code_gains` handles the
  `Kc/Ti/Td → kp/ki/kd` conversion.
- **No auto-apply.** Mirror the existing safety posture (plan doc + `audit.py`): the
  proposer *prepares* a `GainProposal`; installation stays behind the same
  review/validate/apply path the relay autotune uses. The audit module already has numeric
  keys for `kp/ki/kd/Ku/Pu/hysteresis/dose_*`; add a `record_*` for a model-proposal event.
- **Dead time is operator input.** The model has no way to know sensor lag; expose it in
  the GUI with a sane default (≈ one `dt`) and document that it is the largest source of
  proposer error.

---

## 8. Limitations — state these in the GUI and the docstring

1. **The model is the phosphate charge-balance model.** If the real reactor has additional
   buffering (media components, CO₂/bicarbonate, proteins) not in `Chemistry`, β is
   under-estimated and gains will be **too hot**. The proposal is a *starting point*; the
   experimental relay remains the ground truth.
2. **Dead time dominates the error budget.** A 2× error in θ moves `Pu` and thus `ki`
   materially. Prefer a slightly conservative (larger) θ.
3. **Calibration must be current.** Stale or poorly-fit calibrations (check `r2`,
   `installable_reason`) corrupt both the actuator gain and the dose floor.
4. **Closed form is a bound, not a value.** Never ship `Ku_closed_form` gains directly.
5. **Single operating point.** Gains are proposed at the setpoint; for wide excursions rely
   on the existing gain-scaling, and re-verify with a real run when in doubt.

---

## 9. Test plan for the implementer

- **Unit:** `g_static` vs `static_gain` agree to 1e-9 across a pH grid; closed-form
  `Ku/Pu` reproduce Appendix B values.
- **Property:** `propose_gains` output within ±5% of a noisy `run_relay_experiment` over
  the 3×3 chemistry grid (§4 is the reference).
- **Closed-loop regression:** proposed gains give lower IAE than 0.3× and non-oscillatory
  behaviour vs 3×, under a step disturbance (reproduce Fig 3).
- **Calibration clamp:** with a pump whose `min_duty` forces `dose_min > d`, assert the
  proposal raises `dose_ml` and emits the warning.
- **Budget clamp:** assert a breach of `default_dose_budget_ml` is flagged.
- **Guard:** un-fitted calibration (`installable_reason() is not None`) → proposal refuses
  with a clear message.

---

## Appendix A — Verified public API (installed package)

These signatures were introspected from the installed `reactors_czlab`; implement against
them exactly.

```
# autotune/model.py
buffering_intensity(ph, phosphate_molar, chemistry: Chemistry | None = None) -> float
static_gain(ph, phosphate_molar, volume_l, titrant_molar, chemistry=None) -> float
state_from_ph(ph, phosphate_molar, chemistry: Chemistry) -> float        # chemistry REQUIRED
ph_from_state(z, phosphate_molar, chemistry: Chemistry) -> float
analytic_titration_volume(ph, phosphate_molar, volume_l, base_molar, chemistry=None, *, initial_ph=2.0)
Chemistry(pka, pkw, K1, K2, K3, Kw)                                      # defaults = phosphate
PlantParams(V0, C_P0, Z0, pH0, c_base, c_acid, chem)

# autotune/relay.py
identify_ku_pu(ph_trace, u_trace, dt, relay_amp, hysteresis) -> (Ku, Pu)
tuning_rules(ku, pu) -> {'ZN-PID', 'TL-PI', 'TL-PID'}: (Kc, Ti, Td)
simc_pid(ku, pu, tau_c=None) -> (Kc, Ti, Td)
to_code_gains(kc, ti, td) -> (kp, ki, kd)                                # -> SplitRangeConfig
from_code_gains(kp, ki, kd) -> (kc, ti, td)
scale_gains(kp, ki, kd, beta_ratio) -> (kp, ki, kd)
scale_gains_to_setpoint(kp, ki, kd, ph_tuned, ph_target, phosphate_molar, chemistry=None)
RelayTuneConfig(setpoint, base_dose_ml, acid_dose_ml, hysteresis, dt, dead_time,
                max_cycles, settle_cycles, max_steps, max_minutes, phosphate_molar,
                base_molar, acid_molar, dose_budget_ml, acknowledge_other_loops,
                acknowledge_budget_override, baseline_seconds, baseline_samples,
                clean_cycles, max_adaptations)

# autotune/runtime.py
default_dose_budget_ml(volume_l, phosphate_molar, setpoint, base_molar, acid_molar, chemistry=None) -> float
robust_noise_sigma(timestamps, values) -> float                          # MAD-based
validate_autotune_selection(context, sensor_id, base_id, acid_id, setpoint) -> int

# autotune/simulation.py
run_relay_experiment(plant, cfg, noise_pH=0.0, seed=0) -> result(.Ku, .Pu, .a_amp, .t, .pH, .u)
Pump(...); SplitRangeConfig(setpoint, kp, ki, kd, dt, base_pump, acid_pump)
SplitRangeController(cfg); simulate(ctrl, plant, t_end, r_metabolic_fn, noise_pH, dead_time, seed)
simulation_metrics(res, dt) -> {IAE, max_abs_error, titrant_total_mL, ...}
settling_time(res, band) -> float

# core/calibration/models.py
Calibration(file, a, b, min_duty, max_duty, dispense_duty, points, fitted_at, r2, model,
            residual, fit_points, c, aic, zero_flow_duty)
  .flow_at(duty) -> mL/min ;  .duty_for(flow) -> duty ;  .installable_reason() -> str | None
```

**Gotcha caught during verification:** `buffering_intensity` and `static_gain` default
`chemistry` to `None` (→ phosphate defaults), but `state_from_ph` / `ph_from_state`
**require** an explicit `Chemistry`. Pass `Chemistry()` explicitly to be safe.

## Appendix B — Runnable prototype

The full prototype is saved alongside this document as
`autotune_proposer_prototype.py`. It imports the real package, implements `g_static`,
`closed_form_ku_pu`, `model_relay`, `propose_dose`, and `propose_gains`, and runs the
closed-loop verification. Verified output:

```
== proposer @ pH7, 14 mM, 5 L, 0.5 M ==
  dose      = 1.531 mL/period
  model Ku  = 29.14 mL/pH   Pu = 140.0 s
  closed-fm = 34.46 mL/pH   Pu = 120.0 s (+18% / -14%)
  gains     = kp 9.107  ki 0.02957  kd 0.000
  budget    = 112.6 mL
== closed-loop IAE under acid load ==
  model          IAE=  23.7  maxErr=0.054  titrant=193.1 mL
  experimental   IAE=  25.1  maxErr=0.056  titrant=192.4 mL
  hot 3x         IAE=  17.4  maxErr=0.031  titrant=226.7 mL
  cold 0.3x      IAE=  84.1  maxErr=0.133  titrant=199.4 mL
```

Run it with `PYTHONPATH=. python autotune_proposer_prototype.py` from the repo root.
