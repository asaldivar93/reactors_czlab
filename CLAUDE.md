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
    hamilton.py    What a CP status block MEANS. stdlib only, no pymodbus
  opcua/         Server nodes (reactor/sensor/actuator) + OpcClient
  gui/           NiceGUI web app
    address.py     AddressBook: the browse dicts -> the lookups pages make
    format.py      Rendering; ERROR_VALUE never reaches a screen as a number
    control.py     The control-config write plan (ORDER MATTERS, see below)
    state.py       AppState/STATE: the one connection, the one address book
    controllers/   Page logic that is testable without a browser
    components/, pages/   Assembly only, no decisions
  sql/           PostgreSQL schema and access
  server_info.py Hardware inventory: which sensor/actuator on which address/pin
  run_*.py, export_data.py   Entry points (each has a cli() in [project.scripts])
tests/           pytest suite. Runs with no hardware and no pymodbus.
scripts/, tests_plc/   Ad hoc bench scripts. NOT part of the package, NOT pytest suites.
```

Dependency direction: `core` never imports `opcua`. `data.py` imports nothing.

## Hard constraints — breaking these breaks a deployment

- **Python >= 3.11.** `asyncio.TaskGroup` and `enum.StrEnum` are used.
- **`pymodbus` is pinned `>=3.9`.** `BinaryPayloadBuilder`/`Decoder` lived in
  `pymodbus.payload`, which 3.9 removed; `ModbusHandler` now uses
  `convert_to_registers`/`convert_from_registers` instead, whose `word_order`
  kwarg also landed in 3.9. (There is no pymodbus 4.0 — the latest release is
  3.x.) Do not reintroduce the `pymodbus.payload` API.
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

`core/control.py` classes are `@dataclass(kw_only=True)`. **When adding a
field, decide which side it is on:**

- configuration (setpoint, gains, bounds, times, `backwards`) → default
  `compare=True`
- runtime state (`_is_on`, `_integral_sum`, `_last_time`, and `value` on every
  class except `_ManualControl`) → `field(init=False, compare=False)`

`Actuator.set_control_config` branches on *what changed*: a new output unit or
a new control method swaps the controller wholesale; a same-method config
change instead calls `controller.adopt_config(new)` — which copies only the
`compare=True` fields onto the **running** object — followed by
`refresh_derived_limits()`. So retuning a PID gain or the setpoint, or toggling
`backwards`, no longer resets the integral, the timer phase or the current
output; only a method/unit change does. `adopt_config` is why the
config/runtime split above must be right: a runtime field that is accidentally
`compare=True` would be clobbered on every in-place update. `_PidControl`
overrides `adopt_config` to also carry `_integral_band_is_default` (config in
spirit, but `compare=False`) and to reclamp the integral into the adopted band.
An unrelated OPC write that changes nothing is still a no-op.

Gains (`kp/ki/kd`), the anti-windup band (`min_integral/max_integral` plus the
`auto_integral_band` flag) and `backwards` reach the controller as
`ControlConfig` fields, exposed as writable OPC variables (`opcua/actuator.py`
`init_control_node` / `datachange_notification`). A single `{id}:backwards`
variable serves both PID and on_boundaries (an actuator runs one controller at
a time). `_PidControl.backwards` flips the error sign so two actuators on one
sensor drive opposing pumps (acid/base, heater/cooler).

Validation is `_as_float()` in `__post_init__`, not per-attribute properties.
`ControlFactory.create_control` matches on `config.method`; its `pid` branch
translates `auto_integral_band` to a `None` band (auto-derive from the range).

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

`core/modbus.py` has one `WORD_ORDER = "little"` module constant used by both
`_build_payload` and `decode` (via `convert_to_registers`/
`convert_from_registers`). The migration to the `convert_*` API was checked to
be **byte-for-byte identical** to the old `BinaryPayloadBuilder(byteorder=BIG,
wordorder=LITTLE)` output for float/uint/int, so it does not change the wire
format — but the wire format itself has **never been checked against real
hardware**. If Hamilton readings come back word-swapped or nonsensical, flip
this one constant (`"little"` ↔ `"big"`) and nothing else. Note the `convert_*`
API fixes the byte order *within* each register to big-endian and exposes no
byte-order knob, so the old `BYTE_ORDER` escape hatch is gone; only word order
is tunable now. Flag this before trusting a run.

### OPC naming contract

Browse names are `<reactor>:<name>:<channel>` — e.g. `R0:ph:pH`,
`R0:pwm0:curr_value`. `OpcClient.match_tree` splits on `:` to fill the
`reactor` / `name` / `channel` columns of the `data` table, and `run_plots.py`
filters on those columns. Changing a browse name changes the database contents
and breaks the plots.

Two sides of the same name. A *device* id is `<reactor>:<name>` —
`R0:biomass` — and that is what `set_pairing`/`unpair` validate against
(`sampling.sensors` holds full ids) and what `R{n}:pairings` publishes. The
GUI's `AddressBook` keys devices by the **middle part only** (`biomass`),
because that is what `match_tree` puts in the `name` column. `device_id()` and
`short_name()` in `gui/components/pairing.py` are the only crossing point;
three separate bugs came from crossing it by hand, each failing silently with
`biomass is not a sensor of R0` in the *server's* log and a bare `False` at
the client.

### What the server publishes only because the GUI needs it

`cal_a/cal_b/cal_r2` were the only calibration data published, and the pairing
table and CP status were not readable at all. Four additions exist purely so a
client can *show* state it could previously only change:

- `R{n}:pairings` — JSON on the reactor node. Two browse-name parts, below
  `match_tree`'s `NAME_PARTS = 3`, so it is never archived. Republished under
  `_publish_lock` from both pairing methods.
- `{id}:get_calibration` — JSON: duty limits, `fitted_at`, the fitted points,
  the in-flight run's points and pending measurement, and
  `installable_reason()` verbatim.
- `{id}:read_calibration_status` — reads a Hamilton CP without writing one.
  On demand, never published: CP status registers need administrator or
  specialist level, so every read escalates and drops the operator level on
  the RS485 bus the sampling loop shares.
- `ChannelIndex` — a **Property** (so `match_tree` skips it) on each channel
  variable, because `set_pairing` takes the index and browsing only gives
  names.

### GUI invariants that cost real debugging

- **A long OPC method call kills the session when `auto_reconnect` is on.**
  asyncua's supervisor probes every `watchdog_intervall` (1 s) with a probe
  timeout of the same length; a call outlasting it reads as a dead link and
  the session is torn down, subscription and all. Measured: 4 s or more fails,
  whatever `timeout` says. `calibrate_point` runs a pump for up to
  `MAX_RUN_SECONDS` (600), so it goes through `OpcClient.call_slow_method`,
  which opens a throwaway session. **Any future long-running method must do
  the same.**
- **Control-config writes are ordered: parameters, then `output_unit`, then
  `method` last.** `ActuatorOpc.datachange_notification` rebuilds the whole
  `ControlConfig` on *every* notification, reading only the fields the
  currently selected method needs, so writing `method` first applies the new
  controller to the previous configuration's gains for one notification —
  on a pump, that doses. `gui/control.py` builds the plan; a test pins the
  order with a `Regression:` note.
- **A rejected control config is silent.** `set_control_config` catches the
  `TypeError`, logs it and keeps the running controller. So does
  `check_unit()` for a flow/volume unit on an unfitted pump. The form asks
  `unit_rejection_reason()` *before* writing, or the operator sees a
  configuration that looks accepted and is not.
- **Elements built inside a `ui.timer` callback render but their event
  handlers never fire.** This made the control dialog's Apply and Cancel dead.
  Pass async handlers to `on_click` directly, and let a page `await` its own
  initial load rather than deferring it — a `once=True` timer can also fire
  after the client is gone and raise against a page nobody is looking at.
- **`on_value_change` handlers take an argument:** `lambda _: f()`.
- **Do not put controls inside a refreshable a timer drives.** The dashboard
  rebuilt every Configure button once a second, destroying it under the
  operator's pointer. Only the readings refresh; the cards are built once.
- **Quasar's `outline` button takes the primary colour**, which on the header
  is the header's own background — the Record button was invisible. Header
  buttons need `color=white`.
- Every sensor node carries the calibration methods, so nothing in the address
  space distinguishes a Hamilton probe from a spectral one. The sensor
  calibration screen asks each sensor and hides the ones that answer
  `unsupported`.

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

GUI tests are in three layers, matching the package: `test_gui_address.py`,
`test_gui_format.py`, `test_gui_control.py`, `test_gui_plots.py`,
`test_gui_pump_calibration.py` and `test_gui_pairing.py` are pure functions
over fixture dicts; `test_gui_state.py` drives `AppState` against a fake
`OpcClient`; `test_gui_pages.py` opens every route through NiceGUI's `user`
fixture, which builds the real element tree. `tests/gui_main.py` is the module
that fixture imports — it must call `ui.run()`, which the plugin intercepts,
or the fixture cannot find the routes. The pytest config loads
`nicegui.testing.user_plugin` only, **not** `nicegui.testing.plugin`, which
would drag in the Selenium-backed `screen` fixture and make the suite need a
browser and a webdriver.

`test_sensor_calibration.py` covers the Modbus orchestration around a Hamilton
calibration and is behind a `pytest.importorskip("pymodbus")`, so it runs
where the server extra is installed and stays out of the way of the
client-only install. What the registers *mean* is in `core/hamilton.py` and is
tested without pymodbus at all.

Run the server with no hardware at all:

```bash
uv run reactors-server --simulated --endpoint opc.tcp://localhost:4840/
```

Note `--simulated` still needs the `server` extra: `run_server.py` imports
`core.modbus` at module scope. It also builds a `RandomSensor` for the
Hamilton configs, so `read_calibration_status` answers `unsupported` and the
sensor calibration screen cannot be exercised simulated — that one needs a
real probe.

## Open items

- Modbus decode/endianness needs a bench check (above). The sensor calibration
  screen renders decoded floats, so **its numbers cannot be trusted until that
  check is done.**
- The GUI has never run on a Raspberry Pi, and the experiment interface has
  never touched a real PostgreSQL — the sql module is covered only by guard
  and statement-construction tests.
- The plots page's biomass multi-select was never opened in a browser (the
  popup would not open in the verification environment; the single-select
  beside it was). Its handler wiring is the same construct, and the
  multi-channel filtering is unit tested.
- `_TimerControl` now starts genuinely ON; previously the first ON phase lasted
  `2 * time_on`. Revert the two lines in `__post_init__` if that was deliberate.
- No authentication on the GUI; pump control is reachable from a browser URL.
- README "To do" list is the feature backlog (MFC Modbus, power-out recovery).
