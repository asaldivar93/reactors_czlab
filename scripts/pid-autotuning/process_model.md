# Process model: pH dynamics of a phosphate-buffered bioreactor under NaOH/HCl titration

This document derives, from first principles, the simplest process model of pH that is faithful
enough to design and test the autocalibration method against. It is built on the reaction-invariant
(charge-balance) formulation of [Gustafsson & Waller 1983](https://doi.org/10.1016/0009-2509%2883%2980157-2)
and the CSTR pH treatment of [McAvoy, Hsu & Lowenthal 1972](https://doi.org/10.1021/i260041a013). The
implementation is `scripts/ph_process_model.py`; every equation below corresponds to a function in
that file, and the closing section reports the numerical validation.

## 1. System and modeling assumptions

The vessel is a well-mixed batch reactor of working volume `V` (default 5 L). The medium contains
total phosphate `C_P = 14 mM` as the only buffering species of interest. pH is manipulated by two
metering pumps: NaOH (a strong base, molarity `c_b`, default 0.5 M) and HCl (a strong acid, molarity
`c_a`, default 0.5 M). A growing culture imposes a net metabolic acid/base load `r(t)`
(mol L⁻¹ s⁻¹, positive = acidifying). We assume:

1. **Instantaneous, well-mixed acid–base equilibria.** Proton-transfer reactions are orders of
   magnitude faster than the 10 s control period, so at every instant the solution is at chemical
   equilibrium. This is the standard assumption that makes the titration curve a *static* map.
2. **Isothermal, 25 °C.** The equilibrium constants are taken at 25 °C; a temperature correction is
   discussed in §7 but not required for the control design.
3. **Dilute-solution activities.** Concentrations are used in place of activities (activity
   coefficients ≈ 1). For the modest ionic strength of a phosphate medium this shifts the effective
   pKa values by ≈0.1–0.2 units, which is absorbed into the model uncertainty the autotuner is
   designed to tolerate (§7, and the robustness sweep in the validation studies).
4. **Strong titrants dissociate completely.** Each mole of NaOH contributes one mole of Na⁺; each
   mole of HCl contributes one mole of Cl⁻.

## 2. Reaction invariants

The key modeling move is to describe the state not by the concentration of every species (which the
fast equilibria couple nonlinearly) but by quantities the equilibria leave unchanged. For this system
there are two such **reaction invariants**:

- the **strong-ion difference** (net strong-ion charge), which for Na⁺/Cl⁻ is
  ```
  Z = [Na+] - [Cl-]                                                        (1)
  ```
- the **total phosphate**,
  ```
  C_P = [H3PO4] + [H2PO4-] + [HPO4^2-] + [PO4^3-].                          (2)
  ```

Proton transfer moves phosphate between its four forms and moves H⁺/OH⁻, but it changes neither `Z`
(strong ions do not participate in acid–base reactions) nor `C_P` (phosphate is conserved). `Z` and
`C_P` therefore obey simple *linear* mole balances, and all of the chemical nonlinearity is pushed
into a single algebraic equation relating them to `[H⁺]`. This is exactly the separation
[Gustafsson & Waller 1983](https://doi.org/10.1016/0009-2509%2883%2980157-2) exploit, and it is what
makes the pH loop a Wiener system — a linear dynamic block feeding a static output nonlinearity
([Kalafatis, Wang & Cluett 2005](https://doi.org/10.1016/j.jprocont.2004.03.006)).

## 3. Phosphate speciation (the static chemistry)

Phosphoric acid dissociates in three steps with acid constants `K1, K2, K3`:
```
H3PO4  <-> H+ + H2PO4-     K1 = [H+][H2PO4-]/[H3PO4]      pKa1 = 2.15
H2PO4- <-> H+ + HPO4^2-    K2 = [H+][HPO4^2-]/[H2PO4-]    pKa2 = 7.20
HPO4^2- <-> H+ + PO4^3-    K3 = [H+][PO4^3-]/[HPO4^2-]    pKa3 = 12.35
```
(thermodynamic values at 25 °C; CRC Handbook). Writing `h ≡ [H⁺]`, the equilibrium ratios give the
four **fractional abundances** `α_j = [species j]/C_P`:
```
D(h) = h^3 + K1 h^2 + K1 K2 h + K1 K2 K3
alpha0 = h^3 / D        (H3PO4)
alpha1 = K1 h^2 / D     (H2PO4-)                                            (3)
alpha2 = K1 K2 h / D    (HPO4^2-)
alpha3 = K1 K2 K3 / D   (PO4^3-)
```
The **mean phosphate charge** (charge carried per phosphate, taken positive as negative charge) is
```
n̄(h) = alpha1 + 2 alpha2 + 3 alpha3.                                       (4)
```
so the phosphate contribution to the charge balance is `-C_P · n̄(h)`. These are `Chemistry.alphas`
and `Chemistry.nbar` in the code.

## 4. Electroneutrality — the map (Z, C_P) → pH

The solution must be electrically neutral. Summing all charges (strong ions, water ions, phosphate):
```
[Na+] + [H+] = [Cl-] + [OH-] + [H2PO4-] + 2[HPO4^2-] + 3[PO4^3-]
```
Substituting `Z = [Na+] − [Cl-]`, `[OH⁻] = Kw/h`, and (4):
```
    Z + h - Kw/h - C_P * n̄(h) = 0.                     (electroneutrality)  (5)
```
This is the single nonlinear equation of the model. Given the invariants `(Z, C_P)`, it is solved for
`h` and hence `pH = −log₁₀ h`. The left side `F(h)` is **strictly decreasing** in `h` on
`(0, ∞)` (each term is monotone), so (5) has a unique root and a bracketed Brent solve on
`h ∈ [10⁻¹⁴, 1]` mol L⁻¹ converges unconditionally — this is `ph_from_state`. The inverse map has a
**closed form**: solving (5) for `Z`,
```
    Z(pH) = Kw/h - h + C_P * n̄(h),        h = 10^(-pH)                      (6)
```
which is `state_from_ph`. Equation (6) is the analytic titration curve — the strong-ion difference
(equivalently, the net strong base added) required to hold any given pH.

## 5. Buffering intensity and the process static gain

Differentiating the titration curve (6) gives the **buffering intensity** (buffer capacity)
`β = dZ/dpH`, the amount of strong base per unit pH change. Using `dh/dpH = −ln(10)·h`:
```
    β(pH) = ln(10) * [ h + Kw/h + C_P * ( - h * dn̄/dh ) ]                   (7)
```
The three bracketed terms are the strong-acid (`h`), water (`Kw/h`) and phosphate contributions; the
phosphate term is largest near a pKa. `β > 0` everywhere and is **maximal at pKa₂ ≈ 7.2**, where the
titration curve is flattest. The analytic `dn̄/dh` is implemented in `Chemistry.dnbar_dh` and used by
`buffering_intensity`. This is the same buffer-factor bookkeeping used in aquatic chemistry
([Middelburg, Soetaert & Hagens 2020](https://doi.org/10.1029/2019rg000681)).

The quantity the controller actually sees is the **process static gain** — how far the pH moves per
unit volume of titrant added. Adding `dV` of a base of molarity `c_b` to a volume `V` raises `Z` by
`dZ = c_b·dV/V` (small addition, dilution second-order), and `dpH = dZ/β`, so
```
    Kp(pH) = dpH/dV = c_b / ( V * β(pH) )      [pH per litre of titrant]     (8)
```
which is `static_gain`. The acid pump has the same magnitude with the opposite sign. Equation (8) is
the heart of the control problem and of the autotuner design: **the gain is inversely proportional to
the buffer capacity**, so it is *smallest* on the well-buffered plateau around pH 7 and rises sharply
toward the buffer edges. The autotuner measures `Kp` at the operating setpoint via the relay
experiment; equation (8) with the known `β(pH)` then lets that single measurement be rescaled to any
other setpoint (the model-based gain scaling in the method document).

## 6. Dynamic model — invariant balances

Because `Z` and `C_P` are reaction invariants, their dynamics are ordinary linear mole balances. For
the batch vessel with base flow `q_b`, acid flow `q_a` (both L s⁻¹), an outflow `q_out`, and a
metabolic load `r(t)`:
```
d(V·Z)/dt   = c_b q_b  -  c_a q_a  -  r(t)·V                                 (9a)
d(V·C_P)/dt = - C_P q_out            (phosphate leaves only by outflow)      (9b)
dV/dt       = q_b + q_a - q_out                                              (9c)
```
Tracking the *extensive* invariants `N_Z = V·Z` and `N_P = V·C_P` keeps the balances exactly linear
even as `V` changes; the intensive `Z`, `C_P` are recovered by dividing by `V`, and pH follows from
(5). `PhPlant.step` integrates (9) over one control period with an exact first-order (constant-rate)
update. In a pure batch (`q_out = 0`) `N_P` is constant and only `N_Z` moves.

Two dynamic features matter for control and are represented in the closed-loop simulator rather than
here:
- **Titrant transport / mixing dead-time.** A finite lag exists between commanding a pump and the
  reagent being fully mixed and sensed. It is modeled as a pure dead time `θ` (default one control
  period) plus the first-order dispensing behaviour of the pump.
- **The integrating character.** With `q_out = 0`, equation (9a) shows that a constant net titrant
  flow accumulates `N_Z` linearly — the loop is an **integrator** in the invariant, filtered through
  the static gain (8). This is why the integrating-process tuning rules (Tyreus–Luyben, SIMC) are the
  right choice, as argued in the literature review.

## 7. Parameter values, uncertainty and temperature

| Symbol | Meaning | Default | Source / note |
|---|---|---|---|
| pKa₁, pKa₂, pKa₃ | phosphoric-acid constants (25 °C) | 2.15, 7.20, 12.35 | CRC Handbook |
| pKw | water autoionisation (25 °C) | 14.00 | standard |
| C_P | total phosphate | 14 mM | given (medium) |
| V | working volume | 5 L | `REACTOR_VOLUME` in `run_server.py` |
| c_b, c_a | NaOH, HCl molarity | 0.5 M | typical bench reagent; swept in validation |
| Δt | control period | 10 s | `SAMPLE_PERIOD` in `run_server.py` |

The dominant model uncertainties are (i) the effective pKa shift from ionic strength (≈0.1–0.2 pH
units in a real medium — activity coefficients set to 1 here), (ii) additional buffering species in a
real growth medium (amino acids, CO₂/bicarbonate, cell metabolites) that add to `β` and were
deliberately omitted to keep the model minimal, and (iii) titrant molarity tolerance. All three shift
the *magnitude* of `β` and hence `Kp` but not the qualitative shape, which is exactly the class of
uncertainty a relay-based autotuner is designed to absorb: it measures the actual `Kp` in situ. A
temperature correction, if wanted, enters through the van 't Hoff dependence of `K2` and `Kw`; it is
not needed for the control design because the autotuner re-measures the gain at the operating
temperature.

## 8. Numerical validation

`python scripts/ph_process_model.py` reproduces the two figures below and reports:

- **Solver vs analytic titration curve:** the forward electroneutrality solve `ph_from_state`
  recovers the pH from `Z` computed by the closed-form inverse (6) to a maximum error of
  **1.9 × 10⁻⁸ pH units** across pH 2–12 — the two independent routes agree to solver tolerance.
- **Gain range:** `|Kp|` varies by **131×** across pH 2–12, confirming the textbook pH nonlinearity
  ([McAvoy et al. 1972](https://doi.org/10.1021/i260041a013)).
- **At the pH 7.0 operating point:** `β = 7.66 mmol L⁻¹ pH⁻¹` and `Kp = 13.1 pH L⁻¹` for 0.5 M base
  in 5 L — the well-buffered minimum-gain region, favourable for fixed-gain PID.

![Titration curve of 14 mM phosphate and phosphate speciation. Red points are the forward solver evaluated at base additions computed from the analytic inverse; they lie on the analytic curve to 1.9e-8 pH.]({{artifact:8372f8ad-6091-42d2-a376-e29227d43d6e}})

*Figure 1. Left: titration of 14 mM phosphate in 5 L with 0.5 M NaOH; the plateau at pKa₂ ≈ 7.2 is
the bioreactor operating band. Right: phosphate speciation — the HPO₄²⁻/H₂PO₄⁻ pair dominates around
neutral pH.*

![Buffering intensity and process static gain versus pH. Buffer capacity peaks at pKa2; the static gain is correspondingly smallest there and rises steeply toward the buffer edges.]({{artifact:ed32d88c-104c-4733-987a-cb15e48fb0a8}})

*Figure 2. Left: buffering intensity β(pH), maximal at pKa₂. Right: process static gain |Kp| (log
scale) — minimal on the buffered plateau, ~130× larger near the equivalence regions. The autotuner
probes Kp at the setpoint and rescales it with the known β(pH).*

## 9. Summary of the model interface used downstream

The closed-loop simulator and the autotuner use the model through this small interface (all in
`scripts/ph_process_model.py`):

- `Chemistry` — pKa/Kw and phosphate speciation (`alphas`, `nbar`, `dnbar_dh`).
- `ph_from_state(Z, C_P, chem)` — forward map (5), the "measurement".
- `state_from_ph(pH, C_P, chem)` — inverse map (6), for setting initial conditions.
- `buffering_intensity(pH, C_P, chem)` — β from (7), for gain scaling.
- `static_gain(pH, C_P, V, c, chem)` — Kp from (8), the quantity the relay estimates.
- `PhPlant` — the dynamic vessel integrating balances (9), the "plant" the controller acts on.

## References

- Gustafsson, T. K. & Waller, K. V. (1983). Dynamic modeling and reaction invariant control of pH. *Chem. Eng. Sci.* 38(3), 389–398. https://doi.org/10.1016/0009-2509(83)80157-2
- Kalafatis, A. D., Wang, L. & Cluett, W. R. (2005). Linearizing feedforward–feedback control of pH processes based on the Wiener model. *J. Process Control* 15(1), 103–112. https://doi.org/10.1016/j.jprocont.2004.03.006
- McAvoy, T. J., Hsu, E. & Lowenthal, S. (1972). Dynamics of pH in controlled stirred tank reactor. *Ind. Eng. Chem. Process Des. Dev.* 11(1), 68–70. https://doi.org/10.1021/i260041a013
- Middelburg, J. J., Soetaert, K. & Hagens, M. (2020). Ocean alkalinity, buffering and biogeochemical processes. *Rev. Geophys.* 58(3). https://doi.org/10.1029/2019rg000681
