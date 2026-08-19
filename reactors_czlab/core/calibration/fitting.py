"""Fit and qualify safe, monotone pump calibration models."""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
from lmfit import Model
from scipy.stats import t as student_t

from reactors_czlab.core.calibration.model import CalibrationFit, _Candidate
from reactors_czlab.core.data import (
    CALIBRATION_MODELS,
    MAX_OUTPUT,
    calibration_flow,
    calibration_jacobian,
    calibration_parameter_reason,
)

MIN_POINTS = 4
MAX_RELATIVE_UNCERTAINTY = 0.20
PLOT_SAMPLES = 128

_MODEL_COMPLEXITY = {
    "linear": 2,
    "dead-zone linear": 2,
    "power": 2,
    "saturating exponential": 3,
    "logistic": 3,
}
_MODEL_ORDER = {name: index for index, name in enumerate(CALIBRATION_MODELS)}


def _evaluate(
    model: str,
    duty: np.ndarray,
    a: float,
    b: float,
    c: float,
) -> np.ndarray:
    """Evaluate the centralized scalar model over an array."""
    return np.fromiter(
        (calibration_flow(model, a, b, c, float(value)) for value in duty),
        dtype=float,
        count=duty.size,
    )


def _linear(duty: np.ndarray, a: float, b: float) -> np.ndarray:
    return _evaluate("linear", duty, a, b, 0.0)


def _dead_zone_linear(duty: np.ndarray, a: float, b: float) -> np.ndarray:
    return _evaluate("dead-zone linear", duty, a, b, 0.0)


def _saturating_exponential(
    duty: np.ndarray,
    a: float,
    b: float,
    c: float,
) -> np.ndarray:
    return _evaluate("saturating exponential", duty, a, b, c)


def _logistic(
    duty: np.ndarray,
    a: float,
    b: float,
    c: float,
) -> np.ndarray:
    return _evaluate("logistic", duty, a, b, c)


def _power(duty: np.ndarray, a: float, b: float) -> np.ndarray:
    return _evaluate("power", duty, a, b, 0.0)


_MODEL_FUNCTIONS: dict[str, Callable[..., np.ndarray]] = {
    "linear": _linear,
    "dead-zone linear": _dead_zone_linear,
    "saturating exponential": _saturating_exponential,
    "logistic": _logistic,
    "power": _power,
}


