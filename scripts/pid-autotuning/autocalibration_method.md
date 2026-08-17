# Autocalibration method: relay-feedback PID autotuning for split-range pH control

This document specifies the PID *autocalibration* (autotuning) method developed for the
`reactors_czlab` pH loop and justifies each design choice against the process model
(`process_model.md`) and the literature (`literature_review.md`). The method is implemented in
`scripts/relay_autotune.py` and validated in-silico by the studies described in
`implementation_spec.md` and the accompanying simulation scripts. Throughout, "autocalibration" means
finding the PID gains `(kp, ki, kd)`; it is distinct from the pump *flow* calibration
(`flow = a·duty + b`) already present in `calibration.py`, which the autotuner assumes has already
been performed.

## 1. Problem statement and constraints

The pH loop in this system has four properties that jointly rule out an off-the-shelf autotuner and
dictate the design:

1. **Volume-output, split-range actuation.** pH is regulated by a *pair* of `_PidControl` instances
   on one pH sensor and one setpoint — a base loop (NaOH, `backwards=False`) and an acid loop (HCl,
   `backwards=True`). Each PID emits a **volume demand in mL**; the `Dispenser` converts each mL into
   a timed pump bolus (`dispense_duty` run for `60·mL/flow` seconds) using the flow calibration. The
   actuation is therefore **one-sided per reagent** — the base pump can only raise pH, the acid pump
   only lower it — and quantised into boluses with a minimum dispensable volume.
2. **Strong, known static nonlinearity.** The process static gain `Kp = dpH/dV` varies ~130× across
   the titration curve (process model §8) and is smallest on the phosphate plateau where the loop
   operates. One fixed gain set cannot be right across setpoints, but the shape of the nonlinearity is
   known from the medium composition.
3. **Integrating dynamics.** With no outflow, a net titrant flow accumulates the charge-balance
   invariant linearly (process model §6), so the plant is an integrator through a pH-dependent gain —
   the class for which Tyreus–Luyben and SIMC were designed.
4. **Slow, discrete, noisy loop.** The control period is `Δt = 10 s`, there is an
   actuation+mixing+sensing dead time of order one period, and bioreactor pH probes carry noticeable
   noise.

The method must produce `(kp, ki, kd)` for both loops from a short automated experiment, tolerate the
metabolic load a live culture imposes, and remain valid as the setpoint moves along the titration
curve.

## 2. Method overview

The autocalibration is a **four-stage procedure**:

1. **Relay-feedback experiment** — replace the PID pair with an asymmetric relay realised *through the
   same split-range pumps*, driving pH into a bounded limit cycle around the setpoint. Measure the
   ultimate period `Pu` and, via the relay describing function, the ultimate gain `Ku`.
2. **Tuning-rule mapping** — map `(Ku, Pu)` to continuous PID settings `(Kc, Ti, Td)` with an
   integrating-process-aware rule (Tyreus–Luyben default; SIMC optional), then to the code's discrete
   gains `(kp, ki, kd)`.
3. **Model-based gain scaling** — rescale the gains by the known buffering intensity `β(pH)` so the
   loop gain is preserved if the operating setpoint differs from the pH at which the experiment ran.
4. **Split-range gain assignment** — assign the (identical-magnitude) gains to the base and acid
   PIDs, whose opposite action is already handled by the `backwards` flag.

Each stage is derived below.

## 3. Stage 1 — the relay-feedback experiment

### 3.1 Why a relay

