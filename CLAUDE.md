# CLAUDE.md

Bioreactor controller. A Raspberry Pi PLC reads sensors / drives actuators and
publishes over OPC UA; a PC subscribes and archives to PostgreSQL.
User-facing install and run instructions live in [README.md](README.md) — this
file is the stuff that is expensive to re-derive from the source.

## Layout

```
reactors_czlab/
  core/          Pi-side domain logic
    hardware.py    ONLY module allowed to touch PLC libraries
    data.py        Dataclasses + ERROR_VALUE + MAX_OUTPUT (no deps)
    control.py     Control strategies (dataclasses)
    actuator.py    Actuator ABC + RandomActuator + PlcActuator
    sensor.py      Sensor ABC + RandomSensor + HamiltonSensor + SpectralSensor
    modbus.py      ModbusHandler (RS485, pymodbus)
    reactor.py     Reactor: the two loops + pairing state
    calibration.py Pump calibration: fit, store, reload, run state machine
    dispenser.py   Demand (mL/min or mL) -> duty, bolus timing, volume totals
  opcua/         Server nodes (reactor/sensor/actuator) + OpcClient
  sql/           PostgreSQL schema and access
  server_info.py Hardware inventory: which sensor/actuator on which address/pin
  run_*.py, export_data.py   Entry points (each has a cli() in [project.scripts])
tests/           pytest suite. Runs with no hardware and no pymodbus.
scripts/, tests_plc/   Ad hoc bench scripts. NOT part of the package, NOT pytest suites.
```

Dependency direction: `core` never imports `opcua`. `data.py` imports nothing.

## Hard constraints — breaking these breaks a deployment

- **Python >= 3.11.** `asyncio.TaskGroup` and `enum.StrEnum` are used.
- **`pymodbus` is pinned `<3.9`.** `BinaryPayloadBuilder`/`Decoder` live in
  `pymodbus.payload`, which later releases remove. Unpinning requires porting
  `ModbusHandler` to `convert_from_registers`/`convert_to_registers`.
- **The `server` and `client` extras are independent.** The Pi has no psycopg;
  the PC has no pymodbus. So:
  - `reactors_czlab/__init__.py` and `core/__init__.py` must stay
    **docstring-only**. Adding a re-export there forces every install to carry
    both dependency sets.
  - `sql/` must not import `core.sensor` or `core.modbus`.
- **`core/hardware.py` is the only module that may import `librpiplc`, `board`,
  `busio` or `adafruit_tlc59711`**, and it does no hardware work at import time.
  Entry points call `init_hardware()` explicitly. Never move hardware setup back
  to module scope — it was there before and made the package untestable.
  `core/sensor.py` imports `adafruit_as7341` under `if IN_RASPBERRYPI` only.
- **`core/calibration.py` and `core/dispenser.py` are standard library only.**
  They run on the Pi, which has neither numpy nor psycopg. The calibration fit
  is an ordinary least squares written out by hand for that reason.

## Model you need to hold

### Two loops, one pairing table

Every actuator starts **unpaired** and is refreshed by `Reactor.actuator_loop()`
at 20 Hz from its own controller, with a dummy reference value of
`UNPAIRED_INPUT` (0.0) — fine for manual/timer, meaningless for pid/on_boundaries.
The same loop also calls `tick()` on every actuator, paired or not, so it is
where a volume bolus in flight gets ended — a paired actuator only gets a new
*decision* once per sample `period`, far too coarse to end a dose measured in
fractions of a second.

`ReactorOpc`'s `set_pairing(sensor_id, actuator_id, channel_index)` OPC method
moves an actuator out of `reactor.unpaired.actuators` and into
`reactor.sampling.pairings[sensor_id]`, where `Reactor.sampling_loop()` drives it
from that sensor channel once per `period`. `unpair` reverses it.

- `sampling.pairings` is a `defaultdict(list)`. Keep it that way; the plain dict
  version raised KeyError on the first pairing.
