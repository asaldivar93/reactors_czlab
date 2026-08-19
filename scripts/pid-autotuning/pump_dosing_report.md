# Pump PWM-Duty Calibration & Volume Dosing

Fitting a flow-rate calibration curve to PWM duty, and using it to plan
`(duty, time)` pairs that deliver a requested volume across a wide dynamic range.

## 1. Problem

A PWM-driven metering pump has flow rate `Q` that depends on the commanded duty
`d`. Two tasks:

1. **Calibration** — model `Q(d)` from measured (duty, flow) pairs, and invert
   it (`d(Q)`) so the controller can request a flow.
2. **Dosing** — deliver a requested volume `V` via `V = Q(d)·t`. Fixing `d` and
   solving for `t` pins the pump to one flow rate and limits dynamic range;
   freeing both `d` and `t` widens it.

## 2. Candidate models for Q(d)

The physical response has three features: a **dead-zone** at low duty (stiction /
backpressure), a **near-linear mid-range**, and **saturation** near full duty.
Models differ by how many of these they capture.

| Model | Equation | Notes |
|---|---|---|
| Linear | $Q = a d + b$ | 2 params, trivially invertible; ignores dead-zone & saturation |
| Dead-zone linear | $Q = \max(0,\,k(d-d_0))$ | explicit threshold $d_0$; monotone; no saturation |
| Saturating exponential | $Q = Q_{max}(1-e^{-k(d-d_0)})$ | threshold **and** ceiling; closed-form inverse |
| Logistic | $Q = Q_{max}/(1+e^{-k(d-d_{50})})$ | soft threshold + saturation; forces symmetric S; never reaches 0 |
| Quadratic / cubic | $Q = a d^2 + b d + c$ | flexible but **not guaranteed monotone**; extrapolates badly |
| Power law | $Q = a\,d^{\,n}$ | gradual ramp from 0; no true dead-zone or ceiling |

For a control curve, prefer models that are **monotone** and **analytically
invertible** — the controller needs $d = Q^{-1}(Q_{demand})$ at runtime.

## 3. Model selection

Fitted all parametric candidates to the calibration data (n = 19 points) by
least squares (nonlinear least squares for the curved models). Ranked by
**AIC** (`n·ln(SSE/n) + 2k`), which rewards fit quality and penalizes extra
parameters, with RMSE as the plain error measure.

| Model | Equation | RMSE | AIC |
|---|---|---|---|
| Saturating exponential ✅ | $Q = Q_{max}\,(1-e^{-k(d-d_0)})$ | 4.99 | 67.1 |
| Quadratic | $Q = a d^2 + b d + c$ | 6.34 | 76.2 |
| Logistic | $Q = Q_{max}/(1+e^{-k(d-d_{50})})$ | 7.02 | 80.0 |
| Power law | $Q = a\,d^{\,n}$ | 9.24 | 88.5 |
| Dead-zone linear | $Q = \max(0,\,k(d-d_0))$ | 10.55 | 93.5 |
| Linear | $Q = a d + b$ | 10.55 | 93.5 |

**Winner: saturating exponential** — lowest RMSE and AIC, and it matches the
physics (dead-zone + ceiling). Fitted parameters:

$$Q(d) = 101.28\,\left(1 - e^{-9.340e-04\,(d - 508.3)}\right)$$

- $Q_{max} = 101.2799$ (flow ceiling)
- $k = 9.3401e-04$ (rise rate)
- $d_0 = 508.3$ (fitted dead-zone; **lowest calibrated duty is 800**, so treat
  800 as the reliable floor and 508–800 as extrapolation)

**Inverse** (used by the controller):

$$d(Q) = d_0 - \frac{1}{k}\ln\!\left(1 - \frac{Q}{Q_{max}}\right)$$

## 4. Volume dosing: choosing (duty, time)

`V = Q(d)·t` is one equation in two unknowns. Assign each knob a role:

- **Duty = coarse knob** — sets the flow rate (and thus the dynamic range).
  Discrete (integer PWM counts), so it cannot hit a volume exactly.
- **Time = fine knob** — continuous, so it absorbs duty quantization and gives
  near-continuous volume resolution.

**Dynamic range.** Freeing duty multiplies the reachable-volume range by the
flow ratio of the reliable duty band:

$$\text{range} = \frac{Q_{max}}{Q_{min}}\cdot\frac{t_{max}}{t_{min}}$$

For this calibration that is ≈ 4× (flow) × 300× (time) ≈ **1200×**, versus
≈ 300× at fixed duty.

**Algorithm** (`plan_dose`):

1. Aim for a nominal on-time `t_target` → required flow `q = V / t_target`.
2. Invert calibration to a duty, clamp to `[duty_min, duty_max]`, quantize to
   the PWM step.
3. **Recompute time from the *achieved* flow**: `t = V / Q(duty_q)`. This cancels
   the duty-quantization error in the continuous time variable.
4. Clamp `t` to `[t_min, t_max]`; if a limit is hit, flag `saturated` (volume out
   of range).

Targeting a mid-range `t_target` (rather than always max duty) keeps a timing
margin at both ends: small volumes stay above `t_min`; large volumes have
headroom before `t_max`.

**Set to real hardware values:** `T_MIN` (shortest reliably-timed pump-on: loop
`dt` / valve settle), `T_MAX` (longest acceptable dose), `T_TARGET` (preferred
duration). These, not the calibration, set where the reachable-volume window
lands.

## 5. `plan_dose` function

For our set_up, T_MIN is 1 second and T_MAX would be the sampling period for _PidControl or the maximum dosing time for the rest of control modes

```python
def plan_dose(
    volume: float,
    *,
    t_target: float = T_TARGET,
    t_min: float = T_MIN,
    t_max: float = T_MAX,
    duty_min: float = DUTY_RELIABLE_MIN,
    duty_max: float = DUTY_MAX,
    duty_step: float = 1.0,
) -> dict:
    """Choose a (duty, time) pair at runtime to deliver a requested volume.

    Duty is the coarse knob (sets flow rate and dynamic range); time is the
    fine continuous knob that trims for accuracy and absorbs duty quantization.

    Parameters
    ----------
    volume : float
        Requested dose volume (units consistent with the flow calibration:
        flow * time).
    t_target : float, optional
        Preferred pump-on duration. The planner sizes duty so a nominal dose
        runs for about this long, keeping a time margin at both ends.
    t_min, t_max : float, optional
        Hardware timing limits; the returned time is clamped to this range. 
    duty_min, duty_max : float, optional
        Reliable PWM duty band (defaults to the calibration support).
    duty_step : float, optional
        PWM quantization step in counts; duty is rounded to a multiple of it.

    Returns
    -------
    dict
        ``duty`` (counts), ``time_s`` (s), ``flow`` (achieved rate),
        ``delivered`` (predicted volume), ``residual`` (volume - delivered),
        and ``saturated`` (True if a limit was hit and the volume is not
        exactly reachable).

    Raises
    ------
    ValueError
        If ``volume`` is negative.
    """
    if volume < 0:
        raise ValueError("Requested volume must be non-negative.")
    q_wanted = volume / t_target
    duty = float(duty_from_flow(max(q_wanted, 0.0)))
    duty = min(max(round(duty / duty_step) * duty_step, duty_min), duty_max)
    q_act = float(flow_from_duty(duty))
    t = volume / q_act if q_act > 0 else t_max
    t_clamped = min(max(t, t_min), t_max)
    delivered = q_act * t_clamped
    return {
        "duty": float(duty),
        "time_s": float(t_clamped),
        "flow": q_act,
        "delivered": delivered,
        "residual": volume - delivered,
        "saturated": abs(t_clamped - t) > 1e-9,
    }
```
