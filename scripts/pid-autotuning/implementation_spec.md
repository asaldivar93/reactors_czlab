# Implementation specification: PID autocalibration (relay-feedback autotuning) for `reactors_czlab`

**Audience.** A coding agent implementing the feature. This document specifies *what* to build and
*how it must behave*; it does not contain the feature code. It assumes the algorithm, chemistry, and
validation described in the companion documents:

- `autocalibration_method.md` — the method, its equations, and the (Ku, Pu) → gains mapping.
- `process_model.md` — the charge-balance pH model used to validate the method in silico.
- `literature_review.md` — the primary-source basis for every design choice.

The in-silico reference implementation lives in `scripts/` (`relay_autotune.py`,
`simulate_ph_loop.py`, `ph_process_model.py`, `run_autotune_validation.py`, `robustness_sweep.py`,
`sampling_time_study.py`).
Those scripts are the *behavioural specification*: the production code must reproduce the identified
(Ku, Pu) and the resulting gains for the same relay parameters and plant, to within the tolerances in
§7.

---

## 1. Scope and terminology

Two distinct "calibrations" exist in this codebase; do not conflate them.

1. **Pump flow calibration** (already implemented, `core/calibration.py`): fits `flow = a·duty + b`
   so the `Dispenser` can convert a demanded volume (mL) into a duty and a run-time. This feature
   **depends on** it being current but does not change it.
2. **PID autocalibration** (this feature): finds the controller gains `kp, ki, kd` for the pH
   control loop by running an automated relay-feedback experiment on the live reactor, then writing
   the resulting gains to the control node. "Autocalibration" and "autotuning" are used
   interchangeably below and both mean *this* feature.

The feature targets the **pH loop only** in its first release (split-range NaOH/HCl, output unit =
volume). §9 notes what changes for the temperature loop.

---

## 2. What already exists (integration surface)

The implementer must hook into these existing structures; file paths are relative to the repo root.

- **`core/control.py :: _PidControl`** — the parallel PID. `get_value(measurement)` returns the
  demand; gains are `kp, ki, kd`; `setpoint`, `backwards`, `min_integral`, `max_integral`,
  `auto_integral_band` are fields of its `ControlConfig`. Output is in **mL** (volume) for the pH
  loop. Error is `direction·(setpoint − measurement)` with `direction = −1` when `backwards=True`.
- **Split-range pair.** The pH setpoint is served by **two** `_PidControl` instances on two
  actuators: the **base** pump (NaOH, `backwards=False`) pushes pH up, the **acid** pump (HCl,
  `backwards=True`) pushes it down. They share one pH sensor and one setpoint.
- **`core/actuator.py :: Actuator`** — owns a `controller` (`_PidControl`), a `dispenser`, a
  `channel`, and a `control_period`. `write_output(sens_value)` runs one control step:
  `demand = controller.get_value(sens_value)` → `dispenser.duty(demand)` → `_write_if_changed(...)`.
  `write(duty)` bypasses the change guard and drives the pump directly (used by the pump-calibration
  run). The **`calibrating` property** is the hardware interlock: while `True` the normal control
  loop leaves the actuator alone (see `actuator.py` lines guarding on `self.calibrating`).
- **`core/dispenser.py :: Dispenser`** — converts a mL demand into a bolus: `dispense_duty` for
  `60·mL/flow` seconds, capped at one control period's worth (`MAX_BOLUS_SECONDS`,
  `MIN_DISPENSE_FLOW` in `core/data.py`). `reset()` cancels a delivery in flight.
- **`core/calibration.py :: CalibrationRun`** — the template to copy. It is a plain object that holds
  run state, mutates the actuator, and **returns a status string from every method**; the operator
  drives it step-by-step from a generic OPC client and reads each result off the method call.
  Constructor takes injectable `clock`/`sleep` so tests neither wait nor guess at drift. Uses
  `actuator.calibrating = True/False` around the physical run, and restores
  `actuator.channel.old_value = 0` after `write()` (because `write()` bypasses the change guard).
  Constants `MIN_RUN_SECONDS = 1.0`, `MAX_RUN_SECONDS = 600.0`, `MAX_OUTPUT`.
