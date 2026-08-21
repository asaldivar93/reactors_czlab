# Specification — Model-Based PID Proposer for the pH Autotune Subpackage

**Document type:** implementation specification (prescriptive, testable).
**Companion:** `model_based_pid_proposer.md` (methodology + rationale + prototype). Read
that first for *why*; this document is *what to build and how it will be judged*.
**Target package:** `reactors_czlab.autotune`.
**Implementer:** coding agent.

---

## 1. Purpose & scope

### 1.1 In scope
Add a **model-only PID proposal** capability to the `autotune` subpackage that, given pump
**calibrations** and user-supplied **chemistry**, returns:

- **REQ-1.** Proposed PID gains (`kp, ki, kd`) computed with **no experimental run**.
- **REQ-2.** A proposed **hysteresis** and **dose volume** for a subsequent *optional*
  relay autotune, plus the parameters that bound that run (budget, expected amplitude).

### 1.2 Out of scope
- Executing hardware or the live relay workflow (that is `runtime.py` / the GUI).
- Installing gains onto a controller (stays behind the existing review/validate/apply path).
- GUI/NiceGUI work, OPC-UA, persistence schema changes.
- Temperature or any non-pH loop.
- New tuning-rule math (reuse `relay.tuning_rules` / `relay.simc_pid`).

### 1.3 Design constraints
- **C-1.** New code lives in **one new module** `reactors_czlab/autotune/proposer.py`.
- **C-2.** `proposer.py` may import from `autotune.model`, `autotune.relay`,
  `autotune.runtime`, and the `Calibration` type in `core.calibration.models`. It **must
  not** import GUI, OPC-UA, hardware, or `runtime.AutotuneRun`/coordinator classes.
- **C-3.** Pure functions of dataclasses in → frozen dataclass out. No I/O, no logging side
  effects beyond a module logger, no global state.
- **C-4.** No new third-party dependencies (numpy/scipy already present).
- **C-5.** Reuse existing helpers; do not reimplement identification, tuning, gain
  conversion, noise estimation, or dose-budget math. (See §7 reuse table.)
- **C-6.** `architecture rule` (from the refactor requirements): `autotune` may know about
  `core`; `core` must not learn about the proposer.

---

## 2. Public interface (normative)

All dataclasses `@dataclass(frozen=True)`. Type hints as shown. Angle-bracket types are the
installed ones (Appendix A of the methodology doc).

### 2.1 Inputs

```python
@dataclass(frozen=True)
class ChemistrySpec:
    setpoint: float                  # target pH
    phosphate_molar: float           # total phosphate, mol/L (e.g. 0.014)
    volume_l: float                  # reactor working volume, L
    base_molar: float                # NaOH concentration, mol/L
    acid_molar: float                # HCl concentration, mol/L
    dt: float                        # control period, s
    dead_time: float                 # operator estimate: sensor lag + mixing + dt, s
    chemistry: Chemistry = Chemistry()   # defaults to phosphate system

@dataclass(frozen=True)
class PumpSpec:
    calibration: Calibration         # fitted; installable_reason() must be None
    role: str                        # "base" | "acid"  (for messages/warnings)
```

### 2.2 Outputs

```python
@dataclass(frozen=True)
class GainProposal:
    kp: float
    ki: float
    kd: float
    rule: str                        # "TL-PI" | "TL-PID" | "ZN-PID" | "SIMC"
    Ku: float                        # from noise-free model relay, mL/pH
    Pu: float                        # from noise-free model relay, s
    Ku_closed_form: float            # describing-function seed, mL/pH
    Pu_closed_form: float            # describing-function seed, s
    static_gain: float               # dpH/dmL at setpoint
    buffer_intensity: float          # beta at setpoint, mol/L/pH
    seed_dose_ml: float              # dose used for the model relay
    source: str = "model-relay"
    warnings: tuple[str, ...] = ()

@dataclass(frozen=True)
class ExperimentProposal:
    hysteresis: float                # recommended, pH (= 4 sigma)
    hysteresis_floor: float          # hard minimum, pH (= 2 sigma)
    dose_ml: float                   # per-period dose, clamped (see REQ-6/REQ-7)
    dose_min_ml: float               # calibration-imposed floor
    expected_amplitude_pH: float     # predicted limit-cycle half-amplitude
    dose_budget_ml: float            # from default_dose_budget_ml
    noise_sigma: float | None        # None if no baseline provided
    warnings: tuple[str, ...] = ()
```

