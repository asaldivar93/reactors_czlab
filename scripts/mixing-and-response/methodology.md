# Methodology: sensor response time and reactor mixing time

This document defines the two characterization procedures, states the equations that
turn a recorded trace into a number, and gives the operating envelope in which those
numbers are trustworthy. The envelope is not asserted; it is the result of the in-silico
study in `validate.py`, whose figures and CSV tables are referenced throughout. The
companion `literature_review.md` gives the primary-source basis for every definition;
`implementation_spec.md` translates this methodology into a build order for a coding
agent.

The two features are treated together on purpose. An acid/base pulse read on a real pH
probe is the mixing dynamics of the vessel *convolved with the probe's own first-order
lag*. The mixing time cannot be read off the raw pulse without first knowing the probe
time constant, so the sensor characterization is a prerequisite for the mixing
characterization, not an independent extra. This is the central methodological finding
and it shapes every design decision below.

---

## 1. Definitions and symbols

| Symbol | Meaning | Units |
|---|---|---|
| `tau` (τ) | first-order time constant of a sensor | s |
| `L` | transport delay (dead time) of a sensor | s |
| `T63`, `T90` | time to reach 63.2 % and 90 % of a step's final change | s |
| `Δt` | server sampling period (shared, 1–30 s, default 10 s) | s |
| `Z` | strong-ion difference (charge-balance reaction invariant) | mol L⁻¹ |
| `t95`, `t90` | homogenization time to ±5 % / ±10 % of the final tracer value | s |
| `τ_probe` | the pH probe's τ, reused from the response-time feature | s |

**First-order-plus-dead-time (FOPDT).** A sensor's response to a step change of size `K`
applied at time `t₀` is modelled as

```
y(t) = y₀ + K · (1 − exp(−(t − t₀ − L) / τ))   for t > t₀ + L,   else y₀.
```

`τ` is the time to reach 63.2 % of the final change measured from the end of the dead
time; `L` is the pure delay before any response begins. This is the standard
process-identification form and the same model the repo's autotuning feature already
identifies for the pH loop.

**T63 / T90.** Model-free landmarks read directly off the normalized step: the
interpolated times at which the response first *permanently* exceeds 0.632 and 0.90 of
its total change. They are reported alongside the fitted `τ`/`L` as an independent
cross-check that does not depend on the fit converging (`fig_sensor_validation.png`,
right panel, overlays the fit on the samples).

**Strong-ion difference `Z`.** The reaction invariant returned by the repo's
`state_from_ph`. It is linear in the moles of strong acid or base added to the vessel,
whereas pH is not. Every band criterion below is applied to `Z`, never to raw pH; this
is what makes "±5 % of the final value" a statement about *tracer concentration* rather
than about a logarithmic, buffer-dependent pH scale.

**Homogenization time `t95` / `t90`.** The time from the pulse until the linearized,
lag-corrected tracer signal *enters and remains* within ±5 % (±10 %) of its final value.
The criterion is the **last** band crossing (permanent entry), not the first: a signal
that dips into the band during an overshoot and leaves again is not yet homogeneous.

---

## 2. Feature A — sensor response time

### 2.1 Procedure (operator-performed step test)

1. Bring the sensor to a stable low reading and let the sampling loop record a short
   baseline (a handful of samples).
2. Apply a clean, fast step in the measured quantity. The standard bench methods are a
   probe transferred between two stirred buffers for pH, or between air-saturated and
   N₂-sparged media for dissolved oxygen. The step must be fast compared with the probe
   so that what is recorded is the probe's response, not the stimulus.
3. Let the loop record until the reading has plainly settled (≳ 5 τ).
4. Feed the timestamp/value trace and the known step time to `estimate_response_time`.

The test is operator-driven and uses only the ordinary sampling loop; it needs no
actuator and no chemistry. It is the exact analogue of the operator-driven relay
experiment the autotuning feature already runs.

### 2.2 Estimator

`estimate_response_time(t, y, step_time)` returns:

- `tau`, `dead_time`, `gain`: a bounded least-squares FOPDT fit (`scipy.optimize.curve_fit`);
- `t63`, `t90`: the model-free permanent-crossing landmarks;
- `rmse_norm`: RMS fit residual divided by the step size (the goodness-of-fit metric);
- `n_points_rise`: the number of samples strictly between 10 % and 90 % of the change
  (the resolution metric).

### 2.3 Acceptance criteria

- `rmse_norm ≤ 0.05`: the FOPDT model actually describes the data.
- `n_points_rise ≥ 3`: the step is resolved by enough samples to trust the fit. A step
  that rises within a single sampling interval yields a `τ` at the resolution floor and
  must be flagged, not reported as a number.
- fitted `τ` and model-free `T63 − L` agree within ~15 %.

### 2.4 Validated envelope

`validate.py::sensor_validation` recovers a known probe (`τ = 30 s`, `L = 5 s`) across
Δt (`response_metrics.csv`, `fig_sensor_validation.png`):

- `τ` recovered to within **1 %** for Δt ≤ 20 s;
- degradation sets in at Δt = 30 s, where only ~2 samples fall on the rising edge
  (`n_points_rise` correctly drops to 2, so the acceptance criterion catches it).

The practical rule: **the sampling period must be no coarser than roughly τ/3.** For a
probe faster than ~30 s, temporarily set the server to its 1 s floor for the duration of
the step test.

---

## 3. Feature B — reactor mixing time

### 3.1 Procedure (automated acid/base bolus)