- **`opcua/actuator.py :: ActuatorOpc`** — exposes the workflow over OPC-UA.
  `init_calibration_methods(idx)` wraps each `CalibrationRun` method in an `@uamethod` and declares
  its `ua.Argument`s. The control node already carries writable `kp`, `ki`, `kd`, `setpoint`,
  `min_integral`, `max_integral`, `auto_integral_band`, `backwards`, `curr_sensor` variables — the
  autotuner writes its result to `kp/ki/kd`.
- **`core/reactor.py :: Reactor` / `Sampling`** — the loop. `pairings` maps
  `{sensor_id: [(actuator_id, channel_index), …]}`; this is how the autotuner discovers **which two
  actuators** (base, acid) belong to a pH sensor.

---

## 3. Deliverables (production code)

Create these; mirror the existing calibration feature's structure and style.

1. **`core/autotune.py`** — a new module with:
   - `RelayTuneConfig` dataclass (all knobs in §4, with defaults).
   - `AutotuneRun` class — the run-state object, analogous to `CalibrationRun`, that executes the
     relay experiment on a **split-range actuator pair** and returns status strings. This is the
     algorithmic core; it must be unit-testable with injected `clock`/`sleep` and a fake plant.
   - Pure helper functions (no I/O): `identify_ku_pu(pH_trace, u_trace, dt, relay_amp, hysteresis)`,
     `tuning_rules(Ku, Pu) -> {rule: (Kc, Ti, Td)}`, `to_code_gains(Kc, Ti, Td) -> (kp, ki, kd)`,
     `scale_gains(kp, ki, kd, beta_ratio)`. Port these verbatim (behaviour-for-behaviour) from
     `scripts/relay_autotune.py`; they are already validated.
2. **`opcua/autotune.py`** (or extend `opcua/actuator.py`) — `init_autotune_methods(idx)` exposing
   the workflow (§5) as `@uamethod`s on the **base** actuator's control node (the base pump is the
   canonical owner of the pH loop; it drives the pair).
3. **Persistence** — write the identified `(Ku, Pu)`, the chosen rule, the resulting `(kp, ki, kd)`,
   the operating pH, buffer estimate, and a UTC timestamp to a JSON file next to the pump
   calibrations (reuse `calibration_dir()`; filename e.g. `<reactor>_ph_autotune.json`). On a
   subsequent `reload`, the loop reads gains from the control node as today; the JSON is the audit
   record and the source for "re-apply last tune".
4. **Tests** — `tests/test_autotune.py`: unit tests for the helpers against known analytic cases
   (§7), and an integration test driving `AutotuneRun` against the `ph_process_model.PhPlant` fake so
   CI reproduces the reference (Ku, Pu) within tolerance without hardware.
5. **Docs** — a short operator note (how to run the workflow from an OPC client) added to the repo
   README or `docs/`.

---

## 4. Algorithm specification (`AutotuneRun`)

### 4.1 The relay experiment

Regulate pH by a **relay** instead of the PID: while the measured pH is **below** setpoint − h, dose
the **base** pump a fixed bolus `u_base` mL per control period; while **above** setpoint + h, dose the
**acid** pump `u_acid` mL per period; inside the ±h hysteresis band, hold. This drives a bounded
limit cycle centred near the setpoint. Record the pH trace and the signed volume-per-period trace.

- The relay is realised **through the existing actuation path** — request `u` mL, let the
  `Dispenser` convert it to duty and run-time, exactly as in normal operation. Do **not** write raw
  duties. This guarantees the experiment sees the same nonlinearity (bolus cap, stall floor,
  flow-calibration) the controller will.
