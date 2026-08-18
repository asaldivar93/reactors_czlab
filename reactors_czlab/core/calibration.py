"""Fit, store and reload linear or power-law pump calibrations."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from os import environ
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

import numpy as np
from lmfit.model import ModelResult
from lmfit.models import LinearModel, PowerLawModel
from scipy.stats import t as student_t

from reactors_czlab.core.data import MAX_OUTPUT, MIN_DISPENSE_FLOW, Calibration

if TYPE_CHECKING:
    from reactors_czlab.core.actuator import Actuator
    from reactors_czlab.core.data import Channel

_logger = logging.getLogger("server.calibration")

#: Environment variable overriding where calibrations are stored.
CALIBRATION_ENV = "REACTORS_CALIBRATION_DIR"

#: Fewest distinct duty points a fit will accept.
MIN_POINTS = 4

#: Largest accepted 95% prediction half-width as a fraction of fitted flow.
MAX_RELATIVE_UNCERTAINTY = 0.20

#: Number of samples persisted for calibration plotting.
PLOT_SAMPLES = 128

#: A calibration point shorter than this cannot be measured accurately.
MIN_RUN_SECONDS = 1.0

#: Upper bound, so a mistyped duration cannot run a pump dry.
MAX_RUN_SECONDS = 600.0


def calibration_dir() -> Path:
    """Directory holding the calibration files, created if missing."""
    override = environ.get(CALIBRATION_ENV)
    path = (
        Path(override)
        if override
        else Path.home() / ".reactors_czlab" / "calibrations"
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def calibration_path(name: str) -> Path:
    """Path of the calibration file for ``name``."""
    return calibration_dir() / f"{name}.json"


@dataclass(frozen=True)
class CalibrationFit:
    """Selected LMFit result and its persisted uncertainty samples."""

    model: str
    a: float
    b: float
    r2: float
    residual: float
    max_duty: float
    fit_points: list[tuple[float, float, float, float]]


@dataclass(frozen=True)
class _Candidate:
    """One valid fitted model before cross-model selection."""

    name: str
    result: ModelResult
    a: float
    b: float
    r2: float

    def evaluate(self, duty: np.ndarray) -> np.ndarray:
        """Evaluate this candidate on ``duty``."""
        if self.name == "linear":
            return self.a * duty + self.b
        return self.a * duty**self.b

    def prediction_band(
        self,
        duty: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return fitted flow and a two-sided 95% prediction band.

        LMFit supplies the fitted parameters and scaled covariance. The
        Jacobian propagates that covariance to the model mean; adding the
        residual variance makes this a prediction interval for a future
        measurement rather than only a confidence interval for the mean.
        """
        fitted = self.evaluate(duty)
        covariance = np.asarray(self.result.covar, dtype=float)
        if self.name == "linear":
            jacobian = np.column_stack((duty, np.ones_like(duty)))
        else:
            with np.errstate(divide="ignore", invalid="ignore"):
                first = duty**self.b
                second = self.a * first * np.log(duty)
            # The power model is exactly zero at duty 0. Its exponent
            # derivative has the limiting value 0 there.
            second = np.where(duty == 0.0, 0.0, second)
            jacobian = np.column_stack((first, second))
        mean_variance = np.einsum(
            "ij,jk,ik->i",
            jacobian,
            covariance,
            jacobian,
        )
        residual_variance = max(0.0, float(self.result.redchi))
        dof = max(1, int(self.result.ndata - self.result.nvarys))
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
    fitted, lower, upper = selected.prediction_band(integer_duties)
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
    plot_fitted, plot_lower, plot_upper = selected.prediction_band(plot_duties)
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


def save_calibration(cal: Calibration) -> None:
    """Write a calibration to its file atomically.

    The temp-file-then-replace dance means a power cut during the write
    leaves either the old calibration or the new one, never a half file.
    """
    path = calibration_path(cal.file)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(asdict(cal), indent=2, allow_nan=False),
        encoding="utf-8",
    )
    tmp.replace(path)
    _logger.info(
        "Saved %s calibration %s: a=%s b=%s residual=%s",
        cal.model,
        cal.file,
        cal.a,
        cal.b,
        cal.residual,
    )


