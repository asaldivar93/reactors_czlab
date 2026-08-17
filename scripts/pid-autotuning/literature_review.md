# Literature review: PID autotuning and pH process modeling for a buffered bioreactor

This review supports the design of a PID *autocalibration* (autotuning) feature for the
`reactors_czlab` bioreactor interface, where pH is regulated by metered additions of NaOH and
HCl and temperature by heaters and coolers. Two bodies of work bear on that design. The first is
the automatic tuning of PID controllers — how a controller can find its own gains from a short,
automated experiment on the plant. The second is the process control of pH — a loop whose static
gain varies by orders of magnitude along the titration curve and which, in a buffered growth
medium, is dominated by the buffer chemistry. The practical conclusion drawn here, and carried
into the method and specification documents, is that a **relay-feedback experiment** supplies the
process information an autotuner needs with the least equipment and operator burden, but that on a
pH loop it must be paired with a **model of the titration curve** so the single operating point it
probes can be extrapolated across the nonlinear gain. Both halves are developed below.

## Automatic tuning of PID controllers

### From continuous cycling to the relay experiment

The tuning problem PID autotuners solve was posed in its enduring form by
[Ziegler & Nichols 1942](https://doi.org/10.1115/1.4019269): drive the loop to the edge of
stability under proportional-only control, read off the ultimate gain `Ku` (the proportional gain
at which the loop oscillates with constant amplitude) and the ultimate period `Pu`, and set the
PID gains from a small table of multipliers on those two numbers. The rules are still the
reference against which tuning methods are measured, but the continuous-cycling experiment that
feeds them is hostile to automation: it requires pushing a real plant to sustained oscillation by
trial and error, which is slow and risks a genuinely unstable excursion. Modern reappraisals show
that the resulting settings are also aggressive — [Hägglund & Åström 2002](https://doi.org/10.1111/j.1934-6093.2002.tb00076.x)
document that the classical Z-N settings give poor robustness margins on many common process
dynamics and propose robustness-constrained replacements — so the value of Z-N today is as the
`(Ku, Pu)` *parameterisation*, not as the experiment or the exact multipliers.

The decisive step toward automation was the relay-feedback experiment of
[Åström & Hägglund 1984](https://doi.org/10.1016/0005-1098%2884%2990014-1). Replacing the
proportional controller with an on/off relay of amplitude `d` forces the loop into a stable limit
cycle at very nearly the ultimate frequency, because the relay's switching naturally seeks the
phase-crossover point where the loop phase is −180°. The ultimate period is read directly as the
oscillation period, and the ultimate gain follows from the describing-function approximation of
the relay, `Ku ≈ 4d / (π·a)`, where `a` is the measured amplitude of the process-output
oscillation. This delivers exactly the pair Z-N needs, but from an experiment that is bounded (the
oscillation amplitude is set by the relay amplitude, not by an unstable growth), self-starting, and
requires no prior process model — the properties that made it the basis of essentially every
commercial autotuner since. A recent field-wide perspective ([Hägglund 2024](https://doi.org/10.1016/j.ifacol.2024.08.018))
confirms that relay autotuning remains the workhorse of industrial PID commissioning.

The describing-function estimate of `Ku` is an approximation — it treats the relay as if it passed
only the fundamental harmonic — and its error is the main limitation of the basic method. Two
lines of work address this. The first improves the *identification*: rather than reading `Ku, Pu`
alone, one fits a low-order transfer function to the relay-induced waveform.
[Chang, Shen & Yu 1992](https://doi.org/10.1021/ie00003a030) derive a transfer function directly
from the relay response, [Li, Eskinat & Luyben 1991](https://doi.org/10.1021/ie00055a019) sharpen
the identification accuracy, and [Wang, Hang & Zou 1997](https://doi.org/10.1021/ie960412%2B) show
that a single relay test can yield a full first- or second-order-plus-dead-time (FOPDT/SOPDT)
model. A fitted model is more informative than a point on the frequency response and lets one apply
model-based tuning rules (below) rather than table lookups. The second line reduces the
describing-function error at the source: [Sung, Park & Lee 1995](https://doi.org/10.1021/ie00038a059)
introduce a modified two-part relay that improves the estimate, and
[Tan, Lee & Wang 1996](https://doi.org/10.1002/aic.690420916) fold several such refinements into a
practical enhanced procedure.

### Asymmetric and biased relays — the feature a one-sided pH loop needs

A symmetric relay produces a symmetric limit cycle and returns `Ku` and `Pu` but says nothing about
the process static gain or about any load bias. For the pH application both omissions matter, and
the relevant literature is the asymmetric/biased relay. [Shen, Wu & Yu 1996](https://doi.org/10.1002/aic.690420431)
show that deliberately biasing the relay — unequal ON times or unequal up/down amplitudes — makes
the limit cycle asymmetric in a way that encodes the steady-state gain, so a single experiment
returns `Ku`, `Pu` *and* the static gain. [Kaya & Atherton 2001](https://doi.org/10.1016/s0959-1524%2899%2900073-6)
develop the parameter estimation from asymmetric limit-cycle data in full and show it is also the
natural way to identify a process that is running against a steady load disturbance, because the
asymmetry the load imposes is precisely what the method reads. [Sánchez, Visioli & Vilanova 2021](https://doi.org/10.3390/app11041651)
carry this to a short asymmetric-relay experiment that fits a generic process model quickly, which
is attractive when the experiment ties up a reactor. This family is directly relevant here for two
reasons developed in the method document: the pH plant's actuation is *one-sided per reagent* (the
base pump can only add base, the acid pump only acid), so the relay must be realised as a switch
*between* the two pumps around the setpoint rather than a symmetric ± signal through one actuator;
and the process almost always sits against a net metabolic load (a growing culture that acidifies
or alkalises), which biases the cycle whether or not the relay is deliberately biased.

### Tuning rules: mapping the experiment to gains

Given `(Ku, Pu)` or a fitted FOPDT model, several rule sets map to the PID gains, and the choice is
a robustness/performance trade-off rather than a right answer. The classical
[Ziegler & Nichols 1942](https://doi.org/10.1115/1.4019269) rules (`Kc = 0.6·Ku`, `Ti = Pu/2`,
`Td = Pu/8` for a PID) are the most aggressive and the least robust. The
[Tyreus & Luyben 1992](https://doi.org/10.1021/ie00011a029) rules (`Kc = Ku/3.2`, `Ti = 2.2·Pu`
for a PI) were developed specifically for the sluggish, integrator-plus-dead-time dynamics common
in chemical process units and give a markedly less oscillatory, more robust response; the same
authors' later treatment ([Luyben 2000](https://doi.org/10.1021/ie9906114)) extends the reasoning
to processes with awkward dynamics. Because a titrant-fed pH vessel behaves as an integrator in the
controlled variable (added reagent accumulates as a shift in the charge balance; see the modeling
section), the Tyreus–Luyben settings are a better default here than Z-N.

The model-based alternative is IMC/SIMC tuning. [Skogestad 2003](https://doi.org/10.1016/s0959-1524%2802%2900062-8)
gives simple analytic rules — the SIMC rules — that map a FOPDT or integrating-plus-dead-time model
to PID gains through a single tuning parameter, the desired closed-loop time constant `τc`, and the
"half rule" for reducing higher-order models to that form. The updated treatment
([Skogestad & Grimholt 2012](https://doi.org/10.1007/978-1-4471-2425-2_5)) makes the
speed–robustness trade-off explicit through `τc` and is well suited to the integrating pH dynamics.
The attraction of SIMC over table lookups is that `τc` is a single, physically meaningful knob an
operator can turn toward "faster" or "gentler", which is valuable when the same autotuner must
serve reactors running different media. The `(Ku, Pu)` route and the FOPDT-model route are not
exclusive — a relay experiment can be read both ways, and the method document uses the
`(Ku, Pu)` → Tyreus–Luyben map as the default with a SIMC path as an option.

### Practical concerns: noise, and avoiding sustained oscillation

Two practical issues shape a deployable autotuner. Measurement noise corrupts both the switching of
a relay (spurious switches when the signal crosses the relay threshold on noise rather than
dynamics) and the tuned loop's derivative action. The standard remedy on the relay is a hysteresis
band whose width is set a few multiples of the noise amplitude, which the original
[Åström & Hägglund 1984](https://doi.org/10.1016/0005-1098%2884%2990014-1) method already includes,
and on the controller a filter on the measurement; [Segovia, Hägglund & Åström 2014](https://doi.org/10.1016/j.conengprac.2014.07.005)
give a systematic treatment of measurement-noise filtering matched to the common tuning rules,
which is directly applicable because bioreactor pH probes are noticeably noisy. Separately, some
users prefer not to induce *any* sustained oscillation on a production vessel. The setpoint-overshoot
method of [Shamsuzzoha & Skogestad 2010](https://doi.org/10.1016/j.jprocont.2010.08.003) tunes from
a single closed-loop setpoint step — reading the first overshoot and the time to the first peak —
without driving the loop to a limit cycle, and is a reasonable fallback where a relay experiment is
judged too disruptive. Continuous-cycling itself has also been modernised for automation
([Kim, Lee & Sung 2021](https://doi.org/10.3390/pr9030509)). For the present system the relay
experiment remains the primary recommendation because it is the most information-rich per unit of
disruption, but the setpoint-overshoot method is noted in the specification as an alternative when
the relay's oscillation is unacceptable.

## Process control and modeling of pH

### Why pH is the hard loop

pH control has been the canonical difficult loop in process control since
[McAvoy, Hsu & Lowenthal 1972](https://doi.org/10.1021/i260041a013) wrote down the dynamics of pH
in a continuous stirred-tank reactor from the underlying charge and mass balances and showed that
the apparent process gain — how much the pH moves per unit of reagent added — varies by several
orders of magnitude along the titration curve. Near an equivalence point the curve is nearly
vertical and a tiny reagent addition swings the pH violently; on a buffered plateau the same
addition barely moves it. A single fixed set of PID gains therefore cannot be right everywhere: gains
that are stable near the steep region are hopelessly sluggish on the plateau, and gains that are
brisk on the plateau drive a limit cycle near the equivalence point. This is the central fact the
autotuner must accommodate, and it is why a relay experiment *alone* — which characterises the loop
at one operating point — is insufficient without a model to extrapolate the gain.

### The reaction-invariant formulation

The modeling framework that made pH control tractable is the reaction-invariant description of
[Gustafsson & Waller 1983](https://doi.org/10.1016/0009-2509%2883%2980157-2). Its insight is to
choose state variables that the fast acid–base equilibria leave unchanged: for a system of strong
and weak acids and bases, the *strong-ion difference* (net strong-base minus strong-acid charge)
and the *total concentration of each buffering species* are conserved by proton-transfer reactions
and change only through inflows, outflows and reagent additions. These invariants obey simple linear
balances; all of the nonlinearity is confined to a single algebraic equation — electroneutrality —
that maps the invariants to `[H⁺]` and hence to pH at each instant. This cleanly separates a linear
dynamic part (how reagent additions move the invariants) from a static nonlinear part (how the
invariants determine pH), and it is the formulation adopted in the companion process-model document,
with total phosphate and the strong-ion difference from NaOH/HCl as the invariants. The same
charge-balance/alkalinity bookkeeping is standard in aquatic chemistry, where the buffer factor and
buffering intensity are derived from exactly this invariant ([Middelburg, Soetaert & Hagens 2020](https://doi.org/10.1029/2019rg000681)).

The follow-up work of [Gustafsson & Waller 1992](https://doi.org/10.1021/ie00012a009) turns the
formulation into control: because the static nonlinearity (the titration curve) is known in form, it
can be inverted to linearise the loop, and its residual uncertainty can be handled adaptively. This
Wiener-model view — a linear dynamic block followed by a static output nonlinearity — is made
explicit by [Kalafatis, Wang & Cluett 2005](https://doi.org/10.1016/j.jprocont.2004.03.006), who
identify the titration curve as the static nonlinearity and invert it in a feedforward–feedback
linearising controller, and it also underlies fuzzy and other black-box representations of the same
curve ([Pishvaie & Shahrokhi 2006](https://doi.org/10.1016/j.fss.2006.05.010)). The unifying idea
across these is that **the titration curve is the nonlinearity**, it is largely known from the
medium composition, and inverting or scaling by its local slope converts the badly nonlinear pH loop
into an approximately linear one that fixed-gain PID can control.

### Buffering intensity and the phosphate medium

The local slope of the titration curve is set by the buffering intensity `β = dC_b/dpH`, the amount
of strong base needed per unit pH change. `β` is largest — the curve is flattest, the loop gain
smallest — near a buffer's `pKa`, and phosphate's second dissociation (`pKa₂ ≈ 7.2`) sits squarely
in the neutral band where bioreactors run, which is exactly why phosphate is used as a growth-medium
buffer. At 14 mM total phosphate the medium presents a substantial `β` around pH 7, so the process
gain seen by the pH loop is small and roughly constant over the working band and rises steeply only
as the pH is pushed toward the buffer's edges. This is favourable for control — a well-buffered loop
is more linear over its operating range than an unbuffered one — and it is the quantitative basis for
the model-based gain scaling in the method: the autotuner probes the gain at one pH with the relay,
and the known `β(pH)` from the phosphate speciation lets that single measurement be scaled to any
other setpoint. The practical reagent-and-actuator sizing side of pH control, including the wide
turndown a single loop must cover, is treated pragmatically by [Bays 1974](https://doi.org/10.1016/0013-9327%2874%2990073-1).

## What this implies for the design

Read together, the two literatures point to a specific method rather than a menu. The autotuning
experiment should be a **relay-feedback test**, because it is the most information-rich automated
experiment per unit of process disruption and needs no prior model
([Åström & Hägglund 1984](https://doi.org/10.1016/0005-1098%2884%2990014-1)). It should be realised
as an **asymmetric relay implemented through the existing split-range acid/base pair**, because the
actuation is one-sided per reagent and the culture imposes a net metabolic load that biases the
cycle — the asymmetric-relay estimators recover the static gain and tolerate that bias in one
experiment ([Shen, Wu & Yu 1996](https://doi.org/10.1002/aic.690420431);
[Kaya & Atherton 2001](https://doi.org/10.1016/s0959-1524%2899%2900073-6)). The gains should be
mapped by a **conservative, integrating-process-aware rule** — Tyreus–Luyben as the default, SIMC as
a tunable-robustness option — rather than by classical Z-N, whose robustness on this class of
dynamics is known to be poor ([Tyreus & Luyben 1992](https://doi.org/10.1021/ie00011a029);
[Skogestad 2003](https://doi.org/10.1016/s0959-1524%2802%2900062-8);
[Hägglund & Åström 2002](https://doi.org/10.1111/j.1934-6093.2002.tb00076.x)). And because a single
relay experiment characterises the loop at one pH while the process gain varies with the titration
curve, the tuned gains must be **scaled by the known buffering intensity** of the phosphate medium so
that one experiment serves the whole operating band
([Gustafsson & Waller 1983](https://doi.org/10.1016/0009-2509%2883%2980157-2), [1992](https://doi.org/10.1021/ie00012a009);
[Kalafatis, Wang & Cluett 2005](https://doi.org/10.1016/j.jprocont.2004.03.006)). Finally, because
bioreactor pH probes are noisy and the relay switches on threshold crossings, the experiment needs a
**hysteresis band** on the relay and a **measurement filter** on the derivative term, sized from the
observed noise ([Segovia, Hägglund & Åström 2014](https://doi.org/10.1016/j.conengprac.2014.07.005)).
The process-model and method documents build each of these elements out with equations and derivations,
and the in-silico studies test them against the phosphate-buffered plant.

## References

- Åström, K. J. & Hägglund, T. (1984). Automatic tuning of simple regulators with specifications on phase and amplitude margins. *Automatica* 20(5), 645–651. https://doi.org/10.1016/0005-1098(84)90014-1
- Bays (1974). pH and pIon control in process and waste streams. https://doi.org/10.1016/0013-9327(74)90073-1
- Chang, R. C., Shen, S.-H. & Yu, C.-C. (1992). Derivation of transfer function from relay feedback systems. *Ind. Eng. Chem. Res.* 31(3), 855–860. https://doi.org/10.1021/ie00003a030
- Gustafsson, T. K. & Waller, K. V. (1983). Dynamic modeling and reaction invariant control of pH. *Chem. Eng. Sci.* 38(3), 389–398. https://doi.org/10.1016/0009-2509(83)80157-2
- Gustafsson, T. K. & Waller, K. V. (1992). Nonlinear and adaptive control of pH. *Ind. Eng. Chem. Res.* 31(12), 2681–2693. https://doi.org/10.1021/ie00012a009
- Hägglund, T. (2024). Give us PID controllers and we can control the world. *IFAC-PapersOnLine*. https://doi.org/10.1016/j.ifacol.2024.08.018
- Hägglund, T. & Åström, K. J. (2002). Revisiting the Ziegler–Nichols tuning rules for PI control. *Asian J. Control* 4(4), 364–380. https://doi.org/10.1111/j.1934-6093.2002.tb00076.x
- Kalafatis, A. D., Wang, L. & Cluett, W. R. (2005). Linearizing feedforward–feedback control of pH processes based on the Wiener model. *J. Process Control* 15(1), 103–112. https://doi.org/10.1016/j.jprocont.2004.03.006
- Kaya, İ. & Atherton, D. P. (2001). Parameter estimation from relay autotuning with asymmetric limit cycle data. *J. Process Control* 11(4), 429–439. https://doi.org/10.1016/s0959-1524(99)00073-6
- Kim, Lee & Sung (2021). Improved continuous-cycling method for PID autotuning. *Processes* 9(3), 509. https://doi.org/10.3390/pr9030509
- Li, W., Eskinat, E. & Luyben, W. L. (1991). An improved autotune identification method. *Ind. Eng. Chem. Res.* 30(7), 1530–1541. https://doi.org/10.1021/ie00055a019
- Luyben, W. L. (2000). Tuning proportional–integral controllers for processes with both inverse response and deadtime. *Ind. Eng. Chem. Res.* 39(4), 973–976. https://doi.org/10.1021/ie9906114
- McAvoy, T. J., Hsu, E. & Lowenthal, S. (1972). Dynamics of pH in controlled stirred tank reactor. *Ind. Eng. Chem. Process Des. Dev.* 11(1), 68–70. https://doi.org/10.1021/i260041a013
- Middelburg, J. J., Soetaert, K. & Hagens, M. (2020). Ocean alkalinity, buffering and biogeochemical processes. *Rev. Geophys.* 58(3). https://doi.org/10.1029/2019rg000681
- Pishvaie, M. R. & Shahrokhi, M. (2006). Control of pH processes using fuzzy modeling of titration curve. *Fuzzy Sets Syst.* 157(22), 2983–3006. https://doi.org/10.1016/j.fss.2006.05.010
- Sánchez, Visioli, A. & Vilanova, R. (2021). Fitting of generic process models by an asymmetric short relay feedback experiment. *Appl. Sci.* 11(4), 1651. https://doi.org/10.3390/app11041651
- Segovia, V. R., Hägglund, T. & Åström, K. J. (2014). Measurement noise filtering for common PID tuning rules. *Control Eng. Pract.* 32, 43–63. https://doi.org/10.1016/j.conengprac.2014.07.005
- Shamsuzzoha, M. & Skogestad, S. (2010). The setpoint overshoot method: a simple and fast closed-loop approach for PID tuning. *J. Process Control* 20(10), 1220–1234. https://doi.org/10.1016/j.jprocont.2010.08.003
- Shen, S.-H., Wu, J.-S. & Yu, C.-C. (1996). Use of biased-relay feedback for system identification. *AIChE J.* 42(4), 1174–1180. https://doi.org/10.1002/aic.690420431
- Skogestad, S. (2003). Simple analytic rules for model reduction and PID controller tuning. *J. Process Control* 13(4), 291–309. https://doi.org/10.1016/s0959-1524(02)00062-8
- Skogestad, S. & Grimholt, C. (2012). The SIMC method for smooth PID controller tuning. In *PID Control in the Third Millennium*. https://doi.org/10.1007/978-1-4471-2425-2_5
- Sung, S. W., Park, J. H. & Lee, I.-B. (1995). Modified relay feedback method. *Ind. Eng. Chem. Res.* 34(11), 4133–4135. https://doi.org/10.1021/ie00038a059
- Tan, K. K., Lee, T. H. & Wang, Q.-G. (1996). Enhanced automatic tuning procedure for process control of PI/PID controllers. *AIChE J.* 42(9), 2555–2562. https://doi.org/10.1002/aic.690420916
- Tyreus, B. D. & Luyben, W. L. (1992). Tuning PI controllers for integrator/dead time processes. *Ind. Eng. Chem. Res.* 31(11), 2625–2628. https://doi.org/10.1021/ie00011a029
- Wang, Q.-G., Hang, C. C. & Zou, B. (1997). Low-order modeling from relay feedback. *Ind. Eng. Chem. Res.* 36(2), 375–381. https://doi.org/10.1021/ie960412+
- Ziegler, J. G. & Nichols, N. B. (1942). Optimum settings for automatic controllers. *Trans. ASME* 64, 759–768. https://doi.org/10.1115/1.4019269