def _prediction_band(
    candidate: _Candidate,
    duty: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return fitted flow and a two-sided 95% prediction band."""
    fitted = _evaluate(
        candidate.name,
        duty,
        candidate.a,
        candidate.b,
        candidate.c,
    )
    covariance = np.asarray(candidate.result.covar, dtype=float)
    jacobian = np.asarray(
        [
            calibration_jacobian(
                candidate.name,
                candidate.a,
                candidate.b,
                candidate.c,
                float(value),
            )
            for value in duty
        ],
        dtype=float,
    )
    mean_variance = np.einsum("ij,jk,ik->i", jacobian, covariance, jacobian)
    residual_variance = max(0.0, float(candidate.result.redchi))
    dof = max(1, int(candidate.result.ndata - candidate.result.nvarys))
    multiplier = float(student_t.ppf(0.975, dof))
    half_width = multiplier * np.sqrt(
        np.maximum(0.0, mean_variance + residual_variance),
    )
    return fitted, fitted - half_width, fitted + half_width


def _qualify_candidate(
    candidate: _Candidate,
    highest_measured: float,
) -> tuple[float, list[tuple[float, float, float, float]]]:
    """Return an uncertainty-qualified ceiling and plot samples."""
    integer_duties = np.arange(0.0, math.floor(highest_measured) + 1.0)
    fitted, lower, upper = _prediction_band(candidate, integer_duties)
    qualified = (
        np.isfinite(fitted)
        & np.isfinite(lower)
        & np.isfinite(upper)
        & (lower > 0.0)
        & (((upper - lower) / 2.0) <= MAX_RELATIVE_UNCERTAINTY * fitted)
    )
    if not np.any(qualified):
        error_message = (
            "no duty has a positive 95% lower prediction bound and at most "
            "20% relative uncertainty"
        )
        raise ValueError(error_message)
    max_duty = float(integer_duties[np.flatnonzero(qualified)[-1]])

    plot_duties = np.linspace(0.0, highest_measured, PLOT_SAMPLES)
    if not np.any(plot_duties == max_duty):
        plot_duties = np.sort(np.append(plot_duties, max_duty))
    plot_fitted, plot_lower, plot_upper = _prediction_band(candidate, plot_duties)
    if not all(
        np.all(np.isfinite(values))
        for values in (plot_fitted, plot_lower, plot_upper)
    ):
        error_message = "prediction band is non-finite"
        raise ValueError(error_message)
    samples = [
        tuple(float(value) for value in sample)
        for sample in zip(
            plot_duties,
            plot_fitted,
            plot_lower,
            plot_upper,
            strict=True,
        )
    ]
    return max_duty, samples


def fit_models(points: list[tuple[float, float]]) -> CalibrationFit:
    """Fit supported models and select the lowest-AIC qualified candidate.

    Zero-flow observations are stall evidence, not curve measurements, and
    must be kept outside this function.

    Parameters
    ----------
    points:
        Positive-flow measured ``(duty, flow)`` pairs.

    Returns
    -------
    CalibrationFit
        The uncertainty-qualified candidate with the lowest AIC.

    Raises
    ------
    ValueError
        If the measurements are invalid or no candidate is safe to install.
    """
    distinct = len({duty for duty, _ in points})
    if distinct < MIN_POINTS:
        error_message = (
            f"need at least {MIN_POINTS} distinct duty points, got "
            f"{distinct} from {len(points)} measurements"
        )
        raise ValueError(error_message)
    if any(not math.isfinite(value) for point in points for value in point):
        error_message = "calibration points must contain only finite numbers"
        raise ValueError(error_message)
    if any(not 0.0 <= duty <= MAX_OUTPUT or flow <= 0.0 for duty, flow in points):
        error_message = (
            f"fitted calibration points require duty within 0 - "
            f"{MAX_OUTPUT:.0f} and positive flow; record zero flow as "
            "separate stall evidence"
        )
        raise ValueError(error_message)

    ordered = sorted(points)
    duty = np.asarray([point[0] for point in ordered], dtype=float)
    flow = np.asarray([point[1] for point in ordered], dtype=float)
    highest_measured = float(np.max(duty))
    qualified: list[
        tuple[_Candidate, float, list[tuple[float, float, float, float]]]
    ] = []
    failures: list[str] = []
    for name in CALIBRATION_MODELS:
        try:
            candidate = _fit_candidate(name, duty, flow)
            max_duty, fit_points = _qualify_candidate(candidate, highest_measured)
            qualified.append((candidate, max_duty, fit_points))
        except Exception as exc:  # noqa: BLE001 - third-party fit boundary
            failures.append(f"{name}: {exc}")
    if not qualified:
        error_message = "no installable calibration model: " + "; ".join(failures)
        raise ValueError(error_message)

    best = min(qualified, key=lambda item: item[0].aic)
    best_aic = best[0].aic
    best_residual = float(best[0].result.chisqr)
    tied = [
        item
        for item in qualified
        if math.isclose(item[0].aic, best_aic, rel_tol=1e-9, abs_tol=1e-9)
        or math.isclose(
            float(item[0].result.chisqr),
            best_residual,
            rel_tol=1e-9,
            abs_tol=1e-12,
        )
    ]
    selected, max_duty, fit_points = min(
        tied,
        key=lambda item: (
            _MODEL_COMPLEXITY[item[0].name],
            _MODEL_ORDER[item[0].name],
        ),
    )
    return CalibrationFit(
        model=selected.name,
        a=selected.a,
        b=selected.b,
        c=selected.c,
        r2=selected.r2,
        residual=float(selected.result.chisqr),
        aic=selected.aic,
        max_duty=max_duty,
        fit_points=fit_points,
    )


def _initial_parameters(
    name: str,
    duty: np.ndarray,
    flow: np.ndarray,
) -> dict[str, dict[str, float]]:
    """Build bounded LMFit starting values for one candidate."""
    span = max(1.0, float(np.ptp(duty)))
    peak = float(np.max(flow))
    slope, intercept = np.polyfit(duty, flow, 1)
    raw_slope = float(slope)
    slope = max(raw_slope, np.finfo(float).tiny)
    threshold = (
        min(float(np.min(duty)), max(0.0, -float(intercept) / raw_slope))
        if raw_slope > 0.0
        else 0.0
    )
    match name:
        case "linear":
            return {"a": {"value": slope}, "b": {"value": float(intercept)}}
        case "dead-zone linear":
            return {
                "a": {"value": slope, "min": np.finfo(float).tiny},
                "b": {"value": threshold, "min": 0.0, "max": float(np.min(duty))},
            }
        case "saturating exponential":
            return {
                "a": {"value": peak * 1.1, "min": peak, "max": peak * 1e4},
                "b": {"value": 1.0 / span, "min": np.finfo(float).tiny, "max": 1.0},
                "c": {"value": threshold, "min": 0.0, "max": float(np.min(duty))},
            }
        case "logistic":
            return {
                "a": {"value": peak * 1.1, "min": peak, "max": peak * 1e4},
                "b": {"value": 4.0 / span, "min": np.finfo(float).tiny, "max": 1.0},
                "c": {"value": float(np.median(duty)), "min": 0.0, "max": MAX_OUTPUT},
            }
        case "power":
            positive = duty > 0
            if np.count_nonzero(positive) < MIN_POINTS:
                error_message = "power fit needs four positive duties"
                raise ValueError(error_message)
            return {
                "a": {
                    "value": max(
                        np.finfo(float).tiny,
                        float(flow[positive][-1] / duty[positive][-1]),
                    ),
                    "min": np.finfo(float).tiny,
                },
                "b": {"value": 1.0, "min": np.finfo(float).tiny, "max": 10.0},
            }
        case _:
            error_message = f"unsupported calibration model {name!r}"
            raise ValueError(error_message)


def _fit_candidate(
    name: str,
    duty: np.ndarray,
    flow: np.ndarray,
) -> _Candidate:
    """Fit and validate one named LMFit model."""
    model = Model(_MODEL_FUNCTIONS[name], independent_vars=["duty"])
    parameters = model.make_params()
    for parameter_name, options in _initial_parameters(name, duty, flow).items():
        parameters[parameter_name].set(**options)
    result = model.fit(
        flow,
        parameters,
        duty=duty,
        method="least_squares",
        max_nfev=100_000,
        fit_kws={"ftol": 1e-14, "xtol": 1e-14, "gtol": 1e-14},
    )

    a = float(result.params["a"].value)
    b = float(result.params["b"].value)
    c = float(result.params["c"].value) if "c" in result.params else 0.0
    if not result.success:
        error_message = result.message or "optimizer did not converge"
        raise ValueError(error_message)
    expected_shape = (result.nvarys, result.nvarys)
    if result.covar is None or np.shape(result.covar) != expected_shape:
        error_message = "fit covariance is unavailable"
        raise ValueError(error_message)
    if not np.all(np.isfinite(result.covar)):
        error_message = "fit covariance is non-finite"
        raise ValueError(error_message)
    if not all(
        math.isfinite(value)
        for value in (a, b, c, float(result.chisqr), float(result.redchi))
    ):
        error_message = "fit parameters or residual are non-finite"
        raise ValueError(error_message)
    reason = calibration_parameter_reason(name, a, b, c)
    if reason is not None:
        raise ValueError(reason)

    probe = np.linspace(0.0, float(np.max(duty)), 256)
    predicted = _evaluate(name, probe, a, b, c)
    differences = np.diff(predicted)
    if (
        not np.all(np.isfinite(predicted))
        or np.any(differences < -1e-12)
        or not np.any(differences > 0.0)
    ):
        error_message = "fitted flow is not finite and monotone increasing"
        raise ValueError(error_message)

    syy = float(np.sum((flow - np.mean(flow)) ** 2))
    r2 = 0.0 if syy == 0.0 else 1.0 - float(result.chisqr) / syy
    if not math.isfinite(r2):
        error_message = "fit quality is non-finite"
        raise ValueError(error_message)
    sse = max(float(result.chisqr), np.finfo(float).tiny)
    aic = float(result.ndata * math.log(sse / result.ndata) + 2 * result.nvarys)
    return _Candidate(name, result, a, b, c, r2, aic)
