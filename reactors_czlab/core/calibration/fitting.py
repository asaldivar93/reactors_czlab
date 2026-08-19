"""Fit and validate linear or power-law pump calibrations."""

from __future__ import annotations

import math

import numpy as np
from lmfit.models import LinearModel, PowerLawModel
from scipy.stats import t as student_t

from reactors_czlab.core.calibration.model import CalibrationFit, _Candidate
from reactors_czlab.core.data import MAX_OUTPUT

#: Fewest distinct duty points a fit will accept.
MIN_POINTS = 4

#: Largest accepted 95% prediction half-width as a fraction of fitted flow.
MAX_RELATIVE_UNCERTAINTY = 0.20

#: Number of samples persisted for calibration plotting.
PLOT_SAMPLES = 128


def _evaluate(candidate: _Candidate, duty: np.ndarray) -> np.ndarray:
    """Evaluate a fit candidate on the supplied duty values."""
    if candidate.name == "linear":
        return candidate.a * duty + candidate.b
    return candidate.a * duty**candidate.b


def _prediction_band(
    candidate: _Candidate,
    duty: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return fitted flow and a two-sided 95% prediction band.

    LMFit supplies the fitted parameters and scaled covariance. The Jacobian
    propagates that covariance to the model mean; adding residual variance
    makes this a prediction interval for a future measurement.
    """
    fitted = _evaluate(candidate, duty)
    covariance = np.asarray(candidate.result.covar, dtype=float)
    if candidate.name == "linear":
        jacobian = np.column_stack((duty, np.ones_like(duty)))
    else:
        with np.errstate(divide="ignore", invalid="ignore"):
            first = duty**candidate.b
            second = candidate.a * first * np.log(duty)
        second = np.where(duty == 0.0, 0.0, second)
        jacobian = np.column_stack((first, second))
    mean_variance = np.einsum(
        "ij,jk,ik->i",
        jacobian,
        covariance,
        jacobian,
    )
    residual_variance = max(0.0, float(candidate.result.redchi))
    dof = max(1, int(candidate.result.ndata - candidate.result.nvarys))
    multiplier = float(student_t.ppf(0.975, dof))
    half_width = multiplier * np.sqrt(
        np.maximum(0.0, mean_variance + residual_variance),
    )
    return fitted, fitted - half_width, fitted + half_width


def fit_models(points: list[tuple[float, float]]) -> CalibrationFit:
    """Fit linear and power-law models and select the safest best fit.

    Parameters
    ----------
    points:
        Measured ``(duty, flow)`` pairs.

    Returns
    -------
    CalibrationFit
        The candidate with the lowest unweighted chi-square, with linear
        winning an effective tie.

    Raises
    ------
    ValueError
        If fewer than ``MIN_POINTS`` distinct duty values were measured or
        neither model has finite, monotonic parameters and a usable 95%
        prediction band.

    """
    distinct = len({duty for duty, _ in points})
    if distinct < MIN_POINTS:
        error_message = (
            f"need at least {MIN_POINTS} distinct duty points, got "
            f"{distinct} from {len(points)} measurements"
        )
        raise ValueError(error_message)
    if any(
        not math.isfinite(value)
        for point in points
        for value in point
    ):
        error_message = "calibration points must contain only finite numbers"
        raise ValueError(error_message)
    if any(
        not 0.0 <= point[0] <= MAX_OUTPUT or point[1] < 0.0
        for point in points
    ):
        error_message = (
            f"calibration points require duty within 0 - {MAX_OUTPUT:.0f} "
            "and non-negative flow"
        )
        raise ValueError(error_message)

    ordered = sorted(points)
    duty = np.asarray([point[0] for point in ordered], dtype=float)
    flow = np.asarray([point[1] for point in ordered], dtype=float)
    candidates: list[_Candidate] = []
    failures: list[str] = []
    for name in ("linear", "power"):
        try:
            candidates.append(_fit_candidate(name, duty, flow))
        except Exception as exc:  # noqa: BLE001 - third-party fit boundary
            failures.append(f"{name}: {exc}")
    if not candidates:
        error_message = "no installable calibration model: " + "; ".join(failures)
        raise ValueError(error_message)

    candidates.sort(key=lambda item: (float(item.result.chisqr), item.name != "linear"))
    selected = candidates[0]
    if len(candidates) > 1 and math.isclose(
        float(candidates[0].result.chisqr),
        float(candidates[1].result.chisqr),
        rel_tol=1e-9,
        abs_tol=1e-12,
    ):
        selected = next(item for item in candidates if item.name == "linear")

    highest_measured = float(np.max(duty))
    integer_duties = np.arange(0.0, math.floor(highest_measured) + 1.0)
    fitted, lower, upper = _prediction_band(selected, integer_duties)
    qualified = (
        np.isfinite(fitted)
        & np.isfinite(lower)
        & np.isfinite(upper)
        & (lower > 0.0)
        & (((upper - lower) / 2.0) <= MAX_RELATIVE_UNCERTAINTY * fitted)
    )
    if not np.any(qualified):
        error_message = (
            "neither model has a duty with a positive 95% lower prediction "
            "bound and at most 20% relative uncertainty"
        )
        raise ValueError(error_message)
    max_duty = float(integer_duties[np.flatnonzero(qualified)[-1]])

    plot_duties = np.linspace(0.0, highest_measured, PLOT_SAMPLES)
    if not np.any(plot_duties == max_duty):
        plot_duties = np.sort(np.append(plot_duties, max_duty))
    plot_fitted, plot_lower, plot_upper = _prediction_band(selected, plot_duties)
    if not all(
        np.all(np.isfinite(values))
        for values in (plot_fitted, plot_lower, plot_upper)
    ):
        error_message = f"{selected.name} prediction band is non-finite"
        raise ValueError(error_message)
    fit_points = [
        tuple(float(value) for value in sample)
        for sample in zip(
            plot_duties,
            plot_fitted,
            plot_lower,
            plot_upper,
            strict=True,
        )
    ]
    return CalibrationFit(
        model=selected.name,
        a=selected.a,
        b=selected.b,
        r2=selected.r2,
        residual=float(selected.result.chisqr),
        max_duty=max_duty,
        fit_points=fit_points,
    )


def _fit_candidate(
    name: str,
    duty: np.ndarray,
    flow: np.ndarray,
) -> _Candidate:
    """Fit and validate one named LMFit model."""
    if name == "linear":
        model = LinearModel()
        result = model.fit(flow, model.guess(flow, x=duty), x=duty)
        a = float(result.params["slope"].value)
        b = float(result.params["intercept"].value)
    else:
        model = PowerLawModel()
        positive_duty = duty[duty > 0]
        positive_flow = flow[duty > 0]
        if positive_duty.size < MIN_POINTS or np.any(positive_flow < 0):
            error_message = "power fit needs four non-negative flows at positive duties"
            raise ValueError(error_message)
        initial_a = max(
            np.finfo(float).tiny,
            float(positive_flow[-1] / positive_duty[-1]),
        )
        params = model.make_params(amplitude=initial_a, exponent=1.0)
        result = model.fit(flow, params, x=duty)
        a = float(result.params["amplitude"].value)
        b = float(result.params["exponent"].value)

    if not result.success:
        error_message = result.message or "optimizer did not converge"
        raise ValueError(error_message)
    if result.covar is None or np.shape(result.covar) != (2, 2):
        error_message = "fit covariance is unavailable"
        raise ValueError(error_message)
    if not np.all(np.isfinite(result.covar)):
        error_message = "fit covariance is non-finite"
        raise ValueError(error_message)
    if not all(math.isfinite(value) for value in (a, b, float(result.chisqr), float(result.redchi))):
        error_message = "fit parameters or residual are non-finite"
        raise ValueError(error_message)
    if a <= 0 or (name == "power" and b <= 0):
        error_message = "model coefficients do not define increasing positive flow"
        raise ValueError(error_message)
    probe = np.linspace(max(0.0, float(np.min(duty))), float(np.max(duty)), 256)
    predicted = a * probe + b if name == "linear" else a * probe**b
    if not np.all(np.isfinite(predicted)) or np.any(np.diff(predicted) <= 0):
        error_message = "fitted flow is not finite and strictly increasing"
        raise ValueError(error_message)
    syy = float(np.sum((flow - np.mean(flow)) ** 2))
    r2 = 0.0 if syy == 0.0 else 1.0 - float(result.chisqr) / syy
    if not math.isfinite(r2):
        error_message = "fit quality is non-finite"
        raise ValueError(error_message)
    return _Candidate(name, result, a, b, r2)