1. Record a short pre-pulse baseline of pH (needed for the noise/SNR estimate).
2. Inject a single bolus of strong acid or base through the existing dispenser, using
   the split-range acid/base pair the autotuning feature already drives.
3. Record pH on the ordinary loop until it settles.
4. Feed the trace, the buffer concentration, and `τ_probe` (from Feature A) to
   `estimate_mixing_time`.

The dose is bounded by the same `default_dose_budget_ml` safe-band logic the autotuning
feature uses, and the run reuses the actuator-ownership/interlock machinery so a mixing
test cannot fight a control loop for the pumps.

### 3.2 Estimator — three stages

**Stage 1: linearize.** Map the pH trace to `Z` with `ph_to_tracer` (a vectorized wrapper
over the repo's `state_from_ph`). `Z` is proportional to added titrant, so the band
criterion becomes a statement about concentration.

**Stage 2: deconvolve the probe lag.** The probe output `y` and the true probe-zone
concentration `u` are related by `τ_probe · dy/dt + y = u`. The exact inverse is

```
u(t) = y(t) + τ_probe · dy/dt.
```

`deconvolve_first_order` evaluates this. Because differentiating a sampled signal
amplifies noise, the derivative is taken from a low-order Savitzky–Golay fit rather than
a raw finite difference; the smoothing window is sized to a few sampling intervals.

**Stage 3: band criterion.** On the linearized, deconvolved signal, find the **last**
time it is outside the ±5 % (±10 %) band; `t95` (`t90`) is the next sample, interpolated.

The estimator also returns `t95_raw` (same criterion, no deconvolution). The gap
`t95_raw − t95` is the probe-lag bias, reported so an operator can see how much the
correction moved the answer.

### 3.3 Acceptance criteria

- **SNR:** `0.05 · |final Z offset| ≥ 3 · noise_sigma_z`. The ±5 % band must stand clear
  of the baseline noise on `Z`, or the last-crossing criterion never finds a permanent
  entry. This is a **minimum-dose requirement**: the bolus must be large enough, which
  the buffer capacity near a pKa can make non-trivial (see §4).
- **Resolution:** Δt ≤ t95/3 (fewer samples across the transient give a quantized,
  upward-biased t95).
- **Probe-lag applicability:** `τ_probe ≤ 0.5 · t95`. You cannot resolve a mixing process
  faster than the probe watching it; deconvolution corrects a lag, it does not create
  bandwidth.

### 3.4 Validated envelope

`validate.py` recovers a known mixing time (`t95 = 58 s`, a poorly mixed vessel where the
bias is large) measured through a `τ = 30 s` probe (`mixing_metrics.csv`,
`fig_mixing_validation.png`):

- raw t95 overestimates by **+42 to +62 s**, the probe lag: exactly the bias the two
  coupled features exist to remove;
- **deconvolution cuts the error to +2 to +22 s** for Δt ≤ 20 s;
- at Δt = 30 s the period exceeds t95/2 and the estimate fails the resolution criterion.

The robustness sweep (`fig_robustness.png`, `robustness_metrics.csv`, median absolute
error over 12 noise realizations per cell) gives two sharp boundaries:

- **Noise / dose:** below ~0.001 pH read noise the deconvolved error stays within the
  small-error band; at ≥ 0.002 pH the method collapses (band drowns in noise). This fixes
  the minimum dose.
- **Probe-lag ratio:** the correction is reliable for `τ_probe / t95 ≤ 0.5` and fails once
  `τ_probe ≥ t95`.

---

## 4. Coupling of the two features

Everything above rests on one fact: the raw pulse is `mixing ⊛ probe`. In the validation
the true mixing time is 58 s but the raw measurement reads ~100 s, a **70 % overestimate**
caused entirely by a probe whose τ is a large fraction of the mixing time. A mixing-time
feature that ignored the probe would systematically brand well-mixed vessels as poorly
mixed. Deconvolution using the τ measured by Feature A removes almost all of that bias.
This is why the response-time feature must ship first and why its `τ` is a required input
to the mixing feature: not a convenience but a correctness requirement.

A second coupling is the **buffer**. Near a pKa the vessel resists dosing, so a fixed
bolus produces a smaller pH swing and a worse SNR. The dose budget must therefore be
computed from the charge-balance chemistry (`default_dose_budget_ml`) at the actual
operating pH, not set as a fixed volume, matching the logic the autotuning feature already
uses for its relay amplitude.

---

## 5. Shared sampling-period constraint

Every reactor on the server shares one sampling period, bounded at **1–30 s** (default
10 s; `core/reactor.py::MIN_SAMPLE_PERIOD`/`MAX_SAMPLE_PERIOD`). Both features consume
whatever cadence the loop provides; neither can sample faster on its own. The envelopes
above are stated in terms of Δt precisely because it is the binding constraint. The
operational consequence, carried into the spec as a hard requirement: a characterization
run must **record and report the Δt in force**, and must refuse (or warn) when Δt is too
coarse for the dynamics being measured: τ/3 for the step test, t95/3 for the pulse test.

---

## 6. Figures

- `fig_models.png`: the two forward models, a clean FOPDT step (T63/T90 marked) and a
  mixing pulse showing the non-exponential circulation overshoot smoothed and delayed by
  the probe.
- `fig_sensor_validation.png`: τ/L recovery vs Δt, and an FOPDT fit overlaid on samples.
- `fig_mixing_validation.png`: raw vs deconvolved t95 vs the true value, and the
  deconvolution residual vs probe-lag ratio.
- `fig_robustness.png`: median |t95 error| heatmaps over (Δt, noise) and (Δt, τ/t95),
  from which the operating envelope boundaries are read.