- The two pumps are one-sided actuators, so the relay is intrinsically **asymmetric**: allow
  `u_base ≠ u_acid`. A metabolically loaded loop settles into an asymmetric cycle; letting the two
  amplitudes differ lets the experiment sit centred on the setpoint rather than drift
  (Åström & Hägglund 1984, https://doi.org/10.1016/0005-1098(84)90014-1; Shen, Wu & Yu 1996,
  https://doi.org/10.1002/aic.690420431).

### 4.2 Identification

From the last **N complete cycles** (default ≥ 4, discarding the first transient cycle):

- **Period `Pu`** = mean peak-to-peak (or zero-crossing-to-zero-crossing) time of the pH oscillation.
- **Amplitude `a`** = half the mean peak-to-trough pH swing.
- **Ultimate gain** via the describing-function relation for a relay of effective amplitude `d`
  (mL/period) with hysteresis `h`:

  `Ku = 4·d / (π·√(a² − h²))`      (use `a` if `h = 0`)

  where `d` is the *signed* relay amplitude in the controller's output unit (mL). For the asymmetric
  case use the mean of the two half-amplitudes. `Ku` has units **mL/pH**.

Reject the run (return an error string, write no gains) if: fewer than N clean cycles formed within
the time budget; the amplitude is below a noise floor (`a < a_min`, default 3× the pH sensor noise
σ); or the cycle never centred (persistent drift → the asymmetry ratio is outside [0.2, 5]).

### 4.3 Gains

Map `(Ku, Pu)` to continuous parallel-PID parameters `(Kc, Ti, Td)` by a selectable rule; default
**Tyreus–Luyben PI** (conservative, well-suited to the near-integrating, dead-time-bearing pH loop):

- **TL-PI:** `Kc = Ku/3.2`, `Ti = 2.2·Pu`, `Td = 0`.
- Offer also ZN-PID, TL-PID, and SIMC as alternates (formulas in `autocalibration_method.md`).

Convert to the code's positional gains (see `autocalibration_method.md` §"gain mapping"):

`kp = Kc`,  `ki = Kc/Ti`,  `kd = Kc·Td`   (with `Ti, Td` in **seconds**; `ki` then has units
mL·pH⁻¹·s⁻¹, matching `_PidControl`'s `i_term = ki·error·dt`).

Apply the **same gains to both** split-range PIDs (base and acid); the `backwards` flag already gives
the acid PID the correct sign. Set `kd = 0` unless a PID rule is explicitly chosen (derivative on a
noisy pH probe is rarely worth it — see §"noise" in the method doc).

### 4.4 Operating-point gain scaling (optional, recommended)

The pH process gain varies with buffering intensity β(pH); gains tuned at one setpoint are too hot at
a lower-buffered setpoint and too cold at a higher-buffered one. When the operator moves the setpoint
substantially, scale all three gains by `s = β(pH_tuned)/β(pH_target)` (derivation in
`process_model.md` eq. 8; `scale_gains_to_setpoint` in `scripts/relay_autotune.py`). β can be
computed from the known phosphate concentration and pKa's, or estimated from the relay-cycle
asymmetry. Expose this as a method the operator can call after a setpoint change; do **not** apply it
silently inside the control loop.

---

## 5. OPC-UA workflow (operator-facing)

Mirror `init_calibration_methods`. Every method returns a status string. Declare `ua.Argument`s with
clear names/descriptions as the existing code does. Methods on the **base** actuator's node:

1. `autotune_start(setpoint, u_base_mL, u_acid_mL, hysteresis_pH, max_minutes)` → status.
   Validates ranges (see §6), sets `calibrating = True` on **both** actuators, resets both
   dispensers, records the start, and begins the relay experiment. Non-blocking start is acceptable
   if the run executes in a task; otherwise it runs to completion and returns the identification
   summary.
2. `autotune_status()` → status — cycles completed, current estimate of (Ku, Pu), or "running".
3. `autotune_abort()` → status — stop the relay, drive both pumps to 0, restore
   `channel.old_value = 0` on both, clear `calibrating`. Must be safe to call at any time.
4. `autotune_apply(rule)` → status — compute gains under `rule` (default TL-PI), write `kp/ki/kd` to
   **both** control nodes, persist the JSON record, and return the gains. Refuse if no valid
   identification is held.
5. `autotune_scale_to_setpoint(pH_target)` → status — apply §4.4 scaling to the currently installed
   gains and rewrite them; persist.
6. `autotune_reapply_last()` → status — read the stored JSON and rewrite the last accepted gains.

The `calibrating` interlock (already honoured by the control loop) guarantees the normal PID does not
fight the relay while a run is in progress.

---

## 6. Safety, limits, and interlocks (hard requirements)

- **pH excursion clamp.** Abort automatically if measured pH leaves a configurable safe band
  (default setpoint ± 1.0 pH, absolute floor/ceiling 4.0/10.0) for more than one control period.
  Cells are in the reactor during tuning; an unbounded relay must never be possible.
- **Total-dose budget.** Cap cumulative titrant volume for the whole run (default: the volume that
  would move an unbuffered reactor by 2 pH, computed from V and titrant molarity). Abort on exceed.
- **Time budget.** `max_minutes` hard stop (default 30). Relay period at 14 mM buffer, pH 7 is
  ~5 min (Pu ≈ 293 s in the reference), so ~4–6 cycles fit comfortably; reject if no clean cycles by
  the deadline.
- **Amplitude bounds.** `u_base, u_acid` within `[MIN_DISPENSE_FLOW-equivalent, one-period cap]`;
  `hysteresis ≥ 2×` pH sensor noise σ to avoid chattering on noise.
- **Sampling time (Δt) is variable — recompute Δt-dependent settings at run time.** The current
  control period is Δt = 10 s but it may change; the feature must read `actuator.control_period` at
  the start of every run and never hard-code 10 s. The identified **gains** are Δt-portable (the PID
  integrates/differentiates with the true elapsed time, so `ki`/`kd` are continuous-time quantities —
  see `autocalibration_method.md` §4.4), so a Δt change does **not** require re-tuning. But two things
  MUST scale with Δt:
  1. **Relay amplitude.** Specify the relay bolus so the limit-cycle amplitude stays a safe multiple
     of the hysteresis (`a ≳ 3·h`). Either fix the *per-period* bolus `u` (mL, the reference
     approach) or, if a *flow* (mL/min) is configured, compute `u = flow·Δt/60` and reject the run if
     the resulting `u` is too small to lift `a` clear of `h` at this Δt. At very small Δt an
     under-sized bolus makes `√(a²−h²)` → 0 and `Ku` diverges (verified failure at Δt = 2 s in
     `scripts/sampling_time_study.py`); guard against it explicitly.
  2. **Period-counted budgets.** The "minimum complete cycles", time budget, and dead-time delay
     (`round(dead_time/Δt)` periods) are all expressed in periods; recompute them from the live Δt.
  Recommended operating envelope: keep Δt ≤ Pu/10 (≈ 30 s here) so the zero-order-hold phase lag
  (≈ Δt/2) stays small against the loop period; above that, warn the operator that margin is eroding.
- **Interlock discipline.** Set `calibrating = True` on **both** actuators for the whole run; always
  restore it and `channel.old_value = 0` in a `finally` block (copy the `CalibrationRun` pattern).
  A crash mid-run must leave both pumps at 0.
- **Pump-calibration precondition.** Refuse to start if either pump's flow calibration is missing or
  stale (reuse `replacement_reason`/`load_into` from `calibration.py`). The relay's volume→duty
  conversion is only meaningful with a current flow calibration.
- **One loop at a time.** Refuse to start if the reactor's other loops would be disturbed by the pH
  swing in a way the operator has not acknowledged (document this; a simple flag is enough).
- **Concurrency.** Refuse a second `autotune_start` while one is running (mirror
  `CalibrationRun._running`).

---

## 7. Acceptance tests (must pass in CI, no hardware)

Drive `AutotuneRun` against `scripts/ph_process_model.py :: PhPlant` (the validated fake). With the
reference configuration (V = 5 L, C_P = 14 mM phosphate, setpoint 7.0, titrant 0.5 M,
`u_base = u_acid = 0.20 mL`, `hysteresis = 0.02 pH`, `dt = 10 s`, dead time 10 s, noise σ = 0.005 pH,
seed 0):

- **Identification.** `Ku` within ±15 % of the reference **18.6 mL/pH** and `Pu` within ±10 % of
  **293 s**. (These are the values `scripts/relay_autotune.py` produces; regenerate them by running
  that script and pin the test to its output.)
- **Gains.** TL-PI gains within ±15 % of `kp ≈ 5.83`, `ki ≈ 0.0090`, `kd = 0`.
- **Closed-loop.** With the applied gains, a metabolic acid load step of 3e-7 mol·L⁻¹·s⁻¹ is rejected
  with max |pH error| ≤ 0.05 and no sustained oscillation (reproduce
  `run_autotune_validation.py :: stage2a_robustness`, "autotuned" row).
- **Scaling.** Moving the setpoint to pH 5.8 with `autotune_scale_to_setpoint` avoids the limit cycle
  that un-scaled gains fall into (reproduce `stage2b_scaling`; scaled settling ≤ 900 s, unscaled does
  not settle).
- **Helper unit tests.** `identify_ku_pu` on a synthetic square/triangle wave of known period and
  amplitude returns that period and the describing-function `Ku` analytically; `to_code_gains`
  round-trips `(Kc, Ti, Td) ↔ (kp, ki, kd)`.
- **Interlock test.** `autotune_abort` mid-run leaves both pumps at duty 0 and `calibrating = False`;
  a pH-excursion beyond the safe band triggers an automatic abort.
- **Sampling-time (Δt) robustness.** Reproduce `scripts/sampling_time_study.py`: gains tuned at
  Δt = 10 s and applied unchanged at Δt ∈ {5, 20, 40} s keep disturbance-rejection IAE within ~1.6×
  of the Δt = 10 s value and never oscillate (confirms Δt-portable gains). And: the relay-amplitude
  guard rejects a run whose per-period bolus is too small to clear the hysteresis band (the Δt = 2 s
  fixed-flow failure case), rather than returning a bogus Ku.

Robustness envelope (informational, from `robustness_sweep.py`): re-running the relay in situ across
buffer 7–28 mM × setpoint 5.8–8.0 holds disturbance-rejection IAE in 27–38 and max error ≤ 0.033 pH;
1-D sweeps over volume (2–10 L), titrant molarity (0.1–1.0 M), and pump-calibration error (0.5–1.5×)
are all well-behaved. The production autotuner should stay within this band when tested against the
same plant.

---

## 8. Parameter defaults (single source of truth)

| Parameter | Default | Unit | Notes |
|---|---|---|---|
| `u_base`, `u_acid` | 0.20 | mL/period | asymmetric allowed; ≥ min dispensable bolus |
| `hysteresis` (h) | 0.02 | pH | ≥ 2× sensor noise σ |
| control period `Δt` | 10 (variable) | s | **read `actuator.control_period` at run time**; gains are Δt-portable, but relay amplitude and period-counted budgets scale with Δt (§6). Recommended Δt ≤ Pu/10 ≈ 30 s |
| min complete cycles | 4 | – | discard first transient cycle |
| tuning rule | TL-PI | – | `Kc=Ku/3.2, Ti=2.2·Pu, Td=0` |
| safe pH band | setpoint ± 1.0 | pH | hard abs floor/ceiling 4.0/10.0 |
| dose budget | 2-pH-unit-equivalent | mL | from V and titrant molarity |
| time budget | 30 | min | hard stop |
| amplitude noise floor `a_min` | 3σ | pH | reject weak cycles |

---

## 9. Notes for the temperature loop (future release)

The same relay method transfers to temperature (heater/cooler split range), with three differences:
(i) output unit is duty/power, not volume, so no dispenser volume→duty step; (ii) the process is a
near-linear first-order-plus-dead-time, so ZN-PID or SIMC with a nonzero `Td` is appropriate and
derivative action is usable (thermistor noise is far below pH-probe noise); (iii) safe band and dose
budget become a temperature-excursion clamp and a max-power/time budget. The `AutotuneRun` structure,
OPC workflow, and persistence are unchanged.

---

## 10. Explicit non-goals

- No change to the pump **flow** calibration (`core/calibration.py`), only a precondition check.
- No new control algorithm — gains feed the **existing** `_PidControl`.
- No automatic, silent re-tuning in the control loop — every tune is operator-initiated and every
  gain write is recorded.
- No model-based (internal-model) controller — the relay method is model-free by design; the
  charge-balance model is used only for in-silico validation and for the optional β gain scaling.

---

## References

Full bibliography with verified DOIs is in `literature_review.md`. Key sources for this spec:
Åström & Hägglund (1984) https://doi.org/10.1016/0005-1098(84)90014-1;
Shen, Wu & Yu (1996) https://doi.org/10.1002/aic.690420431;
Tyreus & Luyben (1992) https://doi.org/10.1021/ie00011a029;
Skogestad (2003, SIMC) https://doi.org/10.1016/S0959-1524(02)00062-8;
Ziegler & Nichols (1942) https://doi.org/10.1115/1.4019269.
