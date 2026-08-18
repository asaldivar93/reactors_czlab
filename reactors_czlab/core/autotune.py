"""Pure relay-autotuning and closed-loop pH simulation utilities."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum, auto
from time import perf_counter
from typing import Protocol

import numpy as np

from reactors_czlab.core.data import ERROR_VALUE, ControlMethod, OutputUnit
from reactors_czlab.core.ph_model import (
    Chemistry,
    PhPlant,
    buffering_intensity,
    state_from_ph,
)


@dataclass
class RelayTuneConfig:
    """Configuration for a split-range relay-feedback experiment.

    The 0.20 mL bolus defaults are intended for the future live workflow.
    Deterministic model validation explicitly uses 0.30 mL.
    """

    setpoint: float = 7.0
    u_base: float = 0.20
    u_acid: float = 0.20
    hysteresis: float = 0.02
    dt: float = 10.0
    dead_time: float = 10.0
    max_cycles: int = 12
    settle_cycles: int = 2
    max_steps: int = 4000

    # Live-workflow safety and chemistry settings. The simulation helpers
    # above use only the original fields, so extending the dataclass keeps
    # the accepted Stage-1 API intact.
    max_minutes: float = 30.0
    phosphate_molar: float = 0.014
    base_molar: float = 0.5
    acid_molar: float = 0.5
    dose_budget_ml: float | None = None
    acknowledge_other_loops: bool = False
    acknowledge_budget_override: bool = False
    baseline_seconds: float = 60.0
    baseline_samples: int = 6
    clean_cycles: int = 4
    max_adaptations: int = 6


class RelayController:
    """Stateful asymmetric relay with a pH hysteresis band."""

    def __init__(self, config: RelayTuneConfig) -> None:
        """Start with the base side of the relay active."""
        self.config = config
        self.high = True

    def output(self, ph: float) -> float:
        """Return signed bolus demand in mL for one control period."""
        if self.high and ph > self.config.setpoint + self.config.hysteresis:
            self.high = False
        elif (
            not self.high
            and ph < self.config.setpoint - self.config.hysteresis
        ):
            self.high = True
        return self.config.u_base if self.high else -self.config.u_acid


@dataclass(frozen=True)
class RelayIdentification:
    """Identified limit-cycle properties from a relay experiment."""

    Ku: float
    Pu: float
    amplitude: float
    mean_ph: float
    cycles_used: int


@dataclass
class RelayResult:
    """Trace and identification returned by a simulated relay experiment."""

    t: np.ndarray
    pH: np.ndarray
    u: np.ndarray
    Ku: float
    Pu: float
    a_amp: float
    cycle_mean_pH: float
    cycles_used: int
    u_base: float
    u_acid: float
    switch_times: list[float] = field(default_factory=list)


def _local_extrema(values: np.ndarray, *, maxima: bool) -> np.ndarray:
    if maxima:
        indices = np.where(
            (values[1:-1] > values[:-2])
            & (values[1:-1] >= values[2:])
        )[0] + 1
        return values[indices] if indices.size else np.array([values.max()])
    indices = np.where(
        (values[1:-1] < values[:-2])
        & (values[1:-1] <= values[2:])
    )[0] + 1
    return values[indices] if indices.size else np.array([values.min()])


def _identify_relay(
    time: np.ndarray,
    ph: np.ndarray,
    switch_times: np.ndarray,
    u_base: float,
    u_acid: float,
    hysteresis: float,
    *,
    settle_cycles: int,
) -> RelayIdentification:
    required_switches = 2 * (settle_cycles + 1)
    if switch_times.size < required_switches:
        return RelayIdentification(*(float("nan"),) * 4, cycles_used=0)

    cycle_boundaries = switch_times[::2]
    start_time = cycle_boundaries[min(settle_cycles, len(cycle_boundaries) - 1)]
    periods = np.diff(cycle_boundaries)
    if periods.size > settle_cycles:
        periods = periods[settle_cycles:]
    ultimate_period = float(np.mean(periods)) if periods.size else float("nan")

    segment = ph[time >= start_time]
    if segment.size < 4:
        return RelayIdentification(
            float("nan"),
            ultimate_period,
            float("nan"),
            float("nan"),
            len(periods),
        )
    peak = float(np.mean(_local_extrema(segment, maxima=True)))
    trough = float(np.mean(_local_extrema(segment, maxima=False)))
    mean_ph = 0.5 * (peak + trough)
    amplitude = 0.5 * (peak - trough)
    if not np.isfinite(amplitude) or amplitude <= hysteresis:
        error_message = (
            "relay cycle amplitude must be finite and clear hysteresis"
        )
        raise ValueError(error_message)
    corrected_amplitude = np.sqrt(max(amplitude**2 - hysteresis**2, 1e-12))
    ultimate_gain = 2.0 * (u_base + u_acid) / (
        np.pi * corrected_amplitude
    )
    return RelayIdentification(
        float(ultimate_gain),
        ultimate_period,
        amplitude,
        mean_ph,
        len(periods),
    )


def identify_ku_pu(
    ph_trace: np.ndarray | list[float],
    u_trace: np.ndarray | list[float],
    dt: float,
    relay_amp: float | tuple[float, float],
    hysteresis: float,
) -> tuple[float, float]:
    """Identify ultimate gain and period from a settled relay trace.

    Parameters
    ----------
    ph_trace:
        Measured pH samples containing at least one complete cycle.
    u_trace:
        Signed relay demand samples. Sign changes define switch times.
    dt:
        Sampling period in seconds.
    relay_amp:
        Symmetric relay amplitude or ``(base, acid)`` amplitudes in mL.
    hysteresis:
        Relay hysteresis half-width in pH units.

    Returns
    -------
    tuple[float, float]
        ``(Ku, Pu)`` in mL/pH and seconds.

    Raises
    ------
    ValueError
        If the traces cannot define a limit cycle or inputs are invalid.
    """
    ph = np.asarray(ph_trace, dtype=float)
    demand = np.asarray(u_trace, dtype=float)
    if ph.ndim != 1 or demand.ndim != 1 or ph.size != demand.size:
        error_message = "pH and relay traces must be equal-length 1-D arrays"
        raise ValueError(error_message)
    if ph.size < 4 or not np.all(np.isfinite(ph)):
        error_message = "pH trace must contain at least four finite samples"
        raise ValueError(error_message)
    if not np.isfinite(dt) or dt <= 0:
        error_message = "dt must be finite and positive"
        raise ValueError(error_message)
    if not np.isfinite(hysteresis) or hysteresis < 0:
        error_message = "hysteresis must be finite and non-negative"
        raise ValueError(error_message)

    if isinstance(relay_amp, tuple):
        u_base, u_acid = relay_amp
    else:
        u_base = u_acid = relay_amp
    if (
        not np.isfinite(u_base)
        or not np.isfinite(u_acid)
        or u_base <= 0
        or u_acid <= 0
    ):
        error_message = "relay amplitudes must be finite and positive"
        raise ValueError(error_message)

    signs = np.sign(demand)
    if np.any(signs == 0):
        error_message = "relay trace must contain non-zero signed demands"
        raise ValueError(error_message)
    switch_indices = np.flatnonzero(signs[1:] != signs[:-1]) + 1
    if switch_indices.size < 3:
        error_message = "relay trace must contain at least three switches"
        raise ValueError(error_message)
    switch_times = switch_indices.astype(float) * dt
    identification = _identify_relay(
        np.arange(ph.size, dtype=float) * dt,
        ph,
        switch_times,
        float(u_base),
        float(u_acid),
        hysteresis,
        settle_cycles=0,
    )
    if not np.isfinite(identification.Ku) or not np.isfinite(identification.Pu):
        error_message = "relay trace does not contain an identifiable cycle"
        raise ValueError(error_message)
    return identification.Ku, identification.Pu


def run_relay_experiment(
    plant: PhPlant,
    config: RelayTuneConfig,
    r_metabolic: float = 0.0,
    noise_pH: float = 0.005,
    seed: int = 0,
) -> RelayResult:
    """Drive a model plant with a relay and identify its steady limit cycle.

    Raises
    ------
    ValueError
        If a detected cycle does not have a finite amplitude that clears the
        configured hysteresis band.
    """
    rng = np.random.default_rng(seed)
    controller = RelayController(config)
    delay = max(0, round(config.dead_time / config.dt))
    measurement_buffer = [plant.pH] * (delay + 1)
    time: list[float] = []
    ph_trace: list[float] = []
    demand_trace: list[float] = []
    switch_times: list[float] = []
    previous_high = controller.high

    for index in range(config.max_steps):
        true_ph = plant.pH
        measurement_buffer.append(true_ph + rng.normal(0.0, noise_pH))
        measured_ph = measurement_buffer.pop(0)
        demand = controller.output(measured_ph)
        if controller.high != previous_high:
            switch_times.append(index * config.dt)
            previous_high = controller.high

        base_flow = max(demand, 0.0) / 1000.0 / config.dt
        acid_flow = max(-demand, 0.0) / 1000.0 / config.dt
        plant.step(
            q_base=base_flow,
            q_acid=acid_flow,
            dt=config.dt,
            r_metabolic=r_metabolic,
        )
        time.append(index * config.dt)
        ph_trace.append(true_ph)
        demand_trace.append(demand)
        if len(switch_times) >= 2 * (
            config.max_cycles + config.settle_cycles
        ):
            break

    time_array = np.asarray(time)
    ph_array = np.asarray(ph_trace)
    identification = _identify_relay(
        time_array,
        ph_array,
        np.asarray(switch_times),
        config.u_base,
        config.u_acid,
        config.hysteresis,
        settle_cycles=config.settle_cycles,
    )
    return RelayResult(
        t=time_array,
        pH=ph_array,
        u=np.asarray(demand_trace),
        Ku=identification.Ku,
        Pu=identification.Pu,
        a_amp=identification.amplitude,
        cycle_mean_pH=identification.mean_ph,
        cycles_used=identification.cycles_used,
        u_base=config.u_base,
        u_acid=config.u_acid,
        switch_times=switch_times,
    )


def tuning_rules(
    ku: float,
    pu: float,
) -> dict[str, tuple[float, float, float]]:
    """Return ZN-PID, TL-PI, and TL-PID continuous controller settings."""
    return {
        "ZN-PID": (0.6 * ku, 0.5 * pu, 0.125 * pu),
        "TL-PI": (ku / 3.2, 2.2 * pu, 0.0),
        "TL-PID": (ku / 2.2, 2.2 * pu, pu / 6.3),
    }


def simc_pid(
    ku: float,
    pu: float,
    tau_c: float | None = None,
) -> tuple[float, float, float]:
    """Return SIMC PI settings inferred from an integrating relay point."""
    if ku <= 0 or pu <= 0:
        error_message = "Ku and Pu must be positive"
        raise ValueError(error_message)
    angular_frequency = 2.0 * np.pi / pu
    dead_time = pu / 4.0
    response_time = dead_time if tau_c is None else tau_c
    if response_time <= 0:
        error_message = "tau_c must be positive"
        raise ValueError(error_message)
    integrating_slope = angular_frequency / ku
    kc = 1.0 / (integrating_slope * (response_time + dead_time))
    ti = 4.0 * (response_time + dead_time)
    return float(kc), ti, 0.0


def to_code_gains(
    kc: float,
    ti: float,
    td: float,
) -> tuple[float, float, float]:
    """Convert continuous parallel PID settings to code gains."""
    if ti <= 0:
        error_message = "Ti must be positive"
        raise ValueError(error_message)
    return kc, kc / ti, kc * td


def from_code_gains(
    kp: float,
    ki: float,
    kd: float,
) -> tuple[float, float, float]:
    """Convert code gains back to continuous parallel PID settings."""
    if kp <= 0 or ki <= 0:
        error_message = "kp and ki must be positive"
        raise ValueError(error_message)
    return kp, kp / ki, kd / kp


def scale_gains(
    kp: float,
    ki: float,
    kd: float,
    beta_ratio: float,
) -> tuple[float, float, float]:
    """Scale all controller gains by ``beta(target) / beta(tuned)``."""
    return kp * beta_ratio, ki * beta_ratio, kd * beta_ratio


def scale_gains_to_setpoint(
    kp: float,
    ki: float,
    kd: float,
    ph_tuned: float,
    ph_target: float,
    phosphate_molar: float,
    chemistry: Chemistry | None = None,
) -> tuple[float, float, float]:
    """Scale gains using buffer intensity at target versus tuned pH."""
    chem = chemistry or Chemistry()
    beta_tuned = buffering_intensity(ph_tuned, phosphate_molar, chem)
    beta_target = buffering_intensity(ph_target, phosphate_molar, chem)
    return scale_gains(kp, ki, kd, beta_target / beta_tuned)


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
    base_bolus_ml: float
    acid_bolus_ml: float
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
    base_bolus_ml: float
    acid_bolus_ml: float
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
        self.base_bolus_ml = float(self.config.u_base)
        self.acid_bolus_ml = float(self.config.u_acid)
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
            "base bolus": config.u_base,
            "acid bolus": config.u_acid,
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
        if config.u_base + config.u_acid > budget:
            error_message = "one base/acid relay pair exceeds the combined dose budget"
            raise ValueError(error_message)

        warnings: list[str] = []
        for label, actuator, bolus in (
            ("base", self.base, config.u_base),
            ("acid", self.acid, config.u_acid),
        ):
            if not math.isfinite(actuator.control_period) or actuator.control_period <= 0:
                error_message = f"{label} control period must be finite and positive"
                raise ValueError(error_message)
            flow = actuator.channel.calibration.flow_at(
                actuator.channel.calibration.dispense_duty,
            )
            minimum = flow * ACTUATOR_TICK_SECONDS / 60.0
            maximum = flow * actuator.control_period / 60.0
            if bolus < minimum or bolus > maximum:
                error_message = (
                    f"{label} bolus {bolus:.6g} mL is outside the deliverable "
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
        """Advance owned bolus deadlines from the 20 Hz actuator loop."""
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
            base_bolus_ml=self.base_bolus_ml,
            acid_bolus_ml=self.acid_bolus_ml,
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
        requested = self.base_bolus_ml if self._relay_high else -self.acid_bolus_ml
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
        new_base = self.base_bolus_ml * factor
        new_acid = self.acid_bolus_ml * factor
        remaining = self.dose_budget_ml - self.actual_dose_ml
        try:
            self._validate_adapted_bolus(self.base, new_base, "base")
            self._validate_adapted_bolus(self.acid, new_acid, "acid")
        except ValueError as exc:
            self._terminate(AutotunePhase.failed, f"adequate relay amplitude cannot be delivered: {exc}", now)
            return
        if new_base + new_acid > remaining:
            self._terminate(AutotunePhase.failed, "adequate relay amplitude exceeds the remaining dose budget", now)
            return
        self.base_bolus_ml = new_base
        self.acid_bolus_ml = new_acid
        self._adaptations += 1
        self.phase = AutotunePhase.adapting
        self.message = f"adapted both relay boluses by {factor:.3g}x"

    def _validate_adapted_bolus(self, actuator: _ActuatorLike, bolus: float, label: str) -> None:
        flow = actuator.channel.calibration.flow_at(actuator.channel.calibration.dispense_duty)
        maximum = flow * actuator.control_period / 60.0
        if not math.isfinite(bolus) or bolus > maximum:
            error_message = f"{label} bolus {bolus:.6g} mL exceeds {maximum:.6g} mL"
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
        actual_base_bolus = base_actual / base_requests
        actual_acid_bolus = acid_actual / acid_requests
        corrected = math.sqrt(max(amplitude**2 - self.config.hysteresis**2, 1e-12))
        ku = 2.0 * (actual_base_bolus + actual_acid_bolus) / (math.pi * corrected)
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
            self.base_bolus_ml,
            self.acid_bolus_ml,
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


def static_gain_from_asymmetry(
    result: RelayResult,
    config: RelayTuneConfig,
) -> float:
    """Return mean signed relay demand per second at the limit cycle."""
    if result.u.size == 0:
        return float("nan")
    return float(np.mean(result.u)) / config.dt


@dataclass
class Pump:
    """Linear pump-calibration model used by closed-loop studies."""

    a: float = 5.0 / 4095.0
    b: float = 0.0
    min_duty: float = 400.0
    max_duty: float = 4095.0
    dispense_duty: float = 4095.0

    def flow_at(self, duty: float) -> float:
        """Return calibrated flow in mL/min at a raw duty."""
        return self.a * duty + self.b

    def per_period_max(self, control_period: float) -> float:
        """Return maximum volume deliverable in one control period."""
        duty = min(self.dispense_duty, self.max_duty)
        return self.flow_at(duty) * control_period / 60.0


@dataclass
class SimPid:
    """Deterministic replica of the production positional parallel PID."""

    setpoint: float
    kp: float
    ki: float
    kd: float
    backwards: bool = False
    min_val: float = 0.0
    max_val: float = 1e9
    min_integral: float | None = None
    max_integral: float | None = None
    _integral_sum: float = field(default=0.0, init=False)
    _last_measurement: float | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        """Derive the default anti-windup limits."""
        if self.min_integral is None:
            self.min_integral = self.min_val
        if self.max_integral is None:
            self.max_integral = self.max_val

    def clamp(self, value: float) -> float:
        """Clamp a value to this controller's output range."""
        return max(self.min_val, min(value, self.max_val))

    def reset(self) -> None:
        """Clear integral and derivative runtime state."""
        self._integral_sum = 0.0
        self._last_measurement = None

    def get_value(self, measurement: float, dt: float) -> float:
        """Advance the PID and return its clamped output."""
        direction = -1.0 if self.backwards else 1.0
        error = direction * (self.setpoint - measurement)
        proportional = self.kp * error
        integral = self._integral_sum + self.ki * error * dt
        self._integral_sum = max(
            self.min_integral,
            min(integral, self.max_integral),
        )
        if self._last_measurement is None:
            derivative = 0.0
        else:
            derivative = (
                -direction
                * self.kd
                * (measurement - self._last_measurement)
                / dt
            )
        self._last_measurement = measurement
        return self.clamp(proportional + self._integral_sum + derivative)