- `sampling.sensors` / `sampling.actuators` are the id lists used for validation.
  An actuator is never in both the paired and unpaired lists — validation that
  requires both is always false (this was a real bug).
- `sample_ready` is an `asyncio.Event` that `ReactorOpc.update()` **must clear**
  after waiting, or it latches set and the publish loop free-runs.

### Controllers: config compares, runtime state does not

`core/control.py` classes are `@dataclass(kw_only=True)`. `Actuator.set_control_config`
replaces the controller only when `!=`, so a running timer/PID is not reset by an
unrelated OPC write. **When adding a field, decide which side it is on:**

- configuration (setpoint, gains, bounds, times) → default `compare=True`
- runtime state (`_is_on`, `_integral_sum`, `_last_time`, and `value` on every
  class except `_ManualControl`) → `field(init=False, compare=False)`

Validation is `_as_float()` in `__post_init__`, not per-attribute properties.
`ControlFactory.create_control` matches on `config.method`.

### Output units and the dispenser

A controller answers *what should I demand?*; `core/dispenser.py` answers *how
do I deliver that?*. `ControlConfig.output_unit` is orthogonal to
`ControlMethod`, so PID and on_boundaries drive a pump in mL/min or mL without
knowing pumps exist. Units are fixed: **duty is raw counts, flow is mL/min,
volume is mL, and the calibration line is `flow = a * duty + b`**.

- `duty` is the default and is a passthrough - today's behaviour exactly.
- `flow` inverts the line, clamped into `[min_duty, max_duty]`. A demand of 0
  writes 0, not `min_duty`.
- `volume` runs the pump at `dispense_duty` for a computed time. The bolus is
  ended by `Reactor.actuator_loop()` at 20 Hz, because a paired actuator only
  gets a decision once per `period` and a dose is far shorter than that.

**Volume-mode decisions are rate-limited to `control_period`.** `write_output()`
is called every `UNPAIRED_PERIOD` for an unpaired actuator; without the guard a
standing manual demand would be dispensed twenty times a second. Do not
"simplify" it away - `test_dispenser.py` pins it with a `Regression:` note.
`Actuator.control_period` is a validating property (mirrored by
`Dispenser.__init__`): a non-positive period raises `ValueError` rather than
silently disabling the guard, since every gap would then satisfy
`< control_period`. `Reactor.__init__` sets it from `period` for every
actuator, so a `Reactor` built with a non-positive sample period now raises at
construction time, not later at the first volume demand.

`Dispenser.total_volume` integrates the *actual* duty over the *actual*
elapsed time, never the sum of demanded volumes, so a superseded bolus still
totals correctly. It survives a control-config change: it records the pump,
not the config.

A flow or volume config against a channel with no *fitted* calibration is
rejected by `core.dispenser.check_unit()` and logged; treating mL/min as raw
counts would peg a pump. `_PidControl.kp` defaults to 100.0, which is tuned
for 0-4095 counts and is wrong in mL/min - gains must be retuned per unit.

**`Calibration.installable_reason()` (`core/data.py`) is the single authority
on whether a `Calibration` may be written to `Channel.calibration`.** It is
called by every site that can do that write - `CalibrationRun.fit()`,
`set_duties()`, `reload()`, and `calibration.load_into()` - and
`core.dispenser.check_unit()` delegates to it for the same question asked at
control-config time. It exists because the four install sites used to each
write their own version of this check and drifted apart across several review
rounds - a `<` where another used `<=`, a stall-floor check dropped in favour
of a flow check that could not see the same evidence, a guard gated behind
`is_fitted` that a hand-edited calibration file could route around by leaving
`fitted_at` empty. One of those drifts let a hand-edited file install a
calibration whose dispense duty produced exactly zero flow, which then raised
`ZeroDivisionError` out of `Dispenser._start_bolus` mid-dose. **A fifth
install site must call `installable_reason()`, not write its own arithmetic.**
It deliberately does not gate on `is_fitted` - the unfitted placeholder
`server_info.py` builds for every pump (`a=1.0, min_duty=0.0,
max_duty=dispense_duty=MAX_OUTPUT`) passes it on its own merits, and a
hand-edited file with `fitted_at` cleared must still be checked on its
numbers.