### 2.3 Functions

```python
def propose_gains(
    chem: ChemistrySpec,
    base: PumpSpec,
    acid: PumpSpec,
    *,
    rule: str = "TL-PI",
    hysteresis: float = 0.02,        # noise-free relay: only sets amplitude
) -> GainProposal: ...

def propose_experiment(
    chem: ChemistrySpec,
    base: PumpSpec,
    acid: PumpSpec,
    *,
    baseline: "Sequence[tuple[float, float]] | None" = None,  # (timestamp_s, pH)
    amplitude_target_mult: float = 3.0,   # target limit-cycle amp = mult * hysteresis
    default_sigma: float = 0.005,         # used when baseline is None
) -> ExperimentProposal: ...
```

Both functions are **pure** and **deterministic** (REQ-8): the model relay must be called
with a fixed seed and `noise_pH=0.0`.

---

## 3. Functional requirements

### `propose_gains`
- **REQ-3 (validate).** Call `installable_reason()` on both calibrations. If either is not
  `None`, raise `ProposerError` (see §5) with a message naming the pump role and reason.
- **REQ-4 (derive plant quantities).** Compute
  `beta = buffering_intensity(setpoint, phosphate_molar, chemistry)`,
  `g = static_gain(setpoint, phosphate_molar, volume_l, base_molar, chemistry)`,
  `theta_eff = dead_time + dt`.
- **REQ-5 (seed dose).** `seed_dose = 2*hysteresis*dt / (g*theta_eff)` (targets amplitude
  ≈ 3·hysteresis). Clamp to the calibration floor `dose_min` (REQ-6). Record it.
- **REQ-5a (closed-form seed).** Compute `Ku_cf, Pu_cf` per the integrator+dead-time
  describing function (methodology §3): `a = h + (g*seed_dose/dt)*theta_eff`,
  `Pu_cf = 4h/(g*seed_dose/dt) + 4*theta_eff`,
  `Ku_cf = 4*seed_dose/(pi*sqrt(a^2 - h^2))`.
- **REQ-5b (model relay).** Build
  `PhPlant(PlantParams(V0=volume_l, C_P0=phosphate_molar, pH0=setpoint,
  c_base=base_molar, c_acid=acid_molar, chem=chemistry))`, then
  `run_relay_experiment(plant, RelayTuneConfig(setpoint=..., base_dose_ml=seed_dose,
  acid_dose_ml=seed_dose, hysteresis=hysteresis, dt=dt, dead_time=dead_time,
  max_cycles=10, settle_cycles=2), noise_pH=0.0, seed=0)` → `Ku, Pu`.
- **REQ-5c (gains).** For `rule in {"TL-PI","TL-PID","ZN-PID"}`:
  `kc, ti, td = tuning_rules(Ku, Pu)[rule]`. For `rule == "SIMC"`:
  `kc, ti, td = simc_pid(Ku, Pu)`. Then `kp, ki, kd = to_code_gains(kc, ti, td)`.
- **REQ-5d (cross-check warning).** If `Ku` and `Ku_cf/1.18` differ by more than a
  configurable tolerance (default ±30%), append a warning
  `"model relay Ku deviates from closed-form seed; check dead_time/dt"`. Do **not** raise.
- **REQ-9 (unknown rule).** An unrecognised `rule` raises `ProposerError`.

### `propose_experiment`
- **REQ-3** (same calibration validation as above).
- **REQ-10 (noise σ).** If `baseline` is given, `sigma = robust_noise_sigma(ts, vals)`;
  else `sigma = None`, use `default_sigma`, and append a warning
  `"no baseline provided; hysteresis derived from default sigma"`.
- **REQ-11 (hysteresis).** `hysteresis_floor = 2*sigma_eff`, `hysteresis = 4*sigma_eff`
  where `sigma_eff` is the measured σ or `default_sigma`.
- **REQ-12 (dose).** `dose = (amplitude_target_mult - 1)*hysteresis*dt / (g*theta_eff)`.
- **REQ-6 (calibration floor).** `dose_min = base.calibration.flow_at(min_duty) * dt / 60`
  (mL). Compute for both pumps; use the larger. If `dose < dose_min`, set `dose = dose_min`
  and append a warning `"dose raised to calibration floor; amplitude will exceed target"`.