@dataclass
class SplitRangeConfig:
    """Configuration for a simulated base/acid PID pair."""

    setpoint: float = 7.0
    kp: float = 5.0
    ki: float = 0.01
    kd: float = 0.0
    dead_band: float = 0.0
    dt: float = 10.0
    base_pump: Pump = field(default_factory=Pump)
    acid_pump: Pump = field(default_factory=Pump)
    min_bolus: float = 0.0


class SplitRangeController:
    """Simulated pair of oppositely acting, one-sided PID controllers."""

    def __init__(self, config: SplitRangeConfig) -> None:
        """Create base and acid PIDs from one shared configuration."""
        self.config = config
        self.cfg = config
        self.base = SimPid(
            config.setpoint,
            config.kp,
            config.ki,
            config.kd,
            max_val=config.base_pump.per_period_max(config.dt),
        )
        self.acid = SimPid(
            config.setpoint,
            config.kp,
            config.ki,
            config.kd,
            backwards=True,
            max_val=config.acid_pump.per_period_max(config.dt),
        )

    def set_gains(self, kp: float, ki: float, kd: float) -> None:
        """Set identical gain magnitudes on the base and acid controllers."""
        for controller in (self.base, self.acid):
            controller.kp = kp
            controller.ki = ki
            controller.kd = kd

    def outputs(self, measurement: float, dt: float) -> tuple[float, float]:
        """Return base and acid volume demands for one sample."""
        base = self.base.get_value(measurement, dt)
        acid = self.acid.get_value(measurement, dt)
        if self.config.dead_band > 0:
            if measurement < self.config.setpoint + self.config.dead_band:
                acid = 0.0
            if measurement > self.config.setpoint - self.config.dead_band:
                base = 0.0
        if self.config.min_bolus > 0:
            base = 0.0 if base < self.config.min_bolus else base
            acid = 0.0 if acid < self.config.min_bolus else acid
        return base, acid


