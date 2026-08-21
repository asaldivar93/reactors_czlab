"""Estimators for sensor response time and reactor mixing time.

These are the *reference implementations* the production feature must reproduce.
Both consume a uniformly sampled trace (the shape the server delivers: one column
of timestamps at a fixed period, one column of readings) and return a small set of
physically meaningful numbers with clear acceptance criteria.

``estimate_response_time``
    Fit a first-order-plus-dead-time (FOPDT) model to a recorded step and also read
    the model-free T63/T90 crossing times. Returns ``ResponseEstimate``.

``estimate_mixing_time``
    Turn an acid/base pulse read on the pH probe into a homogenization time. The raw
    pH is (1) linearized to the strong-ion difference ``Z`` using the repo's
    charge-balance chemistry, which is linear in added strong titrant; (2) optionally
    deconvolved for the probe's first-order lag ``tau`` (from the response-time
    feature) via the exact inverse filter ``c_true = c_meas + tau*dc/dt``; then
    (3) reduced to t95 / t90 by the last-crossing band criterion. Returns
    ``MixingEstimate``.

Design constraints honoured here (see literature_review.md / methodology.md):
  * uniform coarse sampling (server period 1-30 s) is the only input;
  * pH is nonlinear, so the band criterion is applied to the linearized tracer,
    never to raw pH;
  * the band criterion uses the LAST crossing (permanent entry), not the first.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.optimize import curve_fit
from scipy.signal import savgol_filter

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from reactors_czlab.autotune.model import Chemistry, state_from_ph  # noqa: E402


# --------------------------------------------------------------------------- #
# Sensor response time
# --------------------------------------------------------------------------- #
@dataclass
class ResponseEstimate:
    """Result of a sensor step-response identification.

    Attributes
    ----------
    tau:
        Fitted first-order time constant in seconds.
    dead_time:
        Fitted transport delay in seconds.
    gain:
        Fitted steady-state change (measured units).
    t63, t90:
        Model-free interpolated times to 63.2% and 90% of the total change, in
        seconds relative to the step, or ``nan`` if never reached.
    rmse_norm:
        Root-mean-square fit residual normalized by the step size (dimensionless);
        the primary goodness-of-fit acceptance metric.
    n_points_rise:
        Number of samples strictly between 10% and 90% of the change; the resolution
        acceptance metric (a step resolved by too few samples is untrustworthy).
    """

    tau: float
    dead_time: float
    gain: float
    t63: float
    t90: float
    rmse_norm: float
    n_points_rise: int


def _fopdt_step(t: np.ndarray, tau: float, dead_time: float, gain: float,
                y0: float, t_step: float) -> np.ndarray:
    """Return the FOPDT response to a unit step applied at ``t_step``."""
    dt = t - t_step - dead_time
    resp = np.where(dt > 0.0, gain * (1.0 - np.exp(-dt / max(tau, 1e-9))), 0.0)
    return y0 + resp


def _crossing_time(t: np.ndarray, y_norm: np.ndarray, level: float) -> float:
    """Return the last time ``y_norm`` crosses ``level`` upward, interpolated."""
    above = y_norm >= level
    if not above.any():
        return float("nan")
    # Last index where it transitions from below to at/above and stays.
    idx = np.where(above)[0][0]
    # Walk back to the last upward crossing that is permanent.
    perm = above.copy()
    for i in range(len(perm) - 1, 0, -1):
        if not perm[i]:
            break
        idx = i
    if idx == 0:
        return float(t[0])
    y0, y1 = y_norm[idx - 1], y_norm[idx]
    if y1 == y0:
        return float(t[idx])
    frac = (level - y0) / (y1 - y0)
    return float(t[idx - 1] + frac * (t[idx] - t[idx - 1]))


def estimate_response_time(
    t: np.ndarray,
    y: np.ndarray,
    *,
    step_time: float | None = None,
) -> ResponseEstimate:
    """Estimate sensor response time from a recorded step.

    Parameters
    ----------
    t:
        Sample timestamps in seconds (uniform spacing assumed but not required).
    y:
        Measured sensor readings, same length as ``t``.
    step_time:
        Time of the commanded step. If ``None``, taken as the first timestamp.

    Returns
    -------
    ResponseEstimate
    """
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    if step_time is None:
        step_time = float(t[0])
    y0 = float(np.mean(y[t <= step_time])) if (t <= step_time).any() else float(y[0])
    y_inf = float(np.mean(y[-max(1, len(y) // 10):]))
    gain0 = y_inf - y0
    if abs(gain0) < 1e-12:
        raise ValueError("no detectable step change in the trace")

    # Normalized response for model-free crossings and initial guesses.
    y_norm = (y - y0) / gain0
    span = t[-1] - step_time
    p0 = [max(span / 5.0, 1.0), max((t[1] - t[0]) if len(t) > 1 else 1.0, 0.0), gain0]

    def model(tt, tau, dead, gain):
        return _fopdt_step(tt, tau, dead, gain, y0, step_time)

    bounds = ([1e-3, 0.0, gain0 * 0.5 if gain0 > 0 else gain0 * 1.5],
              [span * 5.0, span, gain0 * 1.5 if gain0 > 0 else gain0 * 0.5])
    try:
        popt, _ = curve_fit(model, t, y, p0=p0, bounds=bounds, maxfev=20000)
        tau, dead, gain = (float(v) for v in popt)
    except Exception:
        tau, dead, gain = p0[0], p0[1], gain0

    resid = y - model(t, tau, dead, gain)
    rmse_norm = float(np.sqrt(np.mean(resid**2)) / abs(gain0))
    t63 = _crossing_time(t - step_time, y_norm, 0.632)
    t90 = _crossing_time(t - step_time, y_norm, 0.90)
    n_rise = int(np.sum((y_norm > 0.1) & (y_norm < 0.9)))
    return ResponseEstimate(tau, dead, gain, t63, t90, rmse_norm, n_rise)


# --------------------------------------------------------------------------- #
# Mixing time
# --------------------------------------------------------------------------- #
@dataclass
class MixingEstimate:
    """Result of a pulse-based mixing-time determination.

    Attributes
    ----------
    t95, t90:
        Homogenization times in seconds: the last time the linearized, deconvolved
        signal enters and stays within +-5% / +-10% of its final value, measured from
        the pulse.
    t95_raw:
        t95 computed from the raw (un-deconvolved) linearized signal; the gap
        ``t95 - t95_raw`` is the probe-lag bias that deconvolution removes.
    final_offset:
        Final linearized tracer change (mol/L of strong-ion difference); the SNR
        acceptance metric together with ``noise_sigma_z``.
    noise_sigma_z:
        Estimated noise on the linearized signal, from the pre-pulse baseline.
    deconvolved:
        Whether probe-lag deconvolution was applied.
    """

    t95: float
    t90: float
    t95_raw: float
    final_offset: float
    noise_sigma_z: float
    deconvolved: bool


def ph_to_tracer(
    ph: np.ndarray,
    phosphate_molar: float,
    *,
    chem: Chemistry | None = None,
) -> np.ndarray:
    """Map a pH trace to the strong-ion difference Z (linear in added titrant).

    ``Z`` is the reaction invariant the repo's chemistry uses; it is linear in the
    moles of strong acid/base present, so a band criterion applied to ``Z`` is a
    band on tracer concentration, which pH is not.
    """
    chem = chem or Chemistry()
    return np.array([state_from_ph(float(p), phosphate_molar, chem) for p in ph])


def deconvolve_first_order(
    t: np.ndarray,
    y: np.ndarray,
    tau: float,
    *,
    smooth_window: int = 0,
) -> np.ndarray:
    """Invert a first-order lag: return ``y + tau * dy/dt`` on a sampled signal.

    This is the exact inverse of ``tau*dy/dt + y = u`` and reconstructs the true
    upstream signal ``u`` from the probe output ``y`` given the probe time constant
    ``tau``. Because differentiation amplifies high-frequency noise, an optional
    Savitzky-Golay pre-smooth (odd ``smooth_window`` >= 5 samples) is applied and the
    derivative is taken from the same low-order polynomial fit, which is the standard
    way to keep the deconvolution stable on noisy coarse data.
    """
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    if tau <= 0.0:
        return y.copy()
    dt = float(np.median(np.diff(t))) if t.size > 1 else 1.0
    if smooth_window and smooth_window >= 5 and smooth_window <= y.size:
        win = smooth_window if smooth_window % 2 == 1 else smooth_window + 1
        y_s = savgol_filter(y, win, polyorder=2)
        dydt = savgol_filter(y, win, polyorder=2, deriv=1, delta=dt)
    else:
        y_s = y
        dydt = np.gradient(y, t)
    return y_s + tau * dydt


def estimate_mixing_time(
    t: np.ndarray,
    ph: np.ndarray,
    phosphate_molar: float,
    *,
    pulse_time: float | None = None,
    tau_probe: float = 0.0,
    band95: float = 0.05,
    band90: float = 0.10,
    smooth_window: int = 0,
    chem: Chemistry | None = None,
) -> MixingEstimate:
    """Estimate reactor mixing time from an acid/base pulse read on the pH probe.

    Parameters
    ----------
    t:
        Sample timestamps in seconds.
    ph:
        Measured pH trace, same length as ``t``.
    phosphate_molar:
        Total phosphate buffer concentration, for the pH->tracer linearization.
    pulse_time:
        Time the bolus was injected. If ``None``, taken as the first timestamp.
    tau_probe:
        Probe first-order time constant from the response-time feature. If > 0 the
        linearized signal is deconvolved before the band criterion is applied.
    band95, band90:
        Fractional homogeneity bands (defaults +-5% and +-10%).
    chem:
        Chemistry model (defaults to the repo default).

    Returns
    -------
    MixingEstimate
    """
    t = np.asarray(t, dtype=float)
    ph = np.asarray(ph, dtype=float)
    if pulse_time is None:
        pulse_time = float(t[0])

    z = ph_to_tracer(ph, phosphate_molar, chem=chem)
    pre = z[t <= pulse_time]
    z0 = float(np.mean(pre)) if pre.size else float(z[0])
    noise_sigma_z = float(np.std(pre)) if pre.size > 1 else 0.0

    z_raw = z
    if tau_probe > 0:
        z_use = deconvolve_first_order(t, z, tau_probe, smooth_window=smooth_window)
    elif smooth_window and smooth_window >= 5 and smooth_window <= z.size:
        win = smooth_window if smooth_window % 2 == 1 else smooth_window + 1
        z_use = savgol_filter(z, win, polyorder=2)
    else:
        z_use = z_raw

    z_final = float(np.mean(z_use[-max(1, len(z_use) // 10):]))
    offset = z_final - z0
    if abs(offset) < 1e-12:
        raise ValueError("no detectable tracer change after the pulse")

    def _t_band(signal: np.ndarray, band: float) -> float:
        norm = (signal - z0) / offset  # 0 before pulse, ->1 at homogeneity
        within = np.abs(norm - 1.0) <= band
        mask = t >= pulse_time
        idxs = np.where(mask)[0]
        if not within[idxs].any():
            return float("nan")
        # Last index that is OUTSIDE the band; homogenization is the next sample.
        last_out = idxs[0]
        for i in idxs:
            if not within[i]:
                last_out = i
        # Interpolate the crossing between last_out and last_out+1.
        if last_out >= len(t) - 1:
            return float("nan")
        j = last_out + 1
        return float(t[j] - pulse_time)

    t95 = _t_band(z_use, band95)
    t90 = _t_band(z_use, band90)
    t95_raw = _t_band(z_raw, band95)
    return MixingEstimate(
        t95=t95, t90=t90, t95_raw=t95_raw,
        final_offset=offset, noise_sigma_z=noise_sigma_z,
        deconvolved=tau_probe > 0,
    )
