# Literature review: sensor response time and reactor mixing time from acid/base pulses

This review supports two new characterization features for the `reactors_czlab` bioreactor
interface: an estimate of the **response time of a sensor** from a recorded step, and an estimate
of the **mixing (homogenization) time of a reactor** from an acid or base pulse read on the pH
probe. The two are treated together because they are not independent measurements. A pulse read on
a real probe is the convolution of the vessel's homogenization dynamics with the probe's own first
order lag, so the number an operator would naively call "mixing time" is contaminated by sensor
response unless the two are separated deliberately. The literature on each half is mature; the
contribution needed here is to connect them and to fix the operating envelope in which a coarsely
sampled (1–30 s) pH signal can still yield trustworthy numbers.

## Characterizing sensor response time

### The step response and its low-order parameterization

The response time of a process sensor is, in the language of process control, an identification
problem: perturb the input with a known step and read the dynamics of the output. The dominant
dynamics of most electrochemical and optical probes are well approximated by a first-order lag,
optionally preceded by a pure transport delay, so the working model throughout this literature is
the first-order-plus-dead-time (FOPDT) form with a time constant τ and a dead time L. The enduring
parameterizations come from the process-reaction-curve tradition: [Sundaresan & Krishnaswamy
1978](https://doi.org/10.1002/cjce.5450560215) give the estimators for τ and L that are still the
reference for a single recorded transient, deriving them so that the fitted model matches the data
not at arbitrary points but at characteristic fractional-response times, and [Rangaiah &
Krishnaswamy 1994](https://doi.org/10.1021/ie00031a029) extend the same logic to a second-order
model for probes whose roll-on is too gradual to be a single exponential. The practical upshot is
that a step test yields two numbers with clear physical meaning — a transport delay before anything
moves and a time constant setting how fast it then approaches the new value — and that these are far
more robust to fit than a high-order model would be from one noisy curve.

The metrological conventions layered on top of τ are the ones an operator actually reads. The time
to reach 63.2% of the total change is the single time constant of a pure first-order system
(T63 = τ); the time to reach 90% is T90 ≈ L + 2.303·τ, and T90 is the quantity most sensor
datasheets and standards quote because it is model-free to measure — one simply reads the clock when
the normalized response first crosses 0.90. Reporting both the fitted (τ, L) and the interpolated
T90 gives a model-based estimate and a model-free cross-check from the same trace, and a discrepancy
between them is itself the diagnostic that the first-order assumption is failing.

### Probe dynamics as a first-order filter

That the electrochemical probe behaves as a first-order filter on the true process value is not an
assumption of convenience but an experimentally established fact, and the body of work that
established it did so in the course of trying to remove the probe's distortion from oxygen-transfer
measurements. [Kok & Zajic 1975](https://doi.org/10.1002/bit.260170406) characterized the dynamic
response of a polarographic oxygen probe directly and showed it is dominated by diffusion through
the membrane, giving a response that is first-order to good approximation with a time constant of
seconds to tens of seconds. [Dang, Karrer & Dunn 1977](https://doi.org/10.1002/bit.260190606) made
the consequence explicit for parameter estimation: the measured signal is the convolution of the
true dissolved-oxygen trajectory with the probe's impulse response, and they used moment analysis to
back out transfer coefficients corrected for that lag. The synthesis of this era is [Linek, Vacek &
Beneš 1987](https://doi.org/10.1016/0300-9467%2887%2985003-7), a critical review that verifies
experimentally when the probe's own dynamics dominate the apparent measurement and prescribes when
the correction is mandatory rather than optional — the central lesson being that a measurement of a
process time constant is meaningless until the sensor time constant it is entangled with is known
and comparable in magnitude.

This is the load-bearing result for the present features. It says that a sensor's step response is
legitimately summarized by a first-order τ (plus dead time), and — crucially for the mixing-time
feature — that because the probe is a first-order filter, its distortion of a faster upstream signal
is invertible: if τ is known, the true signal can be reconstructed from the measured one. The
oxygen-transfer literature reaches the same methodological conclusion from the applications side.
[Van 't Riet 1979](https://doi.org/10.1021/i260071a001) catalogs the measuring methods and their
systematic errors, and [Jiang & Stenstrom 2012](https://doi.org/10.1061/%28asce%29ee.1943-7870.0000456)
show quantitatively that ignoring probe response biases the estimated transfer coefficient, with the
bias growing as the probe time constant approaches the process time scale being measured. The
recurring warning across four decades is the one the mixing-time feature must heed: when the
sensor is not fast relative to the process, its lag is not noise to be averaged away but a
systematic distortion to be modeled.

## Determining mixing time from a tracer pulse

### The tracer method and its homogenization criterion

Mixing time is the time a stirred vessel takes to bring an added bolus to a defined degree of
homogeneity, and the standard way to measure it is to inject a tracer pulse and watch a local probe
relax to its final value. [Ascanio 2015](https://doi.org/10.1016/j.cjche.2014.10.022) reviews the
experimental techniques and fixes the definitions this feature adopts: mixing time is reported at a
stated closeness to full homogeneity, conventionally t95 (the time after which the normalized signal
stays permanently within ±5% of its final value) or the stricter t99, and the choice of tracer —
acid/base tracked by pH, salt tracked by conductivity, dye tracked by decolorization or image
analysis, trades off against how faithfully the probe tracks the transient. The essential caution
in the definitional layer is that the criterion is a *permanent* entry into the band: a signal that
touches ±5% while still oscillating between well-mixed and poorly-mixed zones has not mixed, so the
estimate must be the last upcrossing of the band edge, not the first.

The acid/base pulse tracked by a pH electrode is the variant directly relevant here, because
`reactors_czlab` already dispenses NaOH and HCl through a calibrated split-range pump pair and reads
pH on a Hamilton probe. Its advantage is that it reuses hardware the reactor already has; its
characteristic difficulty is chemical and is discussed below. Colorimetric and image-analysis
methods ([Cabaret, Bonnot, Fradette & Tanguy 2007](https://doi.org/10.1021/ie0613265)) give a
whole-field mixing time free of any probe lag and are the reference against which single-probe
methods are validated, but they need optical access and image processing the Pi-based system does
not have. Conductivity tracers ([Distelhoff & Marquis 2001](https://doi.org/10.1002/cjce.5450790202))
share the pulse-and-relax logic without pH's nonlinearity but need a second probe the vessel is not
instrumented for. The pragmatic conclusion, consistent with Ascanio's review, is that the pH-pulse
method is the right one *for this reactor* provided its two known distortions — the nonlinear pH
response and the probe lag — are corrected rather than ignored.

### The transient is not a single exponential

A subtlety that separates a correct estimator from a naive one is that the homogenization transient
in a real stirred vessel is not a clean exponential decay. The vessel behaves as a small number of
circulating zones exchanging material, so a probe in one zone sees a stepped or overshooting
approach as successive circulation loops carry the tracer past it. This is the classic
tanks-in-series/circulation picture that [Levenspiel, Lai & Chatlynne
1970](https://doi.org/10.1016/0009-2509%2870%2985084-9) formalize through the residence-time and
tracer-response framework. [Grenville & Nienow 2003](https://doi.org/10.1002/0471451452.ch9) give
the engineering correlations that tie the resulting mixing time to power input and geometry and make
clear that a single circulation time is the natural internal scale of the transient, with full
homogenization taking several circulations. The design consequence for the estimator is that fitting
a single first-order curve to the *reactor* response is wrong on principle; the robust procedure is
the model-free band-crossing criterion applied to a signal that has first been linearized and
deconvolved.

Modern work reinforces that a single probe reads a local, not a global, mixing time, which bounds
what the feature can honestly claim. [Rosseburg et al. 2018](https://doi.org/10.1016/j.ces.2018.05.008)
and [Fitschen et al. 2021](https://doi.org/10.1016/j.cesx.2021.100098) document that mixing time
varies with position in large vessels and develop methods for the local mixing-time *distribution*,
and [Paul & Herwig 2020](https://doi.org/10.1002/elsc.201900162) frame why this matters biologically
— the point of characterizing mixing in a bioreactor is that concentration gradients seen by cells
drive process outcomes on scale-up. For the bench-scale `reactors_czlab` vessel a single-probe
estimate is defensible, but the feature should report it as the mixing time *at the probe location*
and note that it is a lower bound on the worst-case zone.

### Correcting the pH nonlinearity and the probe lag

Two corrections turn the raw pH trace into a defensible mixing time, and both follow directly from
the sections above. First, pH is logarithmic in hydrogen-ion activity and, in the phosphate-buffered
growth medium this reactor uses, the mapping from added titrant to pH is strongly nonlinear along
the titration curve — the same nonlinearity the existing autotuning feature already models with a
charge-balance titration curve (`reactors_czlab.autotune.model`) and that [Gustafsson & Waller
1992](https://doi.org/10.1021/ie00012a009) identify as the defining difficulty of pH loops. A ±5%
band applied to raw pH is therefore not a ±5% band on tracer concentration; the correct homogeneity
variable is the tracer amount recovered by inverting the titration curve, and the band criterion
must be applied to *that* linearized signal. Second, once linearized, the signal is still the
mixing transient seen through the probe's first-order lag, and the invertibility established by the
probe-dynamics literature lets it be removed: the deconvolution c_true = c_meas + τ·(dc_meas/dt),
the exact inverse of a first-order filter, reconstructs the true tracer trajectory from the measured
one given the τ obtained from the sensor-response feature. This is the correction Linek's
review and Dang's moment analysis prescribe, transplanted from oxygen transfer to mixing. The
magnitude of the effect scales with the ratio τ_probe/t_mix: when the probe is fast relative to
mixing the correction is negligible, but as the two time scales approach parity, a regime a slow
pH electrode on a well-mixed bench vessel can easily reach, the uncorrected estimate is biased high
by an amount comparable to τ_probe itself, which is why the two features must ship together.

## Implications for the sampling-limited implementation

The binding practical constraint is that the server samples every reactor on a shared 1–30 s period
(default 10 s), so both estimates are reconstructed from coarsely and uniformly sampled data rather
than a fast analog trace. This sets hard applicability limits that the robustness study must
quantify: a time constant or mixing time can only be resolved if it is several sample periods long,
so the sampling period must be small relative to both τ_probe and t95, and the sample-and-hold
introduces its own half-period of apparent delay that inflates the measured dead time by roughly
Δt/2. The deconvolution is a numerical differentiation of the sampled signal and so amplifies
sampling noise, which places a floor on the pulse size — the perturbation must be large enough that
the response clears the noise band by a comfortable margin, while staying within the buffer's linear
range and the reactor's dose budget. The relay-autotuning literature already in this repository —
[Åström & Hägglund 1984](https://doi.org/10.1016/0005-1098%2884%2990014-1) and, further back,
[Ziegler & Nichols 1942](https://doi.org/10.1115/1.4019269) — is relevant here not for its tuning
rules but because it solved the same engineering problem of extracting a clean dynamic parameter
from a bounded, automated, safety-limited experiment on a live vessel. The interlock and
dose-budget machinery it motivated in `autotune/` is the right pattern for the mixing-time pulse.
The methodology and specification documents that accompany this review carry these limits into
concrete acceptance criteria and an operating envelope validated in silico.
