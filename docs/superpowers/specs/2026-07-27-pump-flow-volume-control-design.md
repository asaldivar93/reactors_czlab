# Pump calibration and flow / volume control modes

Date: 2026-07-27

## Problem

DC peristaltic pumps are driven today as raw PLC duty values (0 - `MAX_OUTPUT`,
4095). An operator who wants to add 0.5 mL of base, or feed at 2 mL/min, has to
guess a duty number and time it by hand. Nothing in the codebase knows how a
duty value relates to a physical flow rate.

This design adds:

1. A calibration subsystem for `PlcActuator` pumps: run the pump, record what
   came out, fit a line, store it, reload it, and recalibrate at runtime.
2. Two new output units, **flow** (mL/min) and **volume** (mL), that work with
   the existing `_PidControl` and `_OnBoundariesControl` strategies rather than
   replacing them.
3. Per-pump tracking of total volume delivered, published as OPC data.

## Decisions taken during design

- Calibration is in scope in full, not deferred to a later spec.
- Calibration points are collected with the Pi driving the pump and the
  operator entering the measured volume. No balance or flow meter.
- Both sub-millilitre boluses and steady mL/min feeds must work.
- Volume mode means a **per-decision bolus**, not a one-shot total dispense and
  not a cumulative cap.
- Total delivered volume is tracked and published per pump. `Reactor.volume` is
  *not* updated from it; that would need a per-pump in/out sign convention and
  couple reactor state to actuator state.
- New units are **orthogonal** to `ControlMethod`, not new members of it.
  Members would multiply: `pid_flow`, `pid_volume`, `boundaries_flow`, ...

## Architecture

`ControlMethod` and the control strategies are untouched. A controller keeps
answering one question - *what should I demand?* - and a new `Dispenser`,
owned by the `Actuator` next to the channel calibration, answers the other -
*how do I deliver that?*

```
sensor value
    |
    v
_Control.get_value()  ->  demand   (counts | mL/min | mL)
                            |
                            v
                      Dispenser    (uses Channel.calibration)
                            |
                            v
                      duty counts  ->  Actuator.write()
```

This keeps `core/control.py` free of any knowledge of pumps or calibration, and
leaves the "configuration compares, runtime state does not" invariant of the
control dataclasses undisturbed.

### New modules

```
core/
  calibration.py   fit / load / save + the calibration run state machine
  dispenser.py     demand -> duty, bolus timing, volume totals
```

Both are standard library only. The Pi keeps its thin dependency set; the
ordinary-least-squares fit is a handful of lines and does not need numpy.
Neither module imports `core.sensor` or `core.modbus`, so the test suite can
import them with no pymodbus present.

### Units convention

Stated once and enforced everywhere:

- **duty** is raw PLC counts, `0 .. MAX_OUTPUT`.
- **flow** is mL/min.
- **volume** is mL.
- The calibration line is **`flow = a * duty + b`**.

## Data model

`core/data.py` stays dependency-free.

### `Calibration` (extended)

Already exists and is already referenced by `R0:pwm0` as
`Calibration("pump_0")`.

| field | meaning |
| --- | --- |
| `file` | file stem, e.g. `pump_0` -> `pump_0.json` |
| `a`, `b` | `flow = a * duty + b` |
| `min_duty` | stall floor; below this the pump does not turn |
| `max_duty` | upper duty bound, defaults to `MAX_OUTPUT` |
| `dispense_duty` | duty used for volume boluses |
| `points` | `list[tuple[float, float]]` of measured `(duty, flow)` |
| `fitted_at` | ISO timestamp; empty string means **unfitted** |
| `r2` | fit quality, informational - it is trivially 1.0 for two points |

A calibration with `fitted_at == ""` is unfitted, and any flow or volume config
against it is rejected.

### `OutputUnit(StrEnum)`

`duty` | `flow` | `volume`. `duty` is the default and is exactly today's
behaviour.