@dataclass
class SimulationResult:
    """Closed-loop pH and dosing traces."""

    t: np.ndarray
    pH: np.ndarray
    base_mL: np.ndarray
    acid_mL: np.ndarray
    setpoint: np.ndarray
    cum_base: float
    cum_acid: float


def simulate(
    controller: SplitRangeController,
    plant: PhPlant,
    t_end: float = 3600.0,
    setpoint_fn: Callable[[float], float] | None = None,
    r_metabolic_fn: Callable[[float], float] | None = None,
    noise_pH: float = 0.005,
    dead_time: float = 10.0,
    delivery_gain: float = 1.0,
    seed: int = 0,
) -> SimulationResult:
    """Run the simulated split-range loop against a pH plant."""
    rng = np.random.default_rng(seed)
    dt = controller.config.dt
    sample_count = round(t_end / dt)
    delay = max(0, round(dead_time / dt))
    measurement_buffer = [plant.pH] * (delay + 1)
    time: list[float] = []
    ph_trace: list[float] = []
    base_trace: list[float] = []
    acid_trace: list[float] = []
    setpoint_trace: list[float] = []
    cumulative_base = 0.0
    cumulative_acid = 0.0
    max_base = controller.config.base_pump.per_period_max(dt)
    max_acid = controller.config.acid_pump.per_period_max(dt)

    for index in range(sample_count):
        current_time = index * dt
        if setpoint_fn is not None:
            setpoint = setpoint_fn(current_time)
            controller.base.setpoint = setpoint
            controller.acid.setpoint = setpoint
            controller.config.setpoint = setpoint
        setpoint = controller.config.setpoint
        metabolic_rate = (
            r_metabolic_fn(current_time)
            if r_metabolic_fn is not None
            else 0.0
        )
        true_ph = plant.pH
        measurement_buffer.append(true_ph + rng.normal(0.0, noise_pH))
        measurement = measurement_buffer.pop(0)
        base, acid = controller.outputs(measurement, dt)
        base = min(base, max_base)
        acid = min(acid, max_acid)
        delivered_base = base * delivery_gain
        delivered_acid = acid * delivery_gain
        plant.step(
            q_base=delivered_base / 1000.0 / dt,
            q_acid=delivered_acid / 1000.0 / dt,
            dt=dt,
            r_metabolic=metabolic_rate,
        )
        cumulative_base += delivered_base
        cumulative_acid += delivered_acid
        time.append(current_time)
        ph_trace.append(true_ph)
        base_trace.append(base)
        acid_trace.append(acid)
        setpoint_trace.append(setpoint)

    return SimulationResult(
        np.asarray(time),
        np.asarray(ph_trace),
        np.asarray(base_trace),
        np.asarray(acid_trace),
        np.asarray(setpoint_trace),
        cumulative_base,
        cumulative_acid,
    )


def simulation_metrics(
    result: SimulationResult,
    dt: float,
) -> dict[str, float]:
    """Return standard error and titrant metrics for a simulation."""
    error = result.pH - result.setpoint
    return {
        "IAE": float(np.sum(np.abs(error)) * dt),
        "ISE": float(np.sum(error**2) * dt),
        "max_abs_error": float(np.max(np.abs(error))),
        "final_error": float(error[-1]),
        "titrant_base_mL": result.cum_base,
        "titrant_acid_mL": result.cum_acid,
        "titrant_total_mL": result.cum_base + result.cum_acid,
    }


def settling_time(result: SimulationResult, band: float = 0.05) -> float:
    """Return time until the response remains inside a symmetric pH band."""
    error = np.abs(result.pH - result.setpoint)
    dt = result.t[1] - result.t[0] if len(result.t) > 1 else 1.0
    outside = np.where(error > band)[0]
    if outside.size == 0:
        return 0.0
    return float((outside[-1] + 1) * dt)


# Compatibility name used by the original scientific study scripts.
metrics = simulation_metrics
