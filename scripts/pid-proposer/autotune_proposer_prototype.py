"""
Prototype: model-only PID proposer for the reactors_czlab pH loop.

Runs against the installed reactors_czlab.autotune package. This is a PROTOTYPE
that demonstrates the methodology in `model_based_pid_proposer.md`; it is not the
feature. It reproduces every number quoted in that document.

Run:  python autotune_proposer_prototype.py
"""
from __future__ import annotations
import numpy as np
from reactors_czlab.autotune.model import (
    Chemistry, PlantParams, PhPlant, buffering_intensity, static_gain)
from reactors_czlab.autotune.relay import (
    RelayTuneConfig, tuning_rules, to_code_gains)
from reactors_czlab.autotune.simulation import (
    run_relay_experiment, Pump, SplitRangeConfig, SplitRangeController,
    simulate, simulation_metrics, settling_time)
from reactors_czlab.autotune.runtime import default_dose_budget_ml


def g_static(c_titrant: float, V: float, beta: float) -> float:
    """Static process gain dpH/dmL, closed form."""
    return c_titrant / (1000.0 * V * beta)


def closed_form_ku_pu(sp, phos, V, c, d, h, dt, dead):
    """Integrator+dead-time hysteresis-relay describing-function seed."""
    beta = buffering_intensity(sp, phos)
    g = g_static(c, V, beta)
    theta = dead + dt                       # discrete detect-then-act adds ~dt
    k1 = g * d / dt
    a = h + k1 * theta
    Pu = 4 * h / k1 + 4 * theta
    Ku = 4 * d / (np.pi * np.sqrt(max(a * a - h * h, 1e-12)))
    return Ku, Pu, a


def model_relay(sp, phos, V, c, d, h, dt, dead, noise=0.0, seed=0):
    """Run the EXISTING relay identification against the model plant."""
    plant = PhPlant(PlantParams(V0=V, C_P0=phos, pH0=sp, c_base=c, c_acid=c))
    cfg = RelayTuneConfig(setpoint=sp, base_dose_ml=d, acid_dose_ml=d,
                          hysteresis=h, dt=dt, dead_time=dead,
                          max_cycles=10, settle_cycles=2)
    return run_relay_experiment(plant, cfg, noise_pH=noise, seed=seed)


def propose_dose(sp, phos, V, c, h, dt, dead, amp_target_mult=3.0):
    """d such that limit-cycle amplitude ~ amp_target_mult * h."""
    beta = buffering_intensity(sp, phos)
    g = g_static(c, V, beta)
    theta = dead + dt
    return (amp_target_mult - 1.0) * h * dt / (g * theta)


def propose_gains(sp, phos, V, c, dt, dead, rule="TL-PI", h=0.02):
    """Model-only gain proposal: noise-free model relay -> tuning rule."""
    d = propose_dose(sp, phos, V, c, h, dt, dead)
    Ku_cf, Pu_cf, _ = closed_form_ku_pu(sp, phos, V, c, d, h, dt, dead)
    r = model_relay(sp, phos, V, c, d, h, dt, dead, noise=0.0)
    kc, ti, td = tuning_rules(r.Ku, r.Pu)[rule]
    kp, ki, kd = to_code_gains(kc, ti, td)
    return dict(kp=kp, ki=ki, kd=kd, Ku=r.Ku, Pu=r.Pu,
                Ku_cf=Ku_cf, Pu_cf=Pu_cf, dose_ml=d, rule=rule)


if __name__ == "__main__":
    sp, phos, V, c, dt, dead, h = 7.0, 0.014, 5.0, 0.5, 10.0, 10.0, 0.02

    print("== proposer @ pH7, 14 mM, 5 L, 0.5 M ==")
    p = propose_gains(sp, phos, V, c, dt, dead)
    print(f"  dose      = {p['dose_ml']:.3f} mL/period")
    print(f"  model Ku  = {p['Ku']:.2f} mL/pH   Pu = {p['Pu']:.1f} s")
    print(f"  closed-fm = {p['Ku_cf']:.2f} mL/pH   Pu = {p['Pu_cf']:.1f} s "
          f"(+{100*(p['Ku_cf']/p['Ku']-1):.0f}% / {100*(p['Pu_cf']/p['Pu']-1):.0f}%)")
    print(f"  gains     = kp {p['kp']:.3f}  ki {p['ki']:.5f}  kd {p['kd']:.3f}")
    print(f"  budget    = {default_dose_budget_ml(V, phos, sp, c, c):.1f} mL")

    # closed-loop check vs experimental / detuned
    def r_met(t):  return 6.0e-6 if t >= 600 else 0.0
    def run(gains, seed=7):
        pump = Pump()
        scfg = SplitRangeConfig(setpoint=sp, kp=gains[0], ki=gains[1], kd=gains[2],
                                dt=dt, base_pump=pump, acid_pump=pump)
        ctrl = SplitRangeController(scfg)
        plant = PhPlant(PlantParams(V0=V, C_P0=phos, pH0=sp, c_base=c, c_acid=c))
        res = simulate(ctrl, plant, t_end=3600.0, r_metabolic_fn=r_met,
                       noise_pH=0.005, dead_time=dead, seed=seed)
        return simulation_metrics(res, dt)

    gm = (p['kp'], p['ki'], p['kd'])
    re = model_relay(sp, phos, V, c, p['dose_ml'], h, dt, dead, noise=0.006, seed=3)
    kc, ti, td = tuning_rules(re.Ku, re.Pu)['TL-PI']
    ge = to_code_gains(kc, ti, td)
    print("\n== closed-loop IAE under acid load ==")
    for name, g in [("model", gm), ("experimental", ge),
                    ("hot 3x", (gm[0]*3, gm[1]*3, 0.0)),
                    ("cold 0.3x", (gm[0]*0.3, gm[1]*0.3, 0.0))]:
        m = run(g)
        print(f"  {name:14s} IAE={m['IAE']:6.1f}  maxErr={m['max_abs_error']:.3f}"
              f"  titrant={m['titrant_total_mL']:.1f} mL")
