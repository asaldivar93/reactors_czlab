"""Live, non-blocking pH relay-autotuning workflow."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum, auto
from time import perf_counter
from typing import Protocol

import numpy as np

from reactors_czlab.autotune.model import Chemistry, state_from_ph
from reactors_czlab.autotune.relay import RelayIdentification, RelayTuneConfig
from reactors_czlab.core.data import ERROR_VALUE, ControlMethod, OutputUnit

# Live, non-blocking workflow -------------------------------------------------

ACTUATOR_TICK_SECONDS = 0.05
MAX_STATUS_SAMPLES = 240
MAX_STATUS_CYCLES = 12


class _SensorLike(Protocol):
    """The sensor surface the core workflow needs from a reactor context."""

    id: str
    channels: Sequence[object]


class _ActuatorLike(Protocol):
    """The actuator surface used without importing OPC or Reactor."""

    id: str
    channel: object
    controller: object
    dispenser: object
    control_period: float
    calibrating: bool
    autotune_owner: object | None

    def claim_autotune(self, owner: object) -> None: ...

    def autotune_demand(self, owner: object, volume_ml: float) -> None: ...

    def autotune_tick(self, owner: object) -> None: ...

    def release_autotune(self, owner: object) -> None: ...


@dataclass(frozen=True)
class AutotuneContext:
    """Reactor state injected into the OPC-independent autotune core.

    ``pairings`` is deliberately a callable. Stage 4 can point it at the
    reactor's live pairing table, and tests can mutate a plain dictionary;
    either way each sample revalidates the current configuration.
    """

    reactor_id: str
    volume_l: float
    sensors: Mapping[str, _SensorLike]
    actuators: Mapping[str, _ActuatorLike]
    pairings: Callable[[], Mapping[str, Sequence[tuple[str, int]]]]


class AutotunePhase(StrEnum):
    """Externally visible phases of a live autotune run."""

    idle = auto()
    baseline = auto()
    adapting = auto()
    settling = auto()
    collecting = auto()
    identified = auto()
    aborted = auto()
    failed = auto()


TERMINAL_PHASES = frozenset(
    {
        AutotunePhase.identified,
        AutotunePhase.aborted,
        AutotunePhase.failed,
    },
)


@dataclass(frozen=True)
class AutotuneSample:
    """One bounded trace point suitable for later JSON serialization."""

    timestamp: float
    ph: float
    requested_volume_ml: float
    actual_dose_ml: float


@dataclass(frozen=True)
class CycleSummary:
    """Measurements and actual delivery accounting for one full cycle."""

    started_at: float
    ended_at: float
    period: float
    peak_ph: float
    trough_ph: float
    amplitude: float
    base_half_seconds: float
    acid_half_seconds: float
    half_cycle_ratio: float
    requested_base_ml: float
    requested_acid_ml: float
    actual_base_ml: float
    actual_acid_ml: float
    base_requests: int
    acid_requests: int


@dataclass(frozen=True)
class AutotuneResult:
    """Successful relay identification plus the clean cycles used."""

    identification: RelayIdentification
    noise_sigma: float
    base_dose_ml: float
    acid_dose_ml: float
    actual_dose_ml: float
    cycles: tuple[CycleSummary, ...]


@dataclass(frozen=True)
class AutotuneStatus:
    """Bounded structured snapshot returned to later OPC/GUI layers."""

    version: int
    phase: AutotunePhase
    message: str
    elapsed_seconds: float
    safe_low: float | None
    safe_high: float | None
    dose_budget_ml: float | None
    actual_dose_ml: float
    base_dose_ml: float
    acid_dose_ml: float
    noise_sigma: float | None
    settling_cycles: int
    clean_cycles: int
    warnings: tuple[str, ...]
    trace: tuple[AutotuneSample, ...]
    switch_times: tuple[float, ...]
    cycles: tuple[CycleSummary, ...]
    result: AutotuneResult | None


@dataclass(frozen=True)
class AutotunePreflight:
    """Validated values and warnings produced before ownership is taken."""

    safe_low: float
    safe_high: float
    default_dose_budget_ml: float
    dose_budget_ml: float
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _Switch:
    timestamp: float
    sample_index: int
    high: bool
    base_total: float
    acid_total: float


def robust_noise_sigma(
    timestamps: Sequence[float],
    values: Sequence[float],
) -> float:
    """Estimate noise after robustly removing a linear baseline trend.

    A Theil-Sen-style median pairwise slope resists both drift and a small
    number of probe spikes. Sigma is the Gaussian-consistent MAD of the
    residuals.
    """
    time = np.asarray(timestamps, dtype=float)
    ph = np.asarray(values, dtype=float)
    if time.ndim != 1 or ph.ndim != 1 or time.size != ph.size or time.size < 2:
        error_message = "baseline needs equal-length timestamps and pH values"
        raise ValueError(error_message)
    if not np.all(np.isfinite(time)) or not np.all(np.isfinite(ph)):
        error_message = "baseline timestamps and pH values must be finite"
        raise ValueError(error_message)
    deltas_t = time[np.newaxis, :] - time[:, np.newaxis]
    deltas_y = ph[np.newaxis, :] - ph[:, np.newaxis]
    upper = np.triu(np.ones_like(deltas_t, dtype=bool), k=1)
    valid = upper & (deltas_t != 0)
    slope = float(np.median(deltas_y[valid] / deltas_t[valid]))
    intercept = float(np.median(ph - slope * time))
    residual = ph - (intercept + slope * time)
    median = float(np.median(residual))
    return float(1.4826 * np.median(np.abs(residual - median)))


def default_dose_budget_ml(
    volume_l: float,
    phosphate_molar: float,
    setpoint: float,
    base_molar: float,
    acid_molar: float,
    chemistry: Chemistry | None = None,
) -> float:
    """Return one safe-band traversal in each direction, in total mL."""
    numbers = (volume_l, phosphate_molar, setpoint, base_molar, acid_molar)
    if not all(math.isfinite(value) for value in numbers):
        error_message = "chemistry, volume, and setpoint values must be finite"
        raise ValueError(error_message)
    if volume_l <= 0 or phosphate_molar <= 0 or base_molar <= 0 or acid_molar <= 0:
        error_message = "volume, phosphate, base molarity, and acid molarity must be positive"
        raise ValueError(error_message)
    safe_low = max(4.0, setpoint - 1.0)
    safe_high = min(10.0, setpoint + 1.0)
    if not safe_low < setpoint < safe_high:
        error_message = "setpoint must be strictly between pH 4 and pH 10"
        raise ValueError(error_message)
    chem = chemistry or Chemistry()
    state = state_from_ph(setpoint, phosphate_molar, chem)
    base_moles = volume_l * (
        state_from_ph(safe_high, phosphate_molar, chem) - state
    )
    acid_moles = volume_l * (
        state - state_from_ph(safe_low, phosphate_molar, chem)
    )
    return 1000.0 * (base_moles / base_molar + acid_moles / acid_molar)


def cycle_quality_reason(
    cycle: CycleSummary,
    hysteresis: float,
    noise_sigma: float,
) -> str | None:
    """Return why one collection cycle is unusable for identification."""
    values = (
        cycle.period,
        cycle.amplitude,
        cycle.half_cycle_ratio,
        cycle.actual_base_ml,
        cycle.actual_acid_ml,
    )
    if not all(math.isfinite(value) for value in values):
        return "relay produced a non-finite cycle"
    if cycle.amplitude <= hysteresis or cycle.amplitude <= 3.0 * noise_sigma:
        return "relay cycle amplitude did not clear hysteresis and noise"
    if not 0.2 <= cycle.half_cycle_ratio <= 5.0:
        return "base/acid half-cycle asymmetry is outside [0.2, 5]"
    return None


def period_quality_reason(cycles: Sequence[CycleSummary]) -> str | None:
    """Return why the collected full-cycle periods are not repeatable."""
    periods = np.asarray([cycle.period for cycle in cycles], dtype=float)
    if periods.size == 0 or not np.all(np.isfinite(periods)):
        return "relay periods are missing or non-finite"
    mean_period = float(np.mean(periods))
    if mean_period <= 0 or np.max(np.abs(periods - mean_period)) / mean_period > 0.25:
        return "relay period variation exceeds 25%"
    return None


class AutotuneRun:
    """Non-blocking, sample-driven relay workflow for one pH pump pair."""

    def __init__(
        self,
        context: AutotuneContext,
        sensor_id: str,
        base_id: str,
        acid_id: str,
        config: RelayTuneConfig | None = None,
        *,
        clock: Callable[[], float] = perf_counter,
        terminal_callback: Callable[[AutotuneRun], str | None] | None = None,
    ) -> None:
        """Create an idle run; :meth:`start` performs preflight and claims pumps."""
        self.context = context
        self.sensor_id = sensor_id
        self.base_id = base_id
        self.acid_id = acid_id
        self.config = config or RelayTuneConfig()
        self.clock = clock
        self._terminal_callback = terminal_callback
        self.audit_id: str | None = None
        self.phase = AutotunePhase.idle
        self.message = "idle"
        self.result: AutotuneResult | None = None
        self.warnings: tuple[str, ...] = ()
        self.noise_sigma: float | None = None
        self.safe_low: float | None = None
        self.safe_high: float | None = None
        self.dose_budget_ml: float | None = None
        self.started_at: float | None = None
        self.ended_at: float | None = None
        self.base_dose_ml = float(self.config.base_dose_ml)
        self.acid_dose_ml = float(self.config.acid_dose_ml)
        self.samples: list[AutotuneSample] = []
        self.switch_times: list[float] = []
        self.cycles: list[CycleSummary] = []
        self.cycle_history: list[CycleSummary] = []
        self._baseline: list[tuple[float, float]] = []
        self._switches: list[_Switch] = []
        self._relay_high = True
        self._settling_seen = 0
        self._adaptations = 0
        self._claimed: list[_ActuatorLike] = []
        self._start_base_total = 0.0
        self._start_acid_total = 0.0

    @property
    def is_active(self) -> bool:
        """Whether the run currently owns the selected pumps."""
        return self.phase not in TERMINAL_PHASES and self.phase is not AutotunePhase.idle

    @property
    def base(self) -> _ActuatorLike:
        """Selected base actuator."""
        return self.context.actuators[self.base_id]

    @property
    def acid(self) -> _ActuatorLike:
        """Selected acid actuator."""
        return self.context.actuators[self.acid_id]

    @property
    def actual_dose_ml(self) -> float:
        """Actual combined volume accrued by both dispensers this run."""
        if self.started_at is None:
            return 0.0
        return max(0.0, self.base.dispenser.total_volume - self._start_base_total) + max(
            0.0,
            self.acid.dispenser.total_volume - self._start_acid_total,
        )

    def preflight(self) -> AutotunePreflight:
        """Validate selection, configuration, chemistry, and deliverability."""
        self._validate_selection()
        config = self.config
        finite_positive = {
            "base dose": config.base_dose_ml,
            "acid dose": config.acid_dose_ml,
            "hysteresis": config.hysteresis,
            "maximum minutes": config.max_minutes,
            "baseline seconds": config.baseline_seconds,
        }
        for name, value in finite_positive.items():
            if not math.isfinite(value) or value <= 0:
                error_message = f"{name} must be finite and positive"
                raise ValueError(error_message)
        if config.baseline_samples < 6 or config.baseline_seconds < 60.0:
            error_message = "baseline must last at least 60 seconds and six samples"
            raise ValueError(error_message)
        if config.clean_cycles < 4 or config.settle_cycles < 2:
            error_message = "autotune needs two settling and four clean cycles"
            raise ValueError(error_message)
        if config.max_adaptations < 1:
            error_message = "maximum adaptations must be at least one"
            raise ValueError(error_message)
        if not config.acknowledge_other_loops:
            error_message = "acknowledge that the pH excursion may affect other loops"
            raise ValueError(error_message)

        default_budget = default_dose_budget_ml(
            self.context.volume_l,
            config.phosphate_molar,
            config.setpoint,
            config.base_molar,
            config.acid_molar,
        )
        safe_low = max(4.0, config.setpoint - 1.0)
        safe_high = min(10.0, config.setpoint + 1.0)
        if config.hysteresis >= min(config.setpoint - safe_low, safe_high - config.setpoint):
            error_message = "hysteresis must fit strictly inside the effective pH safety band"
            raise ValueError(error_message)
        if config.max_minutes * 60.0 <= config.baseline_seconds:
            error_message = "time budget must be longer than the baseline"
            raise ValueError(error_message)
        budget = default_budget
        if config.dose_budget_ml is not None:
            if not math.isfinite(config.dose_budget_ml) or config.dose_budget_ml <= 0:
                error_message = "dose budget override must be finite and positive"
                raise ValueError(error_message)
            if not config.acknowledge_budget_override:
                error_message = "dose budget override requires explicit acknowledgement"
                raise ValueError(error_message)
            budget = config.dose_budget_ml
        if config.base_dose_ml + config.acid_dose_ml > budget:
            error_message = "one base/acid relay pair exceeds the combined dose budget"
            raise ValueError(error_message)

        warnings: list[str] = []
        for label, actuator, dose in (
            ("base", self.base, config.base_dose_ml),
            ("acid", self.acid, config.acid_dose_ml),
        ):
            if not math.isfinite(actuator.control_period) or actuator.control_period <= 0:
                error_message = f"{label} control period must be finite and positive"
                raise ValueError(error_message)
            flow = actuator.channel.calibration.flow_at(
                actuator.channel.calibration.dispense_duty,
            )
            minimum = flow * ACTUATOR_TICK_SECONDS / 60.0
            maximum = flow * actuator.control_period / 60.0
            if dose < minimum or dose > maximum:
                error_message = (
                    f"{label} dose {dose:.6g} mL is outside the deliverable "
                    f"range [{minimum:.6g}, {maximum:.6g}] mL"
                )
                raise ValueError(error_message)
            if actuator.control_period > 30.0:
                warnings.append(
                    f"{label} control period {actuator.control_period:.3g}s exceeds the recommended 30s",
                )
        return AutotunePreflight(
            safe_low,
            safe_high,
            default_budget,
            budget,
            tuple(warnings),
        )

    def start(self) -> AutotuneStatus:
        """Claim both pumps and enter the no-dose baseline phase."""
        if self.phase is not AutotunePhase.idle:
            error_message = "this autotune run has already been started"
            raise RuntimeError(error_message)
        flight = self.preflight()
        self.safe_low = flight.safe_low
        self.safe_high = flight.safe_high
        self.dose_budget_ml = flight.dose_budget_ml
        self.warnings = flight.warnings
        try:
            for actuator in (self.base, self.acid):
                actuator.claim_autotune(self)
                self._claimed.append(actuator)
        except Exception:
            self._cleanup_claimed()
            raise
        self._start_base_total = self.base.dispenser.total_volume
        self._start_acid_total = self.acid.dispenser.total_volume
        self.started_at = self.clock()
        self.phase = AutotunePhase.baseline
        self.message = "collecting a no-dose baseline"
        return self.status()

    def sample(self, ph: float, timestamp: float | None = None) -> AutotuneStatus:
        """Advance the workflow from one real sensor sample.

        Unexpected exceptions are converted to a failed terminal state after
        the same cleanup used by every explicit safety abort.
        """
        if not self.is_active:
            return self.status()
        now = self.clock() if timestamp is None else timestamp
        try:
            self._validate_live(now, ph)
            if not self.is_active:
                return self.status(now)
            match self.phase:
                case AutotunePhase.baseline:
                    self._advance_baseline(now, ph)
                case AutotunePhase.adapting:
                    self._begin_settling()
                    self._advance_relay(now, ph)
                case AutotunePhase.settling | AutotunePhase.collecting:
                    self._advance_relay(now, ph)
                case _:
                    error_message = f"cannot sample autotune phase {self.phase}"
                    raise RuntimeError(error_message)
        except Exception as exc:  # noqa: BLE001 - terminal safety boundary
            self._terminate(AutotunePhase.failed, f"unexpected autotune failure: {exc}", now)
        return self.status(now)

    def tick(self) -> AutotuneStatus:
        """Advance owned dose deadlines from the 20 Hz actuator loop."""
        if not self.is_active:
            return self.status()
        now = self.clock()
        try:
            self._validate_live(now, None)
            if self.is_active:
                self.base.autotune_tick(self)
                self.acid.autotune_tick(self)
                self._abort_if_dose_exhausted(now)
        except Exception as exc:  # noqa: BLE001 - terminal safety boundary
            self._terminate(AutotunePhase.failed, f"unexpected autotune failure: {exc}", now)
        return self.status(now)

    def abort(self, reason: str = "operator aborted autotune") -> AutotuneStatus:
        """Stop an active run through the common terminal cleanup."""
        if self.is_active:
            self._terminate(AutotunePhase.aborted, reason, self.clock())
        return self.status()

    def status(self, now: float | None = None) -> AutotuneStatus:
        """Return a bounded immutable snapshot of the run."""
        instant = self.clock() if now is None else now
        start = instant if self.started_at is None else self.started_at
        end = instant if self.ended_at is None else self.ended_at
        return AutotuneStatus(
            version=1,
            phase=self.phase,
            message=self.message,
            elapsed_seconds=max(0.0, end - start),
            safe_low=self.safe_low,
            safe_high=self.safe_high,
            dose_budget_ml=self.dose_budget_ml,
            actual_dose_ml=self.actual_dose_ml,
            base_dose_ml=self.base_dose_ml,
            acid_dose_ml=self.acid_dose_ml,
            noise_sigma=self.noise_sigma,
            settling_cycles=self._settling_seen,
            clean_cycles=len(self.cycles),
            warnings=self.warnings,
            trace=tuple(self.samples[-MAX_STATUS_SAMPLES:]),
            switch_times=tuple(self.switch_times[-MAX_STATUS_SAMPLES:]),
            cycles=tuple(self.cycle_history[-MAX_STATUS_CYCLES:]),
            result=self.result,
        )

    def _validate_selection(self) -> None:
        validate_autotune_selection(
            self.context,
            self.sensor_id,
            self.base_id,
            self.acid_id,
            self.config.setpoint,
        )

    def _validate_live(self, now: float, ph: float | None) -> None:
        if not math.isfinite(now):
            error_message = "non-finite monotonic timestamp"
            raise ValueError(error_message)
        if self.samples and now < self.samples[-1].timestamp:
            error_message = "sample timestamps must be monotonic"
            raise ValueError(error_message)
        if self.started_at is not None and now - self.started_at >= self.config.max_minutes * 60.0:
            self._terminate(AutotunePhase.aborted, "autotune timed out", now)
            return
        for actuator in (self.base, self.acid):
            if actuator.autotune_owner is not self or not actuator.calibrating:
                self._terminate(AutotunePhase.aborted, "autotune actuator ownership was lost", now)
                return
        try:
            self._validate_selection()
        except ValueError as exc:
            reason = str(exc)
            category = "pairing loss" if "paired" in reason else "configuration loss"
            self._terminate(AutotunePhase.aborted, f"{category}: {reason}", now)
            return
        self._abort_if_dose_exhausted(now)
        if not self.is_active or ph is None:
            return
        if ph == ERROR_VALUE:
            self._terminate(AutotunePhase.aborted, "pH sensor returned ERROR_VALUE", now)
            return
        if not math.isfinite(ph):
            self._terminate(AutotunePhase.aborted, "pH sensor returned a non-finite value", now)
            return
        outside = ph < self.safe_low or ph > self.safe_high
        previous_outside = bool(
            self.samples and (self.samples[-1].ph < self.safe_low or self.samples[-1].ph > self.safe_high),
        )
        if outside and previous_outside:
            self._terminate(AutotunePhase.aborted, "pH was outside the safety band twice", now)


    def _advance_baseline(self, now: float, ph: float) -> None:
        self.base.autotune_demand(self, 0.0)
        self.acid.autotune_demand(self, 0.0)
        self._baseline.append((now, ph))
        self._append_sample(now, ph, 0.0)
        elapsed = now - self._baseline[0][0]
        if len(self._baseline) < self.config.baseline_samples or elapsed < self.config.baseline_seconds:
            return
        self.noise_sigma = robust_noise_sigma(
            [item[0] for item in self._baseline],
            [item[1] for item in self._baseline],
        )
        if self.config.hysteresis < 2.0 * self.noise_sigma:
            self._terminate(
                AutotunePhase.failed,
                f"hysteresis {self.config.hysteresis:.6g} is below 2*sigma ({2 * self.noise_sigma:.6g})",
                now,
            )
            return
        self.phase = AutotunePhase.settling
        self.message = "discarding transient relay cycles"

    def _advance_relay(self, now: float, ph: float) -> None:
        switched = False
        if self._relay_high and ph > self.config.setpoint + self.config.hysteresis:
            self._relay_high = False
            switched = True
        elif not self._relay_high and ph < self.config.setpoint - self.config.hysteresis:
            self._relay_high = True
            switched = True
        requested = self.base_dose_ml if self._relay_high else -self.acid_dose_ml
        self.base.autotune_demand(self, max(requested, 0.0))
        self.acid.autotune_demand(self, max(-requested, 0.0))
        self._append_sample(now, ph, requested)
        if switched:
            self.switch_times.append(now)
            self._switches.append(
                _Switch(
                    now,
                    len(self.samples) - 1,
                    self._relay_high,
                    self.base.dispenser.total_volume,
                    self.acid.dispenser.total_volume,
                ),
            )
            if len(self._switches) >= 3 and (len(self._switches) - 3) % 2 == 0:
                self._complete_cycle(now)
        self._abort_if_dose_exhausted(now)

    def _append_sample(self, now: float, ph: float, requested: float) -> None:
        self.samples.append(AutotuneSample(now, ph, requested, self.actual_dose_ml))

    def _complete_cycle(self, now: float) -> None:
        first, middle, last = self._switches[-3:]
        segment = self.samples[first.sample_index : last.sample_index + 1]
        # Actual deltas are snapshotted immediately after each switch. They
        # include the demand made at ``first`` (once it subsequently runs)
        # and exclude the just-started demand at ``last``. Match request
        # counts to that same half-open interval.
        delivery_segment = self.samples[first.sample_index : last.sample_index]
        values = np.asarray([item.ph for item in segment], dtype=float)
        peak = float(np.max(values))
        trough = float(np.min(values))
        amplitude = 0.5 * (peak - trough)
        requested_base = sum(max(item.requested_volume_ml, 0.0) for item in delivery_segment)
        requested_acid = sum(max(-item.requested_volume_ml, 0.0) for item in delivery_segment)
        base_requests = sum(item.requested_volume_ml > 0 for item in delivery_segment)
        acid_requests = sum(item.requested_volume_ml < 0 for item in delivery_segment)
        first_half = middle.timestamp - first.timestamp
        second_half = last.timestamp - middle.timestamp
        if first.high:
            base_half, acid_half = first_half, second_half
        else:
            acid_half, base_half = first_half, second_half
        ratio = base_half / acid_half if acid_half > 0 else float("inf")
        cycle = CycleSummary(
            first.timestamp,
            last.timestamp,
            last.timestamp - first.timestamp,
            peak,
            trough,
            amplitude,
            base_half,
            acid_half,
            ratio,
            requested_base,
            requested_acid,
            max(0.0, last.base_total - first.base_total),
            max(0.0, last.acid_total - first.acid_total),
            base_requests,
            acid_requests,
        )
        self.cycle_history.append(cycle)
        if not all(math.isfinite(value) for value in (
            cycle.period,
            cycle.amplitude,
            cycle.half_cycle_ratio,
            cycle.actual_base_ml,
            cycle.actual_acid_ml,
        )):
            self._terminate(AutotunePhase.failed, "relay produced a non-finite cycle", now)
            return
        # Use the same adequacy boundary as cycle_quality_reason().  The
        # resize still targets 3*h below, but a cycle already clear of both
        # hysteresis and measured noise must not be enlarged merely because
        # its scientifically valid amplitude is below that conservative
        # target.
        minimum_amplitude = max(
            self.config.hysteresis,
            3.0 * (self.noise_sigma or 0.0),
        )
        if cycle.amplitude <= minimum_amplitude and self.phase is AutotunePhase.settling:
            self._adapt(cycle, now)
            return
        if self.phase is AutotunePhase.settling:
            self._settling_seen += 1
            if self._settling_seen >= self.config.settle_cycles:
                self.phase = AutotunePhase.collecting
                self.message = "collecting clean relay cycles"
            return
        reason = cycle_quality_reason(
            cycle,
            self.config.hysteresis,
            self.noise_sigma or 0.0,
        )
        if reason is not None:
            self._terminate(AutotunePhase.failed, reason, now)
            return
        self.cycles.append(cycle)
        if len(self.cycles) >= self.config.clean_cycles:
            self._identify(now)

    def _adapt(self, cycle: CycleSummary, now: float) -> None:
        if self._adaptations >= self.config.max_adaptations or cycle.amplitude <= 0:
            self._terminate(AutotunePhase.failed, "adequate relay amplitude could not be reached", now)
            return
        factor = min(2.0, 3.0 * self.config.hysteresis / cycle.amplitude)
        new_base = self.base_dose_ml * factor
        new_acid = self.acid_dose_ml * factor
        remaining = self.dose_budget_ml - self.actual_dose_ml
        try:
            self._validate_adapted_dose(self.base, new_base, "base")
            self._validate_adapted_dose(self.acid, new_acid, "acid")
        except ValueError as exc:
            self._terminate(AutotunePhase.failed, f"adequate relay amplitude cannot be delivered: {exc}", now)
            return
        if new_base + new_acid > remaining:
            self._terminate(AutotunePhase.failed, "adequate relay amplitude exceeds the remaining dose budget", now)
            return
        self.base_dose_ml = new_base
        self.acid_dose_ml = new_acid
        self._adaptations += 1
        self.phase = AutotunePhase.adapting
        self.message = f"adapted both relay doses by {factor:.3g}x"

    def _validate_adapted_dose(self, actuator: _ActuatorLike, dose: float, label: str) -> None:
        flow = actuator.channel.calibration.flow_at(actuator.channel.calibration.dispense_duty)
        maximum = flow * actuator.control_period / 60.0
        if not math.isfinite(dose) or dose > maximum:
            error_message = f"{label} dose {dose:.6g} mL exceeds {maximum:.6g} mL"
            raise ValueError(error_message)

    def _begin_settling(self) -> None:
        self.phase = AutotunePhase.settling
        self.message = "discarding transient relay cycles after adaptation"
        self._settling_seen = 0
        self.cycles.clear()
        self._switches.clear()

    def _identify(self, now: float) -> None:
        reason = period_quality_reason(self.cycles)
        if reason is not None:
            self._terminate(AutotunePhase.failed, reason, now)
            return
        periods = np.asarray([cycle.period for cycle in self.cycles], dtype=float)
        mean_period = float(np.mean(periods))
        amplitude = float(np.mean([cycle.amplitude for cycle in self.cycles]))
        base_actual = sum(cycle.actual_base_ml for cycle in self.cycles)
        acid_actual = sum(cycle.actual_acid_ml for cycle in self.cycles)
        base_requests = sum(cycle.base_requests for cycle in self.cycles)
        acid_requests = sum(cycle.acid_requests for cycle in self.cycles)
        if base_requests <= 0 or acid_requests <= 0 or base_actual <= 0 or acid_actual <= 0:
            self._terminate(AutotunePhase.failed, "relay cycles contain no actual delivered dose", now)
            return
        actual_base_dose = base_actual / base_requests
        actual_acid_dose = acid_actual / acid_requests
        corrected = math.sqrt(max(amplitude**2 - self.config.hysteresis**2, 1e-12))
        ku = 2.0 * (actual_base_dose + actual_acid_dose) / (math.pi * corrected)
        identification = RelayIdentification(
            ku,
            mean_period,
            amplitude,
            float(np.mean([(cycle.peak_ph + cycle.trough_ph) / 2 for cycle in self.cycles])),
            len(self.cycles),
        )
        if not all(math.isfinite(value) and value > 0 for value in (ku, mean_period, amplitude)):
            self._terminate(AutotunePhase.failed, "relay identification is non-finite", now)
            return
        # Bank the last delivery before freezing result accounting.
        self._terminate(AutotunePhase.identified, "relay identification completed", now, notify=False)
        self.result = AutotuneResult(
            identification,
            self.noise_sigma or 0.0,
            self.base_dose_ml,
            self.acid_dose_ml,
            self.actual_dose_ml,
            tuple(self.cycles),
        )
        self._notify_terminal()

    def _abort_if_dose_exhausted(self, now: float) -> None:
        if self.actual_dose_ml >= self.dose_budget_ml:
            self._terminate(AutotunePhase.aborted, "actual combined dose budget exhausted", now)

    def _terminate(self, phase: AutotunePhase, message: str, now: float, *, notify: bool = True) -> None:
        if self.phase in TERMINAL_PHASES:
            return
        cleanup_errors = self._cleanup_claimed()
        self.phase = phase
        self.message = message
        if cleanup_errors:
            self.message += "; cleanup errors: " + "; ".join(cleanup_errors)
        self.ended_at = now
        if notify:
            self._notify_terminal()

    def _notify_terminal(self) -> None:
        """Persist a terminal update without allowing audit I/O to break cleanup."""
        if self._terminal_callback is None:
            return
        try:
            reason = self._terminal_callback(self)
        except Exception as exc:  # noqa: BLE001 - audit failure is operator-visible, never fatal
            reason = f"autotune audit callback failed: {exc}"
        if reason:
            self.message += f"; {reason}"

    def _cleanup_claimed(self) -> list[str]:
        errors: list[str] = []
        for actuator in tuple(self._claimed):
            try:
                actuator.release_autotune(self)
            except Exception as exc:  # noqa: BLE001 - finish both cleanups
                errors.append(f"{actuator.id}: {exc}")
        self._claimed.clear()
        return errors


class AutotuneCoordinator:
    """Own the single active non-blocking autotune run for one reactor."""

    def __init__(self, context: AutotuneContext, *, clock: Callable[[], float] = perf_counter, audit: object | None = None) -> None:
        """Attach the coordinator to one injected reactor context."""
        self.context = context
        self.clock = clock
        self.audit = audit
        self.run: AutotuneRun | None = None

    def start(
        self,
        sensor_id: str,
        base_id: str,
        acid_id: str,
        config: RelayTuneConfig | None = None,
    ) -> AutotuneRun:
        """Start one run, refusing overlap on this reactor."""
        if self.run is not None and self.run.is_active:
            error_message = f"{self.context.reactor_id} already has an active autotune"
            raise RuntimeError(error_message)
        run = AutotuneRun(
            self.context,
            sensor_id,
            base_id,
            acid_id,
            config,
            clock=self.clock,
            terminal_callback=self._record_terminal,
        )
        run.start()
        if self.audit is not None:
            try:
                outcome = self.audit.record_started(run)
                if outcome.ok and outcome.data is not None:
                    run.audit_id = outcome.data["run_id"]
                else:
                    run.message += f"; {outcome.message}"
            except Exception as exc:  # noqa: BLE001 - started run remains safe if disk is unavailable
                run.message += f"; autotune audit was not saved: {exc}"
        self.run = run
        return run

    def _record_terminal(self, run: AutotuneRun) -> str | None:
        """Persist a terminal lifecycle update, returning an operator message on failure."""
        if self.audit is None:
            return None
        outcome = self.audit.record_terminal(run)
        return None if outcome.ok else outcome.message


def validate_autotune_selection(
    context: AutotuneContext,
    sensor_id: str,
    base_id: str,
    acid_id: str,
    setpoint: float,
) -> int:
    """Validate one pH split-range selection and return its channel index.

    The live run and audit candidate preparation deliberately share this
    one check, so stale stored selections cannot bypass Stage 2 safeguards.
    """
    prefix = context.reactor_id.split(":", 1)[0]
    for identifier in (sensor_id, base_id, acid_id):
        if identifier.split(":", 1)[0] != prefix:
            error_message = f"{identifier} does not belong to {context.reactor_id}"
            raise ValueError(error_message)
    if base_id == acid_id:
        error_message = "base and acid pumps must be different actuators"
        raise ValueError(error_message)
    if sensor_id not in context.sensors:
        error_message = f"unknown pH sensor {sensor_id}"
        raise ValueError(error_message)
    if base_id not in context.actuators or acid_id not in context.actuators:
        error_message = "unknown base or acid actuator"
        raise ValueError(error_message)
    sensor = context.sensors[sensor_id]
    ph_channels = [index for index, channel in enumerate(sensor.channels) if str(getattr(channel, "units", "")).lower() == "ph"]
    if len(ph_channels) != 1:
        error_message = f"{sensor_id} must have exactly one pH channel"
        raise ValueError(error_message)
    channel_index = ph_channels[0]
    paired = set(context.pairings().get(sensor_id, ()))
    for label, actuator_id, backwards in (("base", base_id, False), ("acid", acid_id, True)):
        actuator = context.actuators[actuator_id]
        if (actuator.id, channel_index) not in paired:
            error_message = f"{label} pump {actuator.id} is not paired to the pH channel"
            raise ValueError(error_message)
        controller = actuator.controller
        if getattr(controller, "method", None) is not ControlMethod.pid:
            error_message = f"{label} pump must use PID control"
            raise ValueError(error_message)
        if actuator.dispenser.unit is not OutputUnit.volume:
            error_message = f"{label} pump must use volume output"
            raise ValueError(error_message)
        if getattr(controller, "setpoint", None) != setpoint:
            error_message = f"{label} pump setpoint does not match the autotune setpoint"
            raise ValueError(error_message)
        if getattr(controller, "backwards", None) is not backwards:
            error_message = f"{label} pump backwards must be {backwards}"
            raise ValueError(error_message)
        calibration = actuator.channel.calibration
        if calibration is None or not calibration.is_fitted:
            error_message = f"{label} pump needs a fitted calibration"
            raise ValueError(error_message)
        reason = calibration.installable_reason()
        if reason is not None:
            error_message = f"{label} pump calibration is unusable: {reason}"
            raise ValueError(error_message)
    if context.actuators[base_id].controller.setpoint != context.actuators[acid_id].controller.setpoint:
        error_message = "base and acid pumps must share one setpoint"
        raise ValueError(error_message)
    return channel_index