- **REQ-7 (budget).** `budget = default_dose_budget_ml(volume_l, phosphate_molar, setpoint,
  base_molar, acid_molar, chemistry)`. Estimate run consumption
  (`dose * expected_cycles`, with a documented default `expected_cycles`, e.g. 2×max_cycles
  for both directions). If it exceeds `budget`, append a warning naming both numbers. Do
  **not** raise (the operator may `acknowledge_budget_override`).
- **REQ-13 (expected amplitude).** `expected_amplitude = hysteresis + (g*dose/dt)*theta_eff`.

### Shared
- **REQ-14.** `dose_ml` and `hysteresis` from `ExperimentProposal` must be directly usable
  as `RelayTuneConfig.base_dose_ml/acid_dose_ml` and `.hysteresis` for a real run (same
  units, same convention). Assert this by construction in a test.
- **REQ-15 (setpoint change).** Provide a thin re-export or docstring pointer to the
  existing `scale_gains_to_setpoint(...)` so a setpoint change does not require a fresh
  proposal. Do not reimplement it.

---

## 4. Units & conventions (normative)

| Quantity            | Symbol        | Unit          | Source                              |
|---------------------|---------------|---------------|-------------------------------------|
| setpoint / pH       | pH            | pH            | input                               |
| phosphate           | `phosphate_molar` | mol/L     | input                               |
| volume              | V             | L             | input                               |
| titrant conc.       | `c_base/c_acid` | mol/L       | input                               |
| control period      | dt            | s             | input                               |
| dead time           | θ             | s             | input (operator estimate)           |
| buffer intensity    | β             | mol·L⁻¹·pH⁻¹  | `buffering_intensity`               |
| static gain         | g             | pH per mL     | `static_gain` (`= c/(1000·V·β)`)    |
| ultimate gain       | Ku            | mL per pH     | `run_relay_experiment` / DF         |
| ultimate period     | Pu            | s             | `run_relay_experiment` / DF         |
| dose per period     | d             | mL            | proposer                            |
| hysteresis          | h             | pH            | proposer (4σ)                       |
| code gains          | kp, ki, kd    | as `SplitRangeConfig` | `to_code_gains`             |

The 1000 factor in `g` converts mL→L. `theta_eff = θ + dt` (the discrete detect-then-act
adds one period; do not omit it).

---

## 5. Error handling (normative)

- **ERR-1.** Define `class ProposerError(ValueError)` in `proposer.py`.
- **ERR-2.** Raise `ProposerError` for: un-fitted/uninstallable calibration (REQ-3);
  unknown `rule` (REQ-9); non-finite or non-positive `volume_l`, `dt`, `phosphate_molar`,
  titrant molarities; `dead_time < 0`; `setpoint` outside `[0, 14]`.
- **ERR-3.** All *recoverable* conditions (budget breach, dose raised to floor, missing
  baseline, Ku/DF mismatch) are **warnings** in the returned dataclass, never exceptions.
- **ERR-4.** Messages must name the offending quantity and value, matching the style of
  `runtime.validate_autotune_selection`.

---

## 6. Non-functional requirements
- **NFR-1 (determinism).** Identical inputs → byte-identical outputs (fixed relay seed).
- **NFR-2 (speed).** `propose_gains` returns in < 500 ms on the reference point (the model
  relay is ~10 cycles). No hardware, no sleeps.
- **NFR-3 (purity).** No file/network I/O; safe to call from any thread.
- **NFR-4 (typing).** Passes the repo's existing type checker / lint config with no new
  ignores. Full type hints.
- **NFR-5 (docstrings).** Module + every public symbol documented; docstrings state the
  limitations from methodology §8 (model buffering only; dead-time dominates error;
  calibration must be current; closed form is a bound; single operating point).

---

## 7. Reuse table (do not reimplement)

| Need                         | Use exactly                                              |
|------------------------------|----------------------------------------------------------|
| β(pH)                        | `model.buffering_intensity`                              |
| static gain                  | `model.static_gain`                                      |
| plant                        | `model.PhPlant`, `model.PlantParams`, `model.Chemistry`  |
| relay identification         | `simulation.run_relay_experiment` + `relay.RelayTuneConfig` |
| tuning rules                 | `relay.tuning_rules`, `relay.simc_pid`                   |
| gain unit conversion         | `relay.to_code_gains`                                    |
| setpoint retune              | `relay.scale_gains_to_setpoint`                          |
| noise σ                      | `runtime.robust_noise_sigma`                             |
| dose budget                  | `runtime.default_dose_budget_ml`                         |
| calibration map & floor      | `Calibration.flow_at`, `.min_duty`, `.installable_reason`|

