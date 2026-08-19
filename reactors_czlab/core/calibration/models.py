"""Runtime models and mathematics for pump calibration."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import exp, inf, isfinite, log
from typing import TYPE_CHECKING

from reactors_czlab.core.data import MAX_OUTPUT

if TYPE_CHECKING:
    from lmfit.model import ModelResult

#: Dose the time bound below is expressed for, in mL.
REFERENCE_DOSE_ML = 1.0

#: Longest a single reference dose may hold the pump ON, in seconds.
MAX_DOSE_SECONDS = 24 * 60 * 60.0

#: Slowest installable flow at the dispense duty, in mL/min.
MIN_DISPENSE_FLOW = 60.0 * REFERENCE_DOSE_ML / MAX_DOSE_SECONDS

#: Calibration fields the pump path does arithmetic with.
_DRIVING_FIELDS = ("a", "b", "c", "min_duty", "max_duty", "dispense_duty")

CALIBRATION_MODELS = (
    "linear",
    "dead-zone linear",
    "saturating exponential",
    "logistic",
    "power",
)

MODEL_PARAMETER_NAMES = {
    "linear": ("slope", "intercept"),
    "dead-zone linear": ("k", "d0"),
    "saturating exponential": ("Qmax", "k", "d0"),
    "logistic": ("Qmax", "k", "d50"),
    "power": ("amplitude", "exponent"),
}


def calibration_parameter_reason(
    model: str,
    a: float,
    b: float,
    c: float = 0.0,
) -> str | None:
    """Return why model coefficients do not define a safe inverse."""
    if model not in CALIBRATION_MODELS:
        return f"model {model!r} is unsupported; expected one of {', '.join(CALIBRATION_MODELS)}"
    if not all(isfinite(value) for value in (a, b, c)):
        return "calibration model coefficients must be finite"
    if a <= 0:
        return f"coefficient a={a:.6g} is not positive; a higher duty would not mean more flow"
    if model == "linear":
        return None
    if model == "power" and b <= 0:
        return f"power exponent b={b:.6g} is not positive; a higher duty would not mean more flow"
    if model == "dead-zone linear" and not 0 <= b <= MAX_OUTPUT:
        return f"coefficient b={b:.6g} is outside the 0 - {MAX_OUTPUT:.0f} duty range"
    if model in {"saturating exponential", "logistic"}:
        if b <= 0:
            return f"coefficient b={b:.6g} is not positive"
        if not 0 <= c <= MAX_OUTPUT:
            return f"coefficient c={c:.6g} is outside the 0 - {MAX_OUTPUT:.0f} duty range"
    return None


def calibration_flow(
    model: str,
    a: float,
    b: float,
    c: float,
    duty: float,
) -> float:
    """Evaluate one supported calibration model at ``duty``."""
    match model:
        case "linear":
            return a * duty + b
        case "dead-zone linear":
            return max(0.0, a * (duty - b))
        case "saturating exponential":
            exponent = -b * (duty - c)
            if exponent > 709.0:
                return -inf
            return a * (1.0 - exp(exponent))
        case "logistic":
            scaled = b * (duty - c)
            if scaled >= 0:
                return a / (1.0 + exp(-scaled))
            factor = exp(scaled)
            return a * factor / (1.0 + factor)
        case "power":
            try:
                return a * duty**b
            except OverflowError:
                return inf
        case _:
            error_message = f"unsupported calibration model {model!r}"
            raise ValueError(error_message)


def calibration_duty(
    model: str,
    a: float,
    b: float,
    c: float,
    flow: float,
) -> float:
    """Invert one supported calibration model for ``flow``."""
    match model:
        case "linear":
            return (flow - b) / a
        case "dead-zone linear" if flow < 0:
            error_message = "a dead-zone linear calibration cannot invert negative flow"
            raise ValueError(error_message)
        case "dead-zone linear":
            return b + flow / a
        case "saturating exponential" if not 0 <= flow < a:
            error_message = f"saturating exponential flow must be within [0, {a:.6g})"
            raise ValueError(error_message)
        case "saturating exponential":
            return c - log(1.0 - flow / a) / b
        case "logistic" if not 0 < flow < a:
            error_message = f"logistic flow must be within (0, {a:.6g})"
            raise ValueError(error_message)
        case "logistic":
            return c - log(a / flow - 1.0) / b
        case "power" if flow < 0:
            error_message = "a power calibration cannot invert negative flow"
            raise ValueError(error_message)
        case "power":
            return (flow / a) ** (1.0 / b)
        case _:
            error_message = f"unsupported calibration model {model!r}"
            raise ValueError(error_message)


def calibration_jacobian(
    model: str,
    a: float,
    b: float,
    c: float,
    duty: float,
) -> tuple[float, ...]:
    """Derivative of fitted flow with respect to active coefficients."""
    match model:
        case "linear":
            return (duty, 1.0)
        case "dead-zone linear" if duty <= b:
            return (0.0, 0.0)
        case "dead-zone linear":
            return (duty - b, -a)
        case "saturating exponential":
            exponent = -b * (duty - c)
            factor = inf if exponent > 709.0 else exp(exponent)
            return (
                1.0 - factor,
                a * (duty - c) * factor,
                -a * b * factor,
            )
        case "logistic":
            fraction = calibration_flow(model, a, b, c, duty) / a
            common = a * fraction * (1.0 - fraction)
            return (
                fraction,
                common * (duty - c),
                -common * b,
            )
        case "power":
            first = duty**b
            second = 0.0 if duty == 0 else a * first * log(duty)
            return (first, second)
        case _:
            error_message = f"unsupported calibration model {model!r}"
            raise ValueError(error_message)


def calibration_zero_threshold(model: str, a: float, b: float, c: float) -> float:
    """Return the model-implied zero-flow threshold in duty counts."""
    match model:
        case "linear":
            return max(0.0, -b / a)
        case "dead-zone linear":
            return b
        case "saturating exponential":
            return c
        case "power" | "logistic":
            return 0.0
        case _:
            error_message = f"unsupported calibration model {model!r}"
            raise ValueError(error_message)


def calibration_equation(
    model: str,
    a: float,
    b: float,
    c: float = 0.0,
) -> str:
    """Format a numeric model equation for an operator."""
    match model:
        case "linear":
            return f"flow = {a:.6g} * duty + {b:.6g}"
        case "dead-zone linear":
            return f"flow = max(0, {a:.6g} * (duty - {b:.6g}))"
        case "saturating exponential":
            return f"flow = {a:.6g} * (1 - exp(-{b:.6g} * (duty - {c:.6g})))"
        case "logistic":
            return f"flow = {a:.6g} / (1 + exp(-{b:.6g} * (duty - {c:.6g})))"
        case "power":
            return f"flow = {a:.6g} * duty ** {b:.6g}"
        case _:
            error_message = f"unsupported calibration model {model!r}"
            raise ValueError(error_message)


@dataclass
class Calibration:
    """Calibration of a pump selected from safe monotone models.

    Flow is mL/min and duty is raw PLC counts. ``fitted_at`` empty means the
    calibration has never been fitted and must not be used to convert.

    Parameters
    ----------
    file:
        File stem the calibration is stored under, e.g. ``R0_pwm0``.
    a, b, c:
        Model coefficients. ``c`` defaults to zero for legacy two-parameter
        files.
    model:
        One of ``CALIBRATION_MODELS``; ``"linear"`` remains the
        backwards-compatible default.
    residual:
        Unweighted fit chi-square, or ``None`` for a legacy linear file.
    aic:
        Akaike information criterion used for model selection, or ``None``
        for a legacy file.
    zero_flow_duty:
        Optional stall evidence kept separate from ``points`` and the fit.
    fit_points:
        Plotting samples as ``(duty, fitted_flow, lower_95, upper_95)``.
        Legacy linear files have no samples until they are refitted.
    min_duty:
        Stall floor. Below this the pump does not turn.
    max_duty:
        Highest duty the pump may be driven at.
    dispense_duty:
        Duty used for volume doses.
    points:
        Measured ``(duty, flow)`` pairs the fit was built from.
    fitted_at:
        ISO timestamp of the fit, empty when unfitted.
    r2:
        Fit quality. Informational: it is trivially 1.0 for two points.
    """

    file: str
    a: float = 1.0
    b: float = 0.0
    min_duty: float = 0.0
    max_duty: float = MAX_OUTPUT
    dispense_duty: float = MAX_OUTPUT
    points: list[tuple[float, float]] = field(default_factory=list)
    fitted_at: str = ""
    r2: float = 0.0
    model: str = "linear"
    residual: float | None = None
    fit_points: list[tuple[float, float, float, float]] = field(default_factory=list)
    # Appended so the positional order of every legacy field remains intact.
    c: float = 0.0
    aic: float | None = None
    zero_flow_duty: float | None = None

    @property
    def is_fitted(self) -> bool:
        """Whether the calibration has ever been fitted."""
        return bool(self.fitted_at)

    def flow_at(self, duty: float) -> float:
        """Flow in mL/min produced at ``duty`` counts."""
        return calibration_flow(self.model, self.a, self.b, self.c, duty)

    def duty_for(self, flow: float) -> float:
        """Duty counts needed for ``flow`` mL/min.

        Raises
        ------
        ValueError
            If the model cannot invert the requested flow or is unknown.
        ZeroDivisionError
            If a coefficient required for inversion is zero.
        """
        return calibration_duty(self.model, self.a, self.b, self.c, flow)

    @property
    def numeric_equation(self) -> str:
        """Fitted equation with the numeric coefficients substituted."""
        return calibration_equation(self.model, self.a, self.b, self.c)

    @property
    def named_parameters(self) -> dict[str, float]:
        """Model coefficients keyed by their domain-specific names."""
        values = (self.a, self.b, self.c)
        return dict(zip(MODEL_PARAMETER_NAMES[self.model], values, strict=False))

    def installable_reason(self) -> str | None:
        """Return why this calibration may not replace one on a channel."""
        for name in _DRIVING_FIELDS:
            value = getattr(self, name)
            if not isfinite(value):
                return (
                    f"{name} is {value}, not a finite number; no range "
                    "check here or clamp in a controller can catch it, "
                    "and the duty it produces cannot be written to a pin"
                )
        parameter_reason = calibration_parameter_reason(
            self.model,
            self.a,
            self.b,
            self.c,
        )
        if parameter_reason is not None:
            return parameter_reason
        if not isfinite(self.r2):
            return f"fit quality r2 is {self.r2}, not a finite number; a fit that could not score itself is not one to drive a pump with"
        if self.residual is not None and (not isfinite(self.residual) or self.residual < 0):
            return f"fit residual {self.residual} is not a finite, non-negative chi-square"
        if self.aic is not None and not isfinite(self.aic):
            return f"fit AIC {self.aic} is not a finite number"
        if self.zero_flow_duty is not None and (not isfinite(self.zero_flow_duty) or not 0 <= self.zero_flow_duty <= MAX_OUTPUT):
            return f"zero-flow duty {self.zero_flow_duty} must be a finite duty within 0 - {MAX_OUTPUT:.0f}"
        has_uncertainty = bool(self.fit_points)
        if (self.residual is None) != (not has_uncertainty):
            return "fit residual and 95% prediction samples must either both be present or both be absent"
        if self.model != "linear" and not has_uncertainty:
            return f"a {self.model} calibration requires 95% prediction samples"
        for point in self.points:
            if not isinstance(point, list | tuple) or len(point) != 2:
                return "each measured calibration point must contain duty and flow"
            duty, measured_flow = point
            if not isfinite(duty) or not isfinite(measured_flow):
                return "measured calibration points must be finite"
            if not 0 <= duty <= MAX_OUTPUT or measured_flow < 0:
                return "measured calibration points require an in-range duty and non-negative flow"
        if self.min_duty < 0:
            return f"stall floor {self.min_duty:.0f} is negative; the floor is what keeps a converted duty at or above zero"
        if self.min_duty > self.max_duty:
            return f"min duty {self.min_duty:.0f} is above max duty {self.max_duty:.0f}; there is no usable band"
        if self.max_duty > MAX_OUTPUT:
            return f"max duty {self.max_duty:.0f} is above the {MAX_OUTPUT:.0f} count full scale of the output"
        if self.dispense_duty < self.min_duty:
            return (
                f"dispense duty {self.dispense_duty:.0f} is below the "
                f"stall floor {self.min_duty:.0f}; the pump was "
                "measured not to turn there, so a dose would run the "
                "clock down while delivering nothing"
            )
        if self.dispense_duty > self.max_duty:
            return (
                f"dispense duty {self.dispense_duty:.0f} is above max "
                f"duty {self.max_duty:.0f}; a dose is written at that "
                "duty, so it would drive the pump past its ceiling"
            )
        if has_uncertainty and self.points and self.max_duty > max(duty for duty, _ in self.points):
            return f"max duty {self.max_duty:.0f} is above the largest measured duty; uncertainty qualification may not extrapolate"
        if has_uncertainty:
            previous_duty = -inf
            max_sample: tuple[float, float, float, float] | None = None
            for sample in self.fit_points:
                if not isinstance(sample, list | tuple) or len(sample) != 4:
                    return "each prediction sample must contain four numbers"
                duty, fitted, lower, upper = sample
                if not all(isfinite(value) for value in sample):
                    return f"prediction sample at duty {duty} is non-finite"
                if duty <= previous_duty:
                    return "prediction sample duties must be strictly increasing"
                if lower > fitted or fitted > upper:
                    return f"prediction sample at duty {duty:.0f} is not ordered lower <= fitted <= upper"
                previous_duty = duty
                if abs(duty - self.max_duty) <= 1e-9:
                    max_sample = sample
            if max_sample is None:
                return "prediction samples do not include the selected max duty"
            _, fitted, lower, upper = max_sample
            half_width = (upper - lower) / 2.0
            if lower <= 0:
                return "the 95% lower prediction bound is not positive at max duty"
            if fitted <= 0 or half_width > 0.2 * fitted:
                return "the 95% prediction half-width exceeds 20% of fitted flow at max duty"
        max_flow = self.flow_at(self.max_duty)
        if not isfinite(max_flow):
            return "fitted flow at max duty is non-finite"
        if max_flow <= 0:
            return f"this calibration produces no flow anywhere in its usable band (zero or negative at max duty {self.max_duty:.0f})"
        flow = self.flow_at(self.dispense_duty)
        if not isfinite(flow):
            return "fitted flow at dispense duty is non-finite"
        if flow == 0:
            return f"dispense duty {self.dispense_duty:.0f} produces no flow; a dose at that duty would never finish"
        if flow < 0:
            return (
                f"dispense duty {self.dispense_duty:.0f} is below "
                f"where the line reaches zero flow ({flow:.6g} "
                "mL/min); a dose there would end immediately having "
                "delivered nothing"
            )
        if flow < MIN_DISPENSE_FLOW:
            return (
                f"dispense duty {self.dispense_duty:.0f} delivers only "
                f"{flow:.6g} mL/min; {REFERENCE_DOSE_ML:.0f} mL would "
                f"take {60.0 * REFERENCE_DOSE_ML / flow:.0f} s, past "
                f"the {MAX_DOSE_SECONDS:.0f} s a dose may hold the pump on"
            )
        return None


@dataclass(frozen=True)
class CalibrationFit:
    """Selected LMFit result and its persisted uncertainty samples."""

    model: str
    a: float
    b: float
    c: float
    r2: float
    residual: float
    aic: float
    max_duty: float
    fit_points: list[tuple[float, float, float, float]]


@dataclass(frozen=True)
class _Candidate:
    """One valid fitted model before cross-model selection."""

    name: str
    result: ModelResult
    a: float
    b: float
    c: float
    r2: float
    aic: float