### `ControlConfig`

Gains `output_unit: OutputUnit = OutputUnit.duty`. Nothing else changes;
`value`, `setpoint`, `lb` and `ub` keep their names and simply carry different
units depending on `output_unit`.

`dispense_duty` lives on the calibration, not on `ControlConfig`: it is a
property of the pump, not of the control strategy.

## Calibration subsystem

### Storage

One JSON file per pump in `$REACTORS_CALIBRATION_DIR`, defaulting to
`~/.reactors_czlab/calibrations/`, named from `Calibration.file`.

Writes are atomic - temp file plus `os.replace` - so a power cut cannot leave a
half-written calibration behind.

Loading is **explicit**, called from `run_server` after the actuators are built,
in the same spirit as `init_hardware()`. Never at import time.

### The run

A `CalibrationRun` per actuator, driven by five OPC methods:

| method | does |
| --- | --- |
| `calibrate_point(duty, seconds)` | drives the pump at `duty` for `seconds`, then zero; stores the **measured** elapsed time as a pending point |
| `record_point(volume_ml)` | the operator's measured mL completes the pending point as `(duty, volume / elapsed_min)` |
| `fit_calibration()` | OLS over collected points -> `a`, `b`, `r2`, `fitted_at`; saves and installs |
| `clear_points()` | discard collected points, keep the installed calibration |
| `reload_calibration()` | re-read the file - the runtime reload path |

Elapsed time is **measured** with `perf_counter`, not assumed from the
requested `seconds`: `asyncio.sleep` drifts, and that drift would go straight
into the flow estimate.

### Derived values

- `min_duty` defaults to the fitted x-intercept `-b / a`, clamped to `>= 0`,
  then raised to the highest duty point that measured zero volume - the
  observed stall floor.
- `dispense_duty` defaults to `max_duty`. A fixed timing error of roughly one
  fast-loop tick costs more volume at a higher duty, so lowering it on the
  bench buys dose accuracy at the cost of stall margin. It is writable over OPC
  for that reason.

### Interlock

`Actuator.calibrating` is set for the duration of a run. `write_output()` and
`tick()` both return immediately while it is set, so the sampling loop and the
fast loop leave the pump alone and no controller fights the calibration.

- A run refuses to start if one is already in flight on that actuator.
- `seconds` is bounded (1 - 600).
- A fit needs at least two distinct duty points.
- Abort, or any exception, drives the pump to 0 and clears the flag in a
  `finally`.

## Dispenser

```python
class Dispenser:
    total_volume: float                            # mL delivered, cumulative
    def duty(self, demand: float) -> float         # a fresh control decision
    def tick(self) -> float | None                 # 20 Hz; a duty to write, or None
    def demand_limits(self, period: float) -> tuple[float, float]
```

The constructor takes an injectable `clock` defaulting to `perf_counter`, so
bolus timing is tested deterministically instead of with `sleep`.

### Per unit

- **`duty`** - identity; `tick()` always returns `None`. Bit-for-bit today's
  behaviour.
- **`flow`** - `duty = (demand - b) / a`, clamped into `[min_duty, max_duty]`.
  A demand of `<= 0` writes 0, not `min_duty`, so off is really off. A demand
  that is positive but below the stall floor is raised to `min_duty` and logged
  at `debug`: the pump cannot turn slower, so it over-delivers. Volume mode is
  the answer for average rates below the stall floor. `tick()` returns `None`:
  flow is a level, not an event.
- **`volume`** - a demand of V mL starts a bolus: write `dispense_duty` and arm
  a deadline `60 * V / flow(dispense_duty)` **seconds** out (`flow` is mL/min).
  `tick()` returns `0.0` once the deadline passes, otherwise `None`. A fresh
  decision arriving mid-bolus supersedes it and re-arms with the new volume.

`tick()` returning `None` means "no duty change to write". Volume accrual runs
on every `duty()` and `tick()` call regardless of the return value.

