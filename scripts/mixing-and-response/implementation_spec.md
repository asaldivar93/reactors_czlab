# Implementation specification: sensor response time and reactor mixing time for `reactors_czlab`

This document tells a coding agent exactly what to build so that operators can (A) measure
a sensor's response time from a step test, and (B) measure a reactor's mixing time from an
acid/base pulse. It mirrors the structure and conventions of
`scripts/pid-autotuning/implementation_spec.md`; read that first, because this feature
reuses much of the autotuning machinery (dose budgeting, actuator ownership, the
step-by-step operator-driven run object, OPC method exposure, JSON audit).

The scientific basis is in `literature_review.md`; the validated methodology, equations,
and operating envelope are in `methodology.md`; the reference behaviour a correct
implementation must reproduce is the prototype in this directory (`estimators.py`,
`response_mixing_model.py`, `validate.py`). Numbers quoted in the acceptance tests (§7) are
the deterministic outputs of that prototype.

---

## 1. Scope and terminology

Two features, deliberately coupled:

- **Feature A, sensor response time.** Fit a first-order-plus-dead-time (FOPDT) model to
  an operator-applied step and report `tau` (τ), dead time `L`, and the model-free
  `T63`/`T90` landmarks, with goodness-of-fit and resolution acceptance metrics.
- **Feature B, mixing time.** Inject one acid/base bolus, read the pH transient, linearize
  it to the strong-ion difference `Z`, deconvolve the probe's first-order lag using the τ
  from Feature A, and report `t95`/`t90` homogenization times by the last-crossing band
  criterion.

**The coupling is a hard requirement, not an optimization.** A pulse read on a real probe
is the mixing dynamics convolved with the probe lag; ignoring the probe overestimates
mixing time by an amount comparable to τ (in the validation, a true 58 s reads ~100 s).
Feature A therefore ships first and its τ is a **required input** to Feature B.

Terms (`tau`, `L`, `T63`/`T90`, `Z`, `t95`/`t90`, Δt) are defined in `methodology.md §1`.

---

## 2. What already exists (integration surface)

Hook into these existing structures; paths are relative to the repo root. These were read
from the current source, not assumed.

- **`core/sensor.py :: Sensor`**: the reading source. A characterization run consumes the
  `(timestamp, value)` stream the sampling loop already produces for a sensor; it does not
  read hardware directly.
- **`core/reactor.py :: Reactor` / `Sampling`**: the loop. The **server-wide sampling
  period is bounded `MIN_SAMPLE_PERIOD = 1.0` … `MAX_SAMPLE_PERIOD = 30.0` seconds**
  (default 10 s), changed atomically for sampling and actuator control together via
  `update_period(period)`. `Sampling.pairings` maps `{sensor_id: [(actuator_id, channel), …]}`
  This is how Feature B discovers the **base** and **acid** actuators paired to a pH
  sensor, exactly as the autotuner does. The characterization run receives samples through a
  private structural `Protocol` (like `core/reactor.py::_AutotuneRunLike`); **`core` must not
  import the new package.**
- **`core/actuator.py :: Actuator`**: for Feature B's bolus. It already exposes the
  autotune-ownership surface the mixing run reuses verbatim:
  `claim_autotune(owner)` / `autotune_demand(owner, volume_ml)` / `autotune_tick(owner)` /
  `release_autotune(owner)` (defined at `core/actuator.py:123–174`). Claiming makes the
  normal control loop leave the actuator alone; the mixing run is just another owner.
- **`core/dispenser.py :: Dispenser`**: converts a mL demand into a timed dose; `reset()`
  cancels a delivery in flight. The bolus goes through this, not a raw pump write.
- **`autotune/runtime.py`**: reuse, do not reimplement:
  - **`default_dose_budget_ml(volume_l, phosphate_molar, setpoint, base_molar, acid_molar, chemistry=None)`**
    (`runtime.py:206`) returns one safe-band (pH ±1, clamped to 4–10) traversal in mL. This
    is the dose cap for the bolus.
  - **`robust_noise_sigma(timestamps, values)`** (`runtime.py:177`): Theil–Sen detrended MAD
    noise estimate; use it on the pre-pulse baseline for the SNR gate and on the step baseline
    for Feature A.
  - **`AutotuneRun`** (`runtime.py:271`): the state-machine template to copy: a plain object
    holding run state, driven step-by-step, **returning a status object from every method**
    (`preflight`/`start`/`sample`/`tick`/`abort`/`status`), with injectable `clock`, a
    `preflight()` that validates before touching hardware, `AutotunePhase` (a `StrEnum`), and
    `_terminate`/`_cleanup_claimed` teardown that releases claimed actuators.