def load_calibration(name: str) -> Calibration | None:
    """Read a stored calibration.

    Every step - resolving and creating the calibration directory,
    reading the file, parsing its JSON, and coercing its fields - is
    covered by the same guard, because any of them can fail on a
    hand-edited file or an unfriendly filesystem and none of them may
    take the server down.

    The guard is a bare ``except Exception``, deliberately. This function
    reads a file an operator can hand-edit into anything: earlier passes
    only anticipated ``OSError``/``ValueError``/``TypeError`` and still
    missed ``OverflowError`` from ``float()`` on an oversized JSON
    integer. Enumerating every exception type ``json.loads``,
    ``Calibration(**raw)``, ``float()`` and tuple-unpacking can raise on
    adversarial input is not a task with a stable finish line, and the
    function's one contract - log and return ``None`` - does not depend
    on which exception type it was. ``BaseException`` subclasses that are
    not ``Exception`` (``KeyboardInterrupt``, ``SystemExit``) still
    propagate, which is correct: those are not data problems.

    Returns
    -------
    Calibration or None
        ``None`` when the calibration directory cannot be created, the
        file is absent, unreadable, malformed, has a field of the wrong
        type or value, or holds a line that cannot be inverted. Every
        one of those is logged and left for the operator.

    """
    try:
        path = calibration_path(name)
        if not path.exists():
            _logger.warning("No stored calibration for %s at %s", name, path)
            return None

        raw = json.loads(path.read_text(encoding="utf-8"))
        cal = Calibration(**raw)
        cal.a = float(cal.a)
        cal.b = float(cal.b)
        cal.min_duty = float(cal.min_duty)
        cal.max_duty = float(cal.max_duty)
        cal.dispense_duty = float(cal.dispense_duty)
        cal.r2 = float(cal.r2)
        if cal.residual is not None:
            cal.residual = float(cal.residual)
        cal.points = [(float(d), float(f)) for d, f in cal.points]
        cal.fit_points = [
            tuple(float(value) for value in point)
            for point in cal.fit_points
        ]
    except Exception:  # see docstring: this function must never raise
        _logger.exception("Unreadable calibration file for %s", name)
        return None

    reason = cal.installable_reason()
    if reason is not None:
        _logger.warning(
            "Calibration %s is not installable: %s",
            name,
            reason,
        )
        return None
    return cal


def replacement_reason(
    current: Calibration,
    stored: Calibration,
) -> str | None:
    """Why ``stored`` may not replace ``current`` on a channel.

    A different question from ``Calibration.installable_reason()``, and
    deliberately kept out of it. That one asks *is this calibration safe
    to drive a pump with at all*, judges nothing but the numbers, and is
    the single authority every install site defers to. This one asks
    *may this replace what is already there*, which only has an answer
    relative to the calibration being replaced - so it lives at the two
    sites that replace one, ``load_into()`` and ``CalibrationRun.reload()``,
    and is written once here so those two cannot drift apart.

    The one case it refuses: an unfitted line landing on top of a fitted
    one. ``Dispenser._accrue()`` is the only consumer that gates on
    ``fitted_at`` - every other one judges the numbers - so an unfitted
    calibration leaves the pump dosing exactly as before while
    ``total_volume`` silently stops counting. The gate cannot simply be
    dropped from ``_accrue`` instead: the placeholder ``server_info.py``
    builds for an uncalibrated pump is ``a=1.0, b=0``, which would report
    ``flow_at(2000) = 2000`` mL/min.

    Returns
    -------
    str or None
        ``None`` when the replacement is allowed, otherwise a
        human-readable reason, safe to show the operator verbatim.

    """
    if current.is_fitted and not stored.is_fitted:
        return (
            f"the stored calibration for {current.file} has never been "
            "fitted, and a fitted one is installed; installing it would "
            "leave the pump dosing while the delivered volume stopped "
            "being counted"
        )
    return None


def load_into(channel: Channel) -> bool:
    """Install the stored calibration for ``channel``, if there is one.

    Returns
    -------
    bool
        True when a stored calibration replaced the channel's placeholder.
        False when there was nothing stored, or what was stored is not
        safe to install - either way the channel keeps its previous
        (placeholder or otherwise) calibration.

    """
    if channel.calibration is None:
        return False

    stored = load_calibration(channel.calibration.file)
    if stored is None:
        return False

    reason = stored.installable_reason() or replacement_reason(
        channel.calibration,
        stored,
    )
    if reason is not None:
        _logger.warning(
            "Stored calibration for %s is unusable, keeping the "
            "existing one: %s",
            channel.calibration.file,
            reason,
        )
        return False

    channel.calibration = stored
    return True