### The re-trigger guard

A bolus is an event, but `write_output()` is called repeatedly: every `period`
when the actuator is paired, and every `UNPAIRED_PERIOD` (50 ms) by the fast
loop when it is not. Unguarded, a manual 2 mL demand on an unpaired pump would
dose 2 mL forty times a second.

**In volume mode the dispenser ignores a new demand until `control_period` has
elapsed since the last accepted decision.**

Consequences:

- Paired and unpaired actuators behave identically.
- `_OnBoundariesControl` doses once per cycle for as long as it stays out of
  band, which is the desired behaviour for pH correction.
- Manual plus volume degrades into a duty-cycled slow feed rather than a
  disaster.

`Reactor.__init__` stamps its `period` onto each actuator as `control_period`,
so `run_server` needs no change. An actuator that is not owned by a reactor -
in tests, or on the bench - falls back to a module constant
`DEFAULT_CONTROL_PERIOD = 10.0`, matching `SAMPLE_PERIOD`. The fallback is
deliberately non-zero: a zero period would disable the guard entirely.

### Volume accounting

`total_volume` integrates the **actual** duty over the **actual** elapsed time,
not the sum of demanded volumes. A superseded, clipped or interrupted bolus
therefore still totals correctly. `total_volume` survives a control-config
change: it records the physical pump, not the configuration.

### Demand limits

`demand_limits()` supplies the controller's `min_val` / `max_val` in the
config's own unit:

| unit | limits |
| --- | --- |
| duty | `(0, MAX_OUTPUT)` |
| flow | `(0, a * max_duty + b)` mL/min |
| volume | `(0, flow(dispense_duty) * period / 60)` mL - what one period can actually deliver |

PID clamping and its anti-windup band therefore land in engineering units
automatically.

**`_PidControl.kp` defaults to `100.0`, which is tuned for 0 - 4095 counts and
is wildly wrong in mL/min.** Gains must be retuned per unit. This is documented,
not worked around.

### Rejecting bad configs

A flow or volume config against a channel with no fitted calibration is
refused: logged at `warning` and the previous controller kept, the same shape
as the existing `TypeError` path in `set_control_config`. Silently treating
mL/min as raw counts would peg a pump.

## Actuator

```python
def write_output(self, sens_value: float) -> None:
    if self.calibrating:
        return
    if sens_value == ERROR_VALUE:      # see below
        return
    demand = self.controller.get_value(sens_value)
    self._write_if_changed(self.dispenser.duty(demand))

def tick(self) -> None:
    if self.calibrating:
        return
    value = self.dispenser.tick()
    if value is not None:
        self._write_if_changed(value)
```

`set_control_config` additionally builds the new dispenser from the config unit
plus the channel calibration, replaces it only when it differs, **preserves
`total_volume` across the replacement**, and creates the controller with
`min_val` / `max_val` taken from `dispenser.demand_limits(self.control_period)`.

### The `ERROR_VALUE` fix

`update_paired_actuators()` currently passes the sensor channel value straight
through, including `ERROR_VALUE` (-0.111) when a read failed. Today that means
a controller acts on a bogus number. Once the actuator is a pump, a failed pH
probe reading -0.111 makes `_OnBoundariesControl` dose base forever.

`write_output()` therefore skips the decision when `sens_value == ERROR_VALUE`
and holds the last output. This is a behaviour change for the existing control
modes and is deliberate.

## Reactor

`unpaired_loop()` is renamed `actuator_loop()`; it now does two jobs and the old
name would be a lie. It keeps refreshing unpaired actuators at 20 Hz, then
ticks every actuator, paired or not:

```python
async with self.unpaired.lock:
    for aid in self.unpaired.actuators:
        self.actuators[aid].write_output(UNPAIRED_INPUT)
for actuator in self.actuators.values():
    actuator.tick()
```

No new lock. `write_output()` and `tick()` are both synchronous and never
await, so the sampling loop's decision and the fast loop's bolus termination
cannot interleave inside a dispenser.

