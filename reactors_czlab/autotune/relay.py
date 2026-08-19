"""Relay control, identification, tuning rules, and gain scaling."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from reactors_czlab.autotune.model import Chemistry, buffering_intensity


@dataclass
class RelayTuneConfig:
    """Configuration for a split-range relay-feedback experiment.

    The 0.20 mL dose defaults are intended for the future live workflow.
    Deterministic model validation explicitly uses 0.30 mL.
    """

    setpoint: float = 7.0
    base_dose_ml: float = 0.20
    acid_dose_ml: float = 0.20
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
        """Return signed dose demand in mL for one control period."""
        if self.high and ph > self.config.setpoint + self.config.hysteresis:
            self.high = False
        elif (
            not self.high
            and ph < self.config.setpoint - self.config.hysteresis
        ):
            self.high = True
        return self.config.base_dose_ml if self.high else -self.config.acid_dose_ml


@dataclass(frozen=True)
class RelayIdentification:
    """Identified limit-cycle properties from a relay experiment."""

    Ku: float
    Pu: float
    amplitude: float
    mean_ph: float
    cycles_used: int


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
    base_dose_ml: float,
    acid_dose_ml: float,
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
    ultimate_gain = 2.0 * (base_dose_ml + acid_dose_ml) / (
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
        base_dose_ml, acid_dose_ml = relay_amp
    else:
        base_dose_ml = acid_dose_ml = relay_amp
    if (
        not np.isfinite(base_dose_ml)
        or not np.isfinite(acid_dose_ml)
        or base_dose_ml <= 0
        or acid_dose_ml <= 0
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
        float(base_dose_ml),
        float(acid_dose_ml),
        hysteresis,
        settle_cycles=0,
    )
    if not np.isfinite(identification.Ku) or not np.isfinite(identification.Pu):
        error_message = "relay trace does not contain an identifiable cycle"
        raise ValueError(error_message)
    return identification.Ku, identification.Pu


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


