"""Simulated relay experiments and closed-loop pH control utilities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from reactors_czlab.autotune.model import PhPlant
from reactors_czlab.autotune.relay import RelayController, RelayTuneConfig, _identify_relay


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
    base_dose_ml: float
    acid_dose_ml: float
    switch_times: list[float] = field(default_factory=list)

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
        config.base_dose_ml,
        config.acid_dose_ml,
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
        base_dose_ml=config.base_dose_ml,
        acid_dose_ml=config.acid_dose_ml,
        switch_times=switch_times,
    )


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
    min_dose: float = 0.0


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
        if self.config.min_dose > 0:
            base = 0.0 if base < self.config.min_dose else base
            acid = 0.0 if acid < self.config.min_dose else acid
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