Two call sites move: `run_server.py:169` and one test.

`Reactor.stop()` additionally cancels any in-flight bolus, so a restart does not
resume one.

## OPC surface

On the actuator node:

- `output_unit` - writable `UInt32` with an `EnumStrings` property, exactly like
  the existing `method` variable. Read in `datachange_notification` and placed
  on `ControlConfig`.
- `total_volume` - published read-only. Under the browse-name contract this is
  `R0:pwm0:total_volume`, so `OpcClient.match_tree` files it into the `data`
  table with no client change, and `run_plots.py` can filter on it like any
  other channel.
- `cal_a`, `cal_b`, `r2` - published read-only.
- `min_duty` and `dispense_duty` are settable at runtime through a sixth
  calibration method, `set_duties(min_duty, dispense_duty)`, rather than as
  writable variables. Writable variables would need their own subscription and
  handler alongside the control-config one, and every stray write would trigger
  a save; a method validates the pair together (a dispense duty below the stall
  floor is refused) and saves once.
- The calibration methods, added with `add_method` following the
  `write_calibration` pattern in `opcua/sensor.py:83`.

## Error handling

| condition | behaviour |
| --- | --- |
| flow/volume config on an unfitted channel | config rejected, previous controller kept, logged at `warning` so it reaches `record.log` |
| calibration file missing at startup | warning; channel stays unfitted, `duty` mode still works |
| corrupt or partial JSON | logged exception, treated as unfitted, server does not crash |
| fit yields `a <= 0` (pump reversed, garbage points) | fit rejected, old calibration kept, `fit_calibration()` returns the reason |
| `flow(dispense_duty) <= 0` | volume mode impossible, config rejected |
| exception during a calibration run | `try/finally` drives the pump to 0 and clears `calibrating` |
| bolus outlives `control_period` | next decision supersedes it; totals stay correct because they are integrated from actual runtime |
| sensor read failed (`ERROR_VALUE`) | decision skipped, last output held |

## Testing

Everything new is standard library only, so it all runs with no hardware and no
pymodbus, and no test imports `core.sensor`.

- **`test_calibration.py`** - OLS against a known line; `r2`; the fewer-than-two
  points and `a <= 0` rejections; save / load round trip into `tmp_path` with
  the directory overridden by environment variable; corrupt-file recovery.
- **`test_dispenser.py`** - duty passthrough identical to today; flow inversion
  with clamping and `demand <= 0 -> 0`; bolus arm and expiry on the fake clock;
  **the re-trigger guard**, carrying a `Regression:` note, since a 40 Hz dosing
  bug is the expensive failure here; `total_volume` integration across a
  superseded bolus; `demand_limits` per unit.
- **`test_actuator.py`** - config rejected without a calibration; `total_volume`
  survives a config change; the `calibrating` interlock blocks both
  `write_output()` and `tick()`; `ERROR_VALUE` holds the last output.
- **`test_reactor.py`** - `actuator_loop` ticks paired actuators too; the
  existing unpaired test is renamed.
- **`test_opcua_calibration.py`** - the five methods driven through the
  stub-node pattern already used by `test_opcua_pairing.py`.

## Documentation

CLAUDE.md gains:

- the units convention;
- the two new modules in the layout;
- the `unpaired_loop` -> `actuator_loop` rename;
- as a stated invariant next to the existing ones, *volume-mode decisions are
  rate-limited to `control_period`* - precisely the kind of guard that gets
  "simplified" away and turns into a 40 Hz dosing bug;
- the `ERROR_VALUE` behaviour change.

The README "To do" entries for pump calibration and volume dispense come off
the list.

## Out of scope

- Any GUI. The operator drives calibration through a generic OPC client.
- Updating `Reactor.volume` from delivered volume.
- Calibration of anything other than `PlcActuator` pumps.
- Automated calibration with a balance or flow meter.