- **`autotune/model.py`**: the chemistry, the single source of truth for pH ↔ composition:
  - **`state_from_ph(ph, phosphate_molar, chem)`** returns the strong-ion difference `Z`;
    this is the linearization used by both the dose budget and the mixing estimator.
  - **`Chemistry`**: default constants (pKa's, etc.).
- **`opcua/autotune.py :: ReactorAutotuneOpc`**: the OPC exposure template.
  `init_methods(idx)` wraps each run method in an `@uamethod` with declared `ua.Argument`s and
  returns status strings the operator reads off each call (`opcua/autotune.py:44,81`). Mirror
  this for the two new runs. **`core` never imports `opcua`; `opcua` calls into `core`/the new
  package.**
- **`calibration_dir()`** (used by the autotune audit): the directory for JSON run records;
  write the characterization results next to the pump calibrations and autotune records.

---

## 3. Deliverables (production code)

Create these; mirror the existing autotune/calibration feature's structure and style
(Python ≥ 3.11, numpydoc docstrings, lazy `%`-style logging, `pathlib`, ruff line length).

1. **`characterization/`**: a new OPC-independent package (sibling of `autotune/`):
   - `estimators.py`: the two pure estimators and their result dataclasses. **Port the
     prototype `scripts/mixing-and-response/estimators.py` almost verbatim**; it already
     imports the repo chemistry via `state_from_ph` and depends only on numpy/scipy. Public
     surface: `estimate_response_time(t, y, step_time=None) -> ResponseEstimate`;
     `estimate_mixing_time(t, ph, phosphate_molar, *, pulse_time, tau_probe, band95=0.05, band90=0.10, smooth_window, chem=None) -> MixingEstimate`;
     helpers `ph_to_tracer`, `deconvolve_first_order`.
   - `runtime.py`: two run objects, `ResponseTimeRun` and `MixingTimeRun`, each following the
     `AutotuneRun` pattern (§4). `MixingTimeRun` reuses `default_dose_budget_ml`,
     `robust_noise_sigma`, and the actuator-ownership surface.
   - `audit.py`: versioned JSON persistence (§3.3), reusing `calibration_dir()`.
   `core/reactor.py` interacts with a run only through a private structural `Protocol` and does
   not import this package.
2. **`opcua/characterization.py`**: `init_characterization_methods(idx)` exposing both
   workflows (§5) as `@uamethod`s on the sensor's node (Feature A) and the **base** actuator's
   control node (Feature B, the canonical owner of the pH loop). Mirror
   `opcua/autotune.py::ReactorAutotuneOpc`.
3. **Persistence**: write each run's inputs and results plus the Δt in force and a UTC
   timestamp to JSON next to the calibrations (filenames e.g.
   `<reactor>_<sensor>_response.json`, `<reactor>_mixing.json`). This is the audit record and
   the source of the τ that Feature B consumes.
4. **Tests**: `tests/test_characterization.py`: unit tests of the estimators against the
   analytic cases in §7, and integration tests driving `ResponseTimeRun`/`MixingTimeRun`
   against the `response_mixing_model.py` fakes so CI reproduces the reference numbers without
   hardware.
5. **Docs**: a short operator note (how to run each workflow from an OPC client), added to the
   README or `docs/`.

---

## 4. Algorithm specification (the two run objects)

Both are plain state machines driven by the sampling loop, one `sample(value, timestamp)`
call per tick, returning a status object each call. Use a `CharacterizationPhase(StrEnum)`
with the phases below.

### 4.1 `ResponseTimeRun` (Feature A)

Phases: `IDLE → BASELINE → RISING → SETTLING → DONE` (`ABORTED` on any failure).

1. **`preflight()`**: validate before touching anything: sensor exists; the current Δt is
   fine enough (require `Δt ≤ expected_tau/3`, with `expected_tau` an operator-supplied hint;
   if none, warn and proceed). Return a status describing what will happen. This step performs
   no I/O.
2. **`start()`**: snapshot the baseline window, record `step_time` (the operator triggers the
   physical step externally, e.g. a probe transfer between buffers or air→N₂ for DO; the run only
   observes), enter `BASELINE`.
3. **`sample(value, t)`**: accumulate. Detect the step (first sustained departure from the
   baseline beyond `k·robust_noise_sigma`), move to `RISING`, then `SETTLING` once the signal
   is within a small band of its running final value for several samples.
4. **`tick()`**: bounded by `MIN_RUN_SECONDS`/`MAX_RUN_SECONDS` (reuse the calibration
   constants); abort on timeout.
5. On settle, call `estimate_response_time` on the collected trace, populate a
   `ResponseEstimate`, evaluate acceptance (§ `methodology.md 2.3`), persist, enter `DONE`.

### 4.2 `MixingTimeRun` (Feature B)

Phases: `IDLE → BASELINE → PULSE → MIXING → SETTLING → DONE` (`ABORTED` on failure).

1. **`preflight()`**: resolve the base/acid pair from `Sampling.pairings`; compute the dose
   with `default_dose_budget_ml` at the operating pH and confirm the requested bolus is within
   budget and within the dispenser's per-call cap; require a valid `tau_probe` from a prior
   Feature-A run (refuse if absent, since the measurement is not trustworthy without it); check
   `Δt ≤ expected_t95/3` if a hint is given. No I/O.
2. **`start()`**: `claim_autotune(self)` on the chosen actuator; record a pre-pulse baseline
   window; enter `BASELINE`.
3. After the baseline, **inject the bolus** via `autotune_demand(self, volume_ml)` (a single
   dose, not a control loop), enter `PULSE → MIXING`.
4. **`sample(ph, t)`**: accumulate pH. Estimate baseline noise with `robust_noise_sigma`;
   enforce the **SNR gate**: `0.05·|final Z offset| ≥ 3·noise_sigma_z` (abort with a clear
   reason if the dose was too small, i.e. the buffer resisted it).
5. **`tick()`**: time-bounded as above; `autotune_tick` as needed; abort on timeout.
6. On settle, call `estimate_mixing_time(..., tau_probe=<from Feature A>, smooth_window=<~40 s / Δt, odd>)`,
   evaluate acceptance (§ `methodology.md 3.3`), persist `t95`, `t90`, `t95_raw`, and the
   lag-bias `t95_raw − t95`, then **`release_autotune(self)`** in teardown (reuse the
   `_cleanup_claimed` pattern so the actuator is always released, even on abort).

---

## 5. OPC-UA workflow (operator-facing)

Mirror `opcua/autotune.py`. Each method returns a status string.

**Feature A**, on the sensor node:
`response_preflight(expected_tau) → response_start() → (loop feeds samples) → response_status() → response_abort()`.
On `DONE`, `response_status` reports `tau`, `L`, `T63`, `T90`, `rmse_norm`, `n_points_rise`,
pass/fail, and the Δt used.

**Feature B**, on the base actuator's control node:
`mixing_preflight(bolus_ml, use_base) → mixing_start() → (loop feeds pH) → mixing_status() → mixing_abort()`.
`mixing_start` refuses if no valid `tau_probe` is available for the paired sensor. On `DONE`,
`mixing_status` reports `t95`, `t90`, `t95_raw`, the lag-bias, the SNR margin, pass/fail, and Δt.

---

## 6. Safety, limits, and interlocks (hard requirements)

- **Dose budget.** The bolus must not exceed `default_dose_budget_ml` at the operating pH,
  nor the dispenser's per-call cap. Preflight rejects an over-budget request before any pump
  moves.
- **Actuator ownership.** Feature B must `claim_autotune` before dosing and `release_autotune`
  in all teardown paths (success, abort, timeout). While claimed, the normal control loop must
  not drive that actuator (this is the existing interlock, reused).
- **Run time bounds.** Both runs honour `MIN_RUN_SECONDS`/`MAX_RUN_SECONDS` and abort on
  timeout.
- **Sampling-period contract.** Each run **records the Δt in force** and refuses or warns when
  Δt is too coarse for the dynamics: `Δt > τ/3` (Feature A) or `Δt > t95/3` (Feature B) is a
  fail/warn, not a silently-wrong number. A run must never change the server period on its own;
  if a finer period is needed for a fast probe, that is an explicit operator action via
  `update_period`.
- **SNR gate.** Feature B aborts with a specific reason when the pulse is too small to clear
  the ±5 % band above noise, rather than reporting a garbage `t95`.
- **Deconvolution applicability.** Feature B flags results where `tau_probe > 0.5·t95`
  (probe too slow to resolve this mixing time); the number is reported but marked
  low-confidence.
- **Injected clock.** Both runs take an injectable `clock` so tests neither wait nor guess at
  drift (as `AutotuneRun` does).

---

## 7. Acceptance tests (must pass in CI, no hardware)

Drive the runs and the estimators against the prototype fakes; these numbers are the
deterministic prototype outputs (`response_metrics.csv`, `mixing_metrics.csv`,
`robustness_metrics.csv`).

1. **Sensor recovery.** A synthetic FOPDT probe (`τ = 30 s`, `L = 5 s`) sampled at Δt ∈
   {2,5,10,15,20} s is recovered with `|τ̂ − 30| / 30 ≤ 0.05` and `|L̂ − 5| ≤ 1 s`. At Δt = 30 s
   `n_points_rise` drops to 2 and the acceptance criterion (`n_points_rise ≥ 3`) correctly
   fails.
2. **Model-free cross-check.** `T63` and `T90` match the analytic `L + τ = 35 s` and
   `L + 2.303·τ = 74.1 s` within one sampling interval for Δt ≤ 10 s.
3. **Mixing bias and correction.** For a known `t95 = 58 s` vessel measured through a
   `τ = 30 s` probe: the **raw** t95 overestimates by ≥ 40 s, and the **deconvolved** t95 is
   within +2…+22 s of truth for Δt ≤ 20 s. Deconvolution must reduce the absolute error by at
   least half.
4. **SNR gate.** At ≥ 0.002 pH read noise on this vessel the run aborts on the SNR gate rather
   than returning a `t95`; at ≤ 0.001 pH it succeeds.
5. **Lag-ratio applicability.** With `τ_probe / t95 ≥ 1` the result is flagged low-confidence.
6. **Ownership.** `MixingTimeRun` claims the actuator on `start` and releases it on `DONE`,
   `abort`, and timeout (assert the actuator is free afterwards in every path).
7. **Sampling-period contract.** A run with `Δt > τ/3` (or `> t95/3`) reports the fail/warn and
   records the Δt used.

---

## 8. Parameter defaults (single source of truth)

| Parameter | Default | Source |
|---|---|---|
| homogeneity bands | ±5 % (`t95`), ±10 % (`t90`) | `methodology.md §1` |
| band criterion | last (permanent) crossing | `methodology.md §1` |
| SG smoothing window | nearest odd to `40 s / Δt`, ≥ 5 samples | `estimators.py` |
| SNR gate | `0.05·|ΔZ| ≥ 3·σ_Z` | `methodology.md §3.3` |
| Δt fineness (A) | `Δt ≤ τ/3` | validation `fig_sensor_validation.png` |
| Δt fineness (B) | `Δt ≤ t95/3` | validation `fig_mixing_validation.png` |
| probe-lag applicability | `τ_probe ≤ 0.5·t95` | validation `fig_robustness.png` |
| dose cap | `default_dose_budget_ml(...)` | `autotune/runtime.py:206` |
| run time bounds | `MIN_RUN_SECONDS`/`MAX_RUN_SECONDS` | `core/calibration.py` |

---

## 9. Explicit non-goals

- No new hardware access: both features consume the existing sampling stream and the existing
  dispenser/actuator surface. Only `core/hardware.py` may touch hardware libraries.
- No automatic changing of the server sampling period; a finer Δt is an explicit operator action.
- No multi-pulse or step-input mixing methods in this release (single bolus only); the estimator
  API leaves room to add them later.
- No temperature- or DO-specific mixing analysis; Feature A's step test already covers DO probe
  response, and mixing is characterized on the pH probe.

---

## References

Scientific basis and primary sources: `literature_review.md`. Validated methodology,
equations, and operating envelope: `methodology.md`. Reference implementation and the exact
numbers the acceptance tests target: `estimators.py`, `response_mixing_model.py`,
`validate.py`, and the CSV/figure outputs in this directory.