Signatures are pinned in Appendix A of `model_based_pid_proposer.md`. Note: `state_from_ph`
/ `ph_from_state` require an explicit `Chemistry` (unlike `buffering_intensity`).

---

## 8. Acceptance criteria (definition of done)

The feature is complete when all of the following pass. Reference numbers are from the
verified prototype at pH 7.0, 14 mM phosphate, 5 L, 0.5 M titrant, dt 10 s, θ 10 s, TL-PI.

- **AC-1.** `propose_gains` at the reference point returns `kp ≈ 9.1`, `ki ≈ 0.0296`,
  `kd == 0.0`, `Ku ≈ 29.1`, `Pu ≈ 140`, `Ku_closed_form ≈ 34.5`, `Pu_closed_form ≈ 120`
  (tolerances: gains ±5%, Ku/Pu ±3%).
- **AC-2 (matches a real run).** Over the 3×3 grid phosphate ∈ {7,14,28} mM × setpoint ∈
  {6,7,8}, `propose_gains` gains are within **±5%** of gains from
  `run_relay_experiment(..., noise_pH≈0.006)` on the same plant.
- **AC-3 (closed-loop).** Under a step metabolic-acid load, proposed gains give **lower
  IAE than 0.3× gains** and **do not oscillate** relative to 3× gains (reproduces
  methodology Fig 3 / prototype table: model IAE ≈ 24 vs cold ≈ 84).
- **AC-4 (dose scaling).** `propose_experiment` dose scales as `V·β/c_titrant` (monotone in
  V and β; inverse in titrant molarity) — assert on three chemistries.
- **AC-5 (hysteresis).** With a synthetic baseline of known σ, returned `hysteresis == 4σ`
  and `hysteresis_floor == 2σ` (±1%).
- **AC-6 (calibration clamp).** With a calibration whose `min_duty` forces
  `dose_min > computed dose`, `dose_ml == dose_min` and the floor warning is present.
- **AC-7 (budget clamp).** A configuration that would exceed `default_dose_budget_ml`
  produces the budget warning and no exception.
- **AC-8 (validation guard).** An un-fitted calibration
  (`installable_reason() is not None`) makes both functions raise `ProposerError` with a
  message naming the pump role.
- **AC-9 (RelayTuneConfig round-trip).** `dose_ml`/`hysteresis` feed into a
  `RelayTuneConfig` and `run_relay_experiment` runs without error (REQ-14).
- **AC-10 (determinism).** Two identical calls return equal dataclasses (NFR-1).
- **AC-11.** Lint/type/format clean; public symbols documented (NFR-4/5).

---

## 9. Deliverables from the coding agent
1. `reactors_czlab/autotune/proposer.py` implementing §2 against §3–§7.
2. Export the public names from `reactors_czlab/autotune/__init__.py`.
3. Tests under the repo's test layout covering **AC-1 … AC-11** (§9 test plan of the
   methodology doc lists the same cases).
4. An audit hook: add a `record_model_proposal` path to `autotune/audit.py` consistent with
   the existing `record_*` methods and the numeric-key set (which already includes
   `kp/ki/kd/Ku/Pu/hysteresis/dose_*`). Preparation only — no controller write.
5. Short docstring/README note pointing operators at `scale_gains_to_setpoint` for setpoint
   changes (REQ-15).

---

## 10. Traceability

| Requirement | Verified by        | Reference in methodology doc          |
|-------------|--------------------|---------------------------------------|
| REQ-1/AC-1  | prototype run      | §1, §4, Appendix B                     |
| REQ-2/AC-4,5| prototype + Fig 4  | §5                                     |
| AC-2        | 3×3 grid           | §3 (grid), §4                          |
| AC-3        | closed-loop sim    | §4, Fig 3                              |
| REQ-5a/DF   | closed-form vs sim | §3, Fig 2                              |
| REQ-6/AC-6  | calibration model  | §2 ("Where the calibration enters")    |
| REQ-7/AC-7  | `default_dose_budget_ml` = 112.6 mL | §5                    |

The prototype `autotune_proposer_prototype.py` is the executable reference for AC-1..AC-3
and AC-10; the coding agent should keep its numbers as regression anchors.