### The error sentinel

`core.data.ERROR_VALUE` (-0.111) is written to a channel when a device read
fails. It is a single constant — do not re-hardcode the literal. The server
publishes it; `OpcClient.datachange_notification` filters it so it never reaches
the `data` table.

### Modbus byte order — UNVERIFIED

`core/modbus.py` has `BYTE_ORDER` / `WORD_ORDER` module constants used by both
`_build_payload` and `decode`. `decode()` was changed to
`BinaryPayloadDecoder.fromRegisters()` (the old code passed raw ints as a byte
buffer) but **has never been checked against real hardware**. If Hamilton
readings come back byte-swapped or nonsensical, flip those two constants and
nothing else. Flag this before trusting a run.

### OPC naming contract

Browse names are `<reactor>:<name>:<channel>` — e.g. `R0:ph:pH`,
`R0:pwm0:curr_value`. `OpcClient.match_tree` splits on `:` to fill the
`reactor` / `name` / `channel` columns of the `data` table, and `run_plots.py`
filters on those columns. Changing a browse name changes the database contents
and breaks the plots.

## Conventions

- Logging is lazy `%`-style: `_logger.debug("In %s - %s", self.id, msg)`.
  Never f-strings in logging calls (these loops run at 20 Hz on a Pi).
- Assign `error_message = ...` then `raise X(error_message)` (ruff TRY003 style).
- numpydoc-style docstrings on public functions; `Raises` sections where a
  caller's correctness depends on the exception.
- ruff `line-length = 79`, `target-version = "py311"`.
- Do not add `__eq__` that compares an object to a bare id string. Objects are
  looked up through the `dict[str, ...]` collections; a custom `__eq__` also
  sets `__hash__ = None`.
- Failed device reads log at `warning` (they must appear in `record.log`, which
  is INFO-level), not `debug`.
- `Actuator.write_output()` skips the decision when the reference reading is
  `ERROR_VALUE` and holds the last output. A failed probe reads -0.111, which
  would otherwise make a boundaries controller dose forever. This applies to
  every control mode, not just the pump ones - it predates output units.
  Relatedly, `_PidControl` is the only strategy that clamps its output;
  manual, timer and on_boundaries return their value untouched, so a
  `_ManualControl` in volume mode is unbounded by design (an operator typing
  a large number gets a long dose) and demand limits are not a safety
  mechanism on their own - enforcement lives at the calibration install sites
  via `installable_reason()`, above.

## Testing

```bash
uv sync --extra dev && uv run pytest
```

`tests/` deliberately avoids importing `core.sensor` (it pulls in pymodbus).
`tests/conftest.py` provides a `FakeSensor` duck type plus `make_sensor` /
`make_actuator` factory fixtures. `RandomActuator` is used directly since
`core.actuator` has no third-party deps.

`test_opcua_pairing.py` drives the `set_pairing`/`unpair` bodies through a stub
node that captures the callbacks — no running server needed (asyncua is a base
dependency, always installed).

Several tests carry a `Regression:` note naming the bug they pin. Do not delete
those without reading them.

Run the server with no hardware at all:

```bash
uv run reactors-server --simulated --endpoint opc.tcp://localhost:4840/
```

## Open items

- Modbus decode/endianness needs a bench check (above).
- `experiments` table exists in the schema but nothing writes to it.
- `_TimerControl` now starts genuinely ON; previously the first ON phase lasted
  `2 * time_on`. Revert the two lines in `__post_init__` if that was deliberate.
- README "To do" list is the feature backlog (MFC Modbus, power-out recovery,
  GUIs).