class CalibrationRun:
    """Collect calibration points from one actuator and fit them.

    Every method returns a status string: the operator drives this from a
    generic OPC client and reads the result straight off the method call.
    """

    def __init__(
        self,
        actuator: Actuator,
        clock: Callable[[], float] = perf_counter,
        sleep: Callable[[float], object] = asyncio.sleep,
    ) -> None:
        """Attach a run to an actuator.

        Parameters
        ----------
        actuator:
            The pump being calibrated.
        clock, sleep:
            Injectable so the tests neither wait nor guess at drift.

        """
        self.actuator = actuator
        self.points: list[tuple[float, float]] = []
        # Public so a test - or a bench script - can swap them after
        # construction; ActuatorOpc builds the run itself.
        self.clock = clock
        self.sleep = sleep

        self._pending: tuple[float, float] | None = None
        self._running = False

    def __repr__(self) -> str:
        """Print the actuator and how many points are collected."""
        return f"CalibrationRun({self.actuator.id}, {len(self.points)} points)"

    @property
    def is_running(self) -> bool:
        """Whether the pump is running for a calibration point right now.

        Read-only view of the flag ``calibrate_point`` sets, so a user
        interface can disable the controls a run must not race with
        without reaching into the run's internals.
        """
        return self._running

    @property
    def pending(self) -> tuple[float, float] | None:
        """The ``(duty, seconds)`` awaiting a measured volume, if any.

        ``record_point`` is the only useful thing to do while this is
        set, and a refused measurement deliberately leaves it in place
        so the operator can retype without re-running the pump.
        """
        return self._pending

    async def calibrate_point(self, duty: float, seconds: float) -> str:
        """Run the pump at ``duty`` for ``seconds``, then stop it.

        The elapsed time is measured rather than assumed: ``asyncio.sleep``
        overshoots, and that overshoot would go straight into the flow.
        """
        if self._running:
            return f"{self.actuator.id} is already calibrating"
        if self.actuator.autotune_owner is not None:
            return f"{self.actuator.id} is owned by an active autotune"
        if not 0 <= duty <= MAX_OUTPUT:
            return f"duty must be within 0 - {MAX_OUTPUT}, got {duty}"
        if not MIN_RUN_SECONDS <= seconds <= MAX_RUN_SECONDS:
            return (
                f"seconds must be within {MIN_RUN_SECONDS} - "
                f"{MAX_RUN_SECONDS}, got {seconds}"
            )

        self._running = True
        self.actuator.calibrating = True
        start = self.clock()
        try:
            self.actuator.write(duty)
            await self.sleep(seconds)
        finally:
            elapsed = self.clock() - start
            self.actuator.write(0)
            # write() bypasses the change guard, so put old_value back in
            # step or the control loop will not rewrite the same value.
            self.actuator.channel.old_value = 0
            self.actuator.calibrating = False
            self._running = False

        self._pending = (duty, elapsed)
        _logger.info(
            "Calibration point on %s: duty %s for %.3fs",
            self.actuator.id,
            duty,
            elapsed,
        )
        return (
            f"ran duty {duty} for {elapsed:.3f}s - now record the measured "
            "volume in mL"
        )

    def record_point(self, volume_ml: float) -> str:
        """Attach the operator's measured volume to the last run.

        The volume arrives from a generic OPC client as a ``Float``, so
        it can be ``inf`` or ``nan``, and neither is caught downstream:
        the model fitter's parameter checks and every branch of
        ``Calibration.installable_reason()`` are comparisons, and a
        comparison against ``nan`` is false. A single bad argument
        therefore used to reach the calibration file and reload from it
        at every boot. Zero is accepted - a point that measured no
        volume is direct evidence of the stall floor, which
        ``_stall_floor`` reads.

        The derived flow is checked as well as the argument: a finite
        but enormous volume over the shortest allowed run still
        overflows to ``inf`` in the division.

        A refused measurement leaves the pending point in place, so the
        operator can retype the number without re-running the pump.
        """
        if self._pending is None:
            return "no point is waiting for a measurement"
        if not 0 <= volume_ml < float("inf"):
            return (
                "volume must be a finite number of mL, zero or more, "
                f"got {volume_ml}"
            )

        duty, elapsed = self._pending
        flow = volume_ml / (elapsed / 60.0)
        if not 0 <= flow < float("inf"):
            return (
                f"volume {volume_ml} mL over {elapsed:.3f}s is not a "
                "flow that can be represented; measure a smaller volume"
            )

        self._pending = None
        self.points.append((duty, flow))
        return (
            f"duty {duty} -> {flow:.4f} mL/min "
            f"({len(self.points)} points collected)"
        )

    def fit(self) -> str:
        """Fit, store and install the collected points."""
        current = self.actuator.channel.calibration
        if current is None:
            return (
                f"{self.actuator.id} has no calibration slot on its "
                "channel; give it one in server_info.py"
            )

        try:
            fitted = fit_models(self.points)
        except ValueError as exc:
            _logger.warning("Fit refused for %s: %s", self.actuator.id, exc)
            return str(exc)

        min_duty = self._stall_floor(
            fitted.model,
            fitted.a,
            fitted.b,
            fitted.max_duty,
        )
        dispense_duty = current.dispense_duty
        if not (
            min_duty <= dispense_duty <= fitted.max_duty
            and self._flow_at(
                fitted.model,
                fitted.a,
                fitted.b,
                dispense_duty,
            )
            >= MIN_DISPENSE_FLOW
        ):
            dispense_duty = fitted.max_duty
        cal = Calibration(
            file=current.file,
            a=fitted.a,
            b=fitted.b,
            min_duty=min_duty,
            max_duty=fitted.max_duty,
            dispense_duty=dispense_duty,
            points=list(self.points),
            fitted_at=datetime.now(UTC).isoformat(),
            r2=fitted.r2,
            model=fitted.model,
            residual=fitted.residual,
            fit_points=fitted.fit_points,
        )
        reason = cal.installable_reason()
        if reason is not None:
            _logger.warning("Fit refused for %s: %s", self.actuator.id, reason)
            return f"{reason} - keeping the old calibration"

        save_calibration(cal)
        self.actuator.channel.calibration = cal
        self.actuator.refresh_controller_limits()
        return (
            f"fitted flow using {cal.model} model: {self._equation(cal)} "
            f"(r2 {cal.r2:.4f}, residual {cal.residual:.6g}), stall floor "
            f"{cal.min_duty:.0f}, qualified max duty {cal.max_duty:.0f}"
        )

    def clear_points(self) -> str:
        """Throw the collected points away, keeping the installed line."""
        self.points = []
        self._pending = None
        return f"cleared the collected points for {self.actuator.id}"

    def reload(self) -> str:
        """Re-read the stored calibration from disk."""
        current = self.actuator.channel.calibration
        if current is None:
            return f"{self.actuator.id} has no calibration slot on its channel"

        stored = load_calibration(current.file)
        if stored is None:
            return f"no usable stored calibration for {current.file}"

        # A calibration file is operator-editable, and load_calibration()
        # only checks the slope's sign. installable_reason() is the one
        # place that decides whether the rest of the numbers are safe to
        # drive a pump with - not gated on is_fitted, since a hand-edited
        # file can set fitted_at to "" while leaving dangerous numbers in
        # the rest of the fields. replacement_reason() then asks the one
        # question that does depend on fitted_at, and that
        # installable_reason() cannot answer because it never sees what
        # is being replaced.
        reason = stored.installable_reason() or replacement_reason(
            current,
            stored,
        )
        if reason is not None:
            return (
                f"stored calibration for {current.file} is unusable: "
                f"{reason} - not installing it"
            )

        self.actuator.channel.calibration = stored
        self.actuator.refresh_controller_limits()
        return f"reloaded {current.file}, fitted at {stored.fitted_at}"

    def set_duties(self, min_duty: float, dispense_duty: float) -> str:
        """Adjust the stall floor and the volume-dose duty without a refit."""
        cal = self.actuator.channel.calibration
        if cal is None:
            return f"{self.actuator.id} has no calibration slot on its channel"
        if not 0 <= min_duty <= MAX_OUTPUT:
            return f"min duty must be within 0 - {MAX_OUTPUT}, got {min_duty}"
        if not 0 <= dispense_duty <= MAX_OUTPUT:
            return f"dispense duty must be within 0 - {MAX_OUTPUT}"

        # Validate a candidate rather than the live object, so a refused
        # change cannot leave the channel's calibration half-updated.
        candidate = replace(
            cal,
            min_duty=min_duty,
            dispense_duty=dispense_duty,
        )
        reason = candidate.installable_reason()
        if reason is not None:
            return reason

        cal.min_duty = min_duty
        cal.dispense_duty = dispense_duty
        save_calibration(cal)
        self.actuator.refresh_controller_limits()
        return f"min duty {min_duty}, dispense duty {dispense_duty}"

    def _stall_floor(
        self,
        model: str,
        a: float,
        b: float,
        max_duty: float,
    ) -> float:
        """Lowest duty the pump is believed to actually turn at.

        The fitted x-intercept is the estimate; a point that measured no
        volume at all is direct evidence and overrides it. Either one
        is capped at ``max_duty`` - a zero-flow reading taken above the
        pump's own ceiling is still evidence the pump does not turn
        there, but adopting it verbatim could push the floor past the
        ceiling and invert the usable band.
        """
        floor = max(0.0, -b / a) if model == "linear" else 0.0
        measured = [duty for duty, flow in self.points if flow <= 0]
        if measured:
            floor = max(floor, max(measured))
        return min(floor, max_duty)

    @staticmethod
    def _flow_at(model: str, a: float, b: float, duty: float) -> float:
        """Evaluate fitted parameters before a Calibration exists."""
        return a * duty + b if model == "linear" else a * duty**b

    @staticmethod
    def _equation(calibration: Calibration) -> str:
        """Human-readable equation for a fitted calibration."""
        if calibration.model == "linear":
            return f"flow = {calibration.a:.6g} * duty + {calibration.b:.6g}"
        return f"flow = {calibration.a:.6g} * duty ** {calibration.b:.6g}"