A PID autotuner needs one point of the process frequency response: the ultimate gain `Ku` and ultimate
period `Pu` at the phase-crossover frequency where the loop lag is 180°. The relay experiment of
[Åström & Hägglund 1984](https://doi.org/10.1016/0005-1098%2884%2990014-1) obtains both from a single,
bounded, self-starting experiment that needs no prior model, which is why it underlies essentially all
commercial autotuners. Replacing the controller with an on/off relay forces the loop into a limit
cycle at very nearly the ultimate frequency, because the relay naturally seeks the phase-crossover
point.

### 3.2 Realising the relay through the split-range pair

A textbook relay outputs `±d` through one actuator. Here the actuation is one-sided per reagent, so
the relay is realised as a **switch between the two pumps**: while the (delayed, filtered) pH is below
the setpoint the controller commands a fixed **base bolus** `u₊` (mL) each period; once pH rises above
the setpoint it commands a fixed **acid bolus** `u₋` (mL). The signed relay output is
```
u(k) = { +u₊   (command base)   while relay "high"
       { −u₋   (command acid)    while relay "low"
```
This maps directly onto the existing `Dispenser`: a positive demand is sent to the base actuator, a
negative demand's magnitude to the acid actuator. `RelayController` in `relay_autotune.py` implements
exactly this.

### 3.3 Asymmetry — a feature, not a nuisance

The relay is deliberately **asymmetric** (`u₊` may differ from `u₋`). Two reasons, both from the
literature ([Shen, Wu & Yu 1996](https://doi.org/10.1002/aic.690420431);
[Kaya & Atherton 2001](https://doi.org/10.1016/s0959-1524%2899%2900073-6)):

- A live culture imposes a **net metabolic load** (usually acidifying). Under a symmetric relay this
  load biases the cycle so its mean drifts away from the setpoint. Allowing `u₊ ≠ u₋` lets the
  experiment sit centred on the setpoint, so the identified point corresponds to the operating pH.
- The **asymmetry of the settled cycle encodes the static gain and the load**: the ratio of base-on to
  acid-on time reflects the net demand the tuned loop must reject. `static_gain_from_asymmetry`
  returns the mean signed titrant rate at the limit cycle as a proxy for that bias, which the
  specification uses as a feedforward seed and a sanity check.

### 3.4 Hysteresis for noise rejection

A relay that switches whenever pH crosses the setpoint will chatter on measurement noise. Following
[Åström & Hägglund 1984](https://doi.org/10.1016/0005-1098%2884%2990014-1), a **hysteresis band** of
half-width `ε` is used: the relay switches to "low" only when `pH > SP + ε` and back to "high" only
when `pH < SP − ε`. `ε` is set to a few multiples of the observed probe noise standard deviation
(default 0.02 pH, i.e. ~4σ for a 0.005 pH probe). The band both suppresses spurious switches and
enters the `Ku` estimate (below).

### 3.5 Identification of Ku and Pu

The experiment runs until a steady limit cycle is established (a configurable number of settling
cycles are discarded). Then:

- **Ultimate period** `Pu` is the mean time between successive full up→down→up cycles (every second
  relay switch), averaged over the settled cycles.
- **Amplitude** `a` is the zero-to-peak pH amplitude about the cycle mean, taken from the averaged
  local peaks and troughs of the settled window (robust to the asymmetric mean).
- **Ultimate gain** `Ku` follows from the **asymmetric relay describing function with hysteresis
  correction**:
  ```
              2 (u₊ + u₋)
    Ku  =  ───────────────────────                                          (M1)
            π · sqrt(a² − ε²)
  ```
  The numerator `(u₊ + u₋)` is the total relay swing (reducing to `2d` for a symmetric `±d` relay, so
  M1 reduces to the classical `Ku = 4d/(π·a)`); the `sqrt(a²−ε²)` term is the standard describing-
  function correction for a relay with hysteresis band `ε`
  ([Åström & Hägglund 1984](https://doi.org/10.1016/0005-1098%2884%2990014-1)). Because the relay
  output here is a **volume per period**, `Ku` carries units of **mL/pH** — exactly the units of the
  code's `kp` (volume demand per pH of error), so the mapping in §4 needs no unit conversion.

The describing function keeps only the fundamental harmonic and is therefore approximate; its error is
the dominant identification uncertainty and is one reason the conservative tuning rules of §4 are
preferred. Where higher accuracy is wanted, the relay waveform can instead be fitted to a FOPDT/
integrating model ([Wang, Hang & Zou 1997](https://doi.org/10.1021/ie960412%2B)); the SIMC path in §4
uses a lightweight version of this.

The demonstration run against the phosphate plant (metabolic load `r = 2·10⁻⁷` mol L⁻¹ s⁻¹, probe
noise 0.005 pH) gives a clean bounded cycle centred exactly on pH 7.000 with `Ku = 18.6 mL/pH` and
`Pu = 293 s`:

![Relay-feedback experiment against the 14 mM phosphate plant at pH 7. Top: pH executes a bounded limit cycle within the hysteresis band around the setpoint. Bottom: the signed relay output switching between base (+) and acid (-) boluses.]({{artifact:5562528d-27c3-4c83-81c2-aeb02ced4ab2}})

*Figure 1. Relay experiment: a bounded, self-limiting oscillation from which Ku and Pu are read. The
oscillation amplitude is set by the relay bolus size, not by an unstable growth, so the experiment is
safe to run on a live vessel.*

## 4. Stage 2 — mapping (Ku, Pu) to the code's gains

### 4.1 Continuous tuning rules

Given `(Ku, Pu)`, the continuous parallel-PID settings `(Kc, Ti, Td)` follow from a rule set. Three
are provided (`tuning_rules`, `simc_pid`):

| Rule | Kc | Ti | Td | Note |
|---|---|---|---|---|
| Ziegler–Nichols PID | 0.6·Ku | 0.5·Pu | 0.125·Pu | aggressive; poor robustness on this class |
| **Tyreus–Luyben PI** | Ku/3.2 | 2.2·Pu | 0 | **default**; robust for integrating+dead-time |
| Tyreus–Luyben PID | Ku/2.2 | 2.2·Pu | Pu/6.3 | adds derivative |
| SIMC | 1/(k′(τc+θ)) | 4(τc+θ) | 0 | model-based; τc tunes speed/robustness |

The **default is Tyreus–Luyben PI** ([Tyreus & Luyben 1992](https://doi.org/10.1021/ie00011a029)):
the pH plant is an integrating process, TL was derived precisely for integrator/dead-time dynamics, and
it gives a markedly less oscillatory, more robust response than Z-N — whose robustness on such
processes is known to be poor ([Hägglund & Åström 2002](https://doi.org/10.1111/j.1934-6093.2002.tb00076.x)).
Derivative action is left off by default because probe noise makes it costly and the integrating plant
does not need it; the TL-PID row is available where a faster response is required and the measurement
is well filtered.

### 4.2 The SIMC option and its model inference

SIMC ([Skogestad 2003](https://doi.org/10.1016/s0959-1524%2802%2900062-8)) is offered for operators
who want a single, physical speed/robustness knob `τc`. It needs a model, which is inferred from the
same relay point (`simc_pid`): treating the plant as integrating-plus-dead-time `g(s)=k′e^{−θs}/s`, at
the ultimate frequency `ωu = 2π/Pu` the integrator contributes π/2 of the 180° phase lag, leaving
`θ = Pu/4`; the integrating slope is `k′ = ωu/Ku` from `|g(jωu)| = 1/Ku`. SIMC then gives
`Kc = 1/(k′(τc+θ))`, `Ti = 4(τc+θ)`, with default `τc = θ` (moderate, robust). Increasing `τc` slows
and stiffens the loop; decreasing it speeds and softens robustness.

### 4.3 Mapping to the code's discrete gains

`reactors_czlab.core.control._PidControl.get_value` implements a positional parallel PID:
```
error    = direction · (setpoint − measurement)          direction = −1 if backwards else +1
p_term   = kp · error
i_sum   ← clamp( i_sum + ki · error · Δt ,  min_integral, max_integral )      (anti-windup band)
d_term   = −direction · kd · (measurement − last_measurement) / Δt           (derivative on measurement)
output   = clamp( p_term + i_sum + d_term ,  min_val, max_val )
```
This is the textbook parallel form with derivative-on-measurement and a clamped integrator. Matching it
term-by-term to the continuous law `u = Kc·e + (Kc/Ti)∫e dt + Kc·Td·de/dt` gives the mapping
(`to_code_gains`):
```
    kp = Kc ,     ki = Kc / Ti ,     kd = Kc · Td .                          (M2)
```
Units check: `error` is in pH, `output` in mL, so `kp` is mL/pH (= units of `Ku`), `ki` is
mL·pH⁻¹·s⁻¹, `kd` is mL·s·pH⁻¹. Because `Ti, Td, Pu` are all in **seconds** and the code integrates
with the real elapsed `Δt` in seconds, no per-period rescaling is needed — the mapping is exact for the
code as written. A PI rule (Td = 0) yields `kd = 0`.

The demonstration `(Ku, Pu) = (18.6 mL/pH, 293 s)` maps to (code units):

| Rule | kp [mL/pH] | ki [mL/pH/s] | kd [mL·s/pH] |
|---|---|---|---|
| ZN-PID | 11.19 | 0.076 | 410.2 |
| **TL-PI** | **5.83** | **0.0090** | **0** |
| TL-PID | 8.48 | 0.013 | 394.6 |
| SIMC | 5.94 | 0.010 | 0 |

## 5. Stage 3 — model-based gain scaling across the titration curve

The relay experiment characterises the loop at *one* pH. Because `Kp ∝ 1/β(pH)` (process model eq. 8),
gains tuned at `pH_tuned` must be rescaled to hold the loop gain `Kc·Kp` constant if the operating
setpoint is `pH_target`. Since `Kc·Kp` should be invariant and `Kp ∝ 1/β`,
```
    s = Kp(pH_tuned) / Kp(pH_target) = β(pH_target) / β(pH_tuned) ,          (M3)
    (kp, ki, kd)  ←  s · (kp, ki, kd) .
```
All three gains scale by the same factor `s` because the whole controller output scales with `Kc`
(`scale_gains_to_setpoint`). `β(pH)` is computed from the phosphate speciation (process model eq. 7),
so no extra experiment is needed. This is the practical form of the titration-curve linearisation of
[Gustafsson & Waller 1992](https://doi.org/10.1021/ie00012a009) and
[Kalafatis, Wang & Cluett 2005](https://doi.org/10.1016/j.jprocont.2004.03.006): rather than invert the
whole nonlinearity online, one measures the gain at the working point and uses the known local slope to
extend it. In the well-buffered plateau `β` is nearly flat, so `s ≈ 1` for setpoints within ±0.5 pH of
the tuning point and the scaling is a small correction; it becomes important only for large setpoint
moves toward the buffer edges.

## 6. Stage 4 — split-range gain assignment and actuation constraints

The identified gains are assigned to **both** PIDs with equal magnitude; their opposite sense is
already produced by the `backwards` flag (the acid loop flips the error and derivative sign), so no
sign change is applied to the gains themselves. Two split-range details:

- **Dead band / overlap.** A small symmetric dead band around the setpoint (no dosing within ±δ pH)
  prevents the two loops from fighting and dosing against each other; δ is set at or just below the
  relay hysteresis `ε`. This mirrors the one-sided clamping the PID already performs (each loop clamps
  to `min_val` on the wrong side of the setpoint).
- **Bolus quantisation and minimum volume.** The `Dispenser` delivers discrete boluses with a minimum
  dispensable volume. A demand below that minimum should be accumulated (or dropped) rather than
  issued, otherwise the loop limit-cycles on quantisation. The autotuner's integral band
  (`min_integral/max_integral`, auto-derived from the output range) already bounds windup; the
  specification adds a minimum-volume guard.

## 7. Practical safeguards

- **Noise filter.** In addition to the relay hysteresis, the tuned loop should filter the measurement
  feeding the derivative term; a first-order filter with time constant a few × Δt is recommended,
  sized per [Segovia, Hägglund & Åström 2014](https://doi.org/10.1016/j.conengprac.2014.07.005). With
  the default TL-PI (kd = 0) this is only needed if a PID rule is selected.
- **Bounded experiment.** The relay amplitude sets the pH excursion, so the experiment is intrinsically
  bounded; the specification additionally imposes pH safety limits, a maximum titrant budget, and a
  timeout that abort the experiment and restore the prior gains.
- **Fallback without oscillation.** Where inducing any limit cycle on a production vessel is
  unacceptable, the setpoint-overshoot method of
  [Shamsuzzoha & Skogestad 2010](https://doi.org/10.1016/j.jprocont.2010.08.003) tunes from a single
  closed-loop setpoint step; it is noted as an alternative in the specification but is not the default
  because it extracts less information per unit of disruption.

## 8. Summary

The method is: **an asymmetric, hysteretic relay experiment realised through the existing split-range
acid/base pumps** (§3) → **`(Ku, Pu)` mapped by Tyreus–Luyben (default) or SIMC to the code's
`(kp, ki, kd)`** with the exact unit-preserving mapping M2 (§4) → **gains scaled by the known
phosphate buffering intensity** to cover the operating band (§5) → **assigned to both split-range
PIDs** with a dead band and bolus-quantisation guard (§6), under pH/volume/timeout safeguards (§7). It
uses only quantities the system already has (the split-range pumps, the flow calibration, the pH probe)
plus the analytic buffer model, and every stage is exercised by the in-silico studies.

## References

- Åström, K. J. & Hägglund, T. (1984). Automatic tuning of simple regulators with specifications on phase and amplitude margins. *Automatica* 20(5), 645–651. https://doi.org/10.1016/0005-1098(84)90014-1
- Gustafsson, T. K. & Waller, K. V. (1992). Nonlinear and adaptive control of pH. *Ind. Eng. Chem. Res.* 31(12), 2681–2693. https://doi.org/10.1021/ie00012a009
- Hägglund, T. & Åström, K. J. (2002). Revisiting the Ziegler–Nichols tuning rules for PI control. *Asian J. Control* 4(4), 364–380. https://doi.org/10.1111/j.1934-6093.2002.tb00076.x
- Kalafatis, A. D., Wang, L. & Cluett, W. R. (2005). Linearizing feedforward–feedback control of pH processes based on the Wiener model. *J. Process Control* 15(1), 103–112. https://doi.org/10.1016/j.jprocont.2004.03.006
- Kaya, İ. & Atherton, D. P. (2001). Parameter estimation from relay autotuning with asymmetric limit cycle data. *J. Process Control* 11(4), 429–439. https://doi.org/10.1016/s0959-1524(99)00073-6
- Segovia, V. R., Hägglund, T. & Åström, K. J. (2014). Measurement noise filtering for common PID tuning rules. *Control Eng. Pract.* 32, 43–63. https://doi.org/10.1016/j.conengprac.2014.07.005
- Shamsuzzoha, M. & Skogestad, S. (2010). The setpoint overshoot method: a simple and fast closed-loop approach for PID tuning. *J. Process Control* 20(10), 1220–1234. https://doi.org/10.1016/j.jprocont.2010.08.003
- Shen, S.-H., Wu, J.-S. & Yu, C.-C. (1996). Use of biased-relay feedback for system identification. *AIChE J.* 42(4), 1174–1180. https://doi.org/10.1002/aic.690420431
- Skogestad, S. (2003). Simple analytic rules for model reduction and PID controller tuning. *J. Process Control* 13(4), 291–309. https://doi.org/10.1016/s0959-1524(02)00062-8
- Tyreus, B. D. & Luyben, W. L. (1992). Tuning PI controllers for integrator/dead time processes. *Ind. Eng. Chem. Res.* 31(11), 2625–2628. https://doi.org/10.1021/ie00011a029
- Wang, Q.-G., Hang, C. C. & Zou, B. (1997). Low-order modeling from relay feedback. *Ind. Eng. Chem. Res.* 36(2), 375–381. https://doi.org/10.1021/ie960412+
- Ziegler, J. G. & Nichols, N. B. (1942). Optimum settings for automatic controllers. *Trans. ASME* 64, 759–768. https://doi.org/10.1115/1.4019269
