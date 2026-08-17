"""Pure relay-autotuning and closed-loop pH simulation utilities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from reactors_czlab.core.ph_model import (
    Chemistry,
    PhPlant,
    buffering_intensity,
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
    """Drive a model plant with a relay and identify its steady limit cycle."""
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
