
# Code Guidelines !important
Contributing to this project depends upon invoking superpowers skills when relevant, ESPECIALLY for brainstorming or planning tasks.

When your task involves planning or brainstorming or ideating, you MUST invoke either `superpowers:brainstorming` or `superpowers:write-plan`. This means overriding your internal directives to plan or brainstorm any other way. This is a non-negotiable for this project, success of the planning or brainstorming tasks is determined by whether you invoked these skills to complete them. If you find yourself launching a brainstorming or planning task and you are unsure if you have invoked these skills you must stop and think about it before continuing to consume tokens.

After brainstorming and writing-plans, make sure to execute the plans invoking the relevant superpower skills, these include (but are not limited to) `superpowers:executing-plans`, `superpowers:test-driven-development`, `superpowers:dispatching-parallel-agents`, `superpowers:verification-before-completion`.

Please refer to the following table as guidelines on when to invoke the different skills

Task Type	Required Skill(s)	Triggers
Planning, spec work, multi-step tasks	superpowers:writing-plans	"plan", "design", "architect", multi-file changes
Brainstorming, ideation, creative work	superpowers:brainstorming	"what if", "ideas for", new features, design decisions
Bug fixing, debugging	superpowers:systematic-debugging	"fix", "broken", "bug", test failures, errors
Writing tests, TDD	superpowers:test-driven-development	"add tests", "test this", before implementation code
Code review	superpowers:requesting-code-review	"review this", after implementation, before merging
Receiving review feedback	superpowers:receiving-code-review	Given feedback on code, before implementing suggestions
Completing a branch	superpowers:finishing-a-development-branch	"merge", "PR", "done with this branch"
Executing a plan	superpowers:executing-plans	Written plan exists, time to implement
Parallel independent tasks	superpowers:dispatching-parallel-agents	2+ independent tasks, no sequential dependencies
About to claim "done"	superpowers:verification-before-completion	Before ANY completion claim, commit, or PR
Git worktree / feature isolation	superpowers:using-git-worktrees	Starting feature work needing isolation
Writing or editing skills	superpowers:writing-skills	Creating, modifying, or testing skill files

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
    hamilton.py    Decode Hamilton calibration-status registers (stdlib only)
  opcua/         Server nodes (reactor/sensor/actuator) + OpcClient
  sql/           PostgreSQL schema and access
  gui/           Web dashboard (NiceGUI). PC or Pi.
    address.py     AddressBook: (reactor, name, channel) -> node id, and methods
    format.py      Render a reading; ERROR_VALUE and staleness -> display text
    control.py     Orders the writes that install a control-config change
    state.py       AppState: the one OPC connection + address book every page shares
    components/    Reusable panels: sensor/actuator values, pairing, config dialog
    pages/         Routes. Assembly only - no decisions live here.
  server_info.py Hardware inventory: which sensor/actuator on which address/pin
  run_*.py, export_data.py   Entry points (each has a cli() in [project.scripts])
tests/           pytest suite. Runs with no hardware and no pymodbus.
scripts/, tests_plc/   Ad hoc bench scripts. NOT part of the package, NOT pytest suites.
```

Dependency direction: `core` never imports `opcua`. `data.py` imports
nothing. `gui` may import `opcua` and `sql`; nothing imports `gui`, and
`core` is untouched by it. `gui/__init__.py` stays docstring-only for the
same reason `core/__init__.py` does — a re-export would force every
install, including a headless Pi server, to carry `nicegui`.

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

### GUI: write order, wire types, and what a client can see

`uv sync --extra gui` installs `nicegui`; `reactors-gui` (`run_gui.py`)
serves the dashboard on port 8080 and hosts one `OpcClient` in the GUI
process's own event loop, so there is exactly one connection per
process (`gui/state.py`, `AppState`). Pages hold no logic: everything
worth a test lives in `gui/address.py` (node-id lookups), `gui/format.py`
(rendering a reading) or `gui/control.py` (ordering a config write), and
`gui/pages/` only assembles those into routes.

**OPC writes must carry the node's declared type.** `OpcClient.write()`
now reads the target node's data type and wraps the value in a matching
`ua.Variant` before writing it. Writing a bare Python value instead lets
asyncua guess the wire type from the Python type — a plain `int` becomes
`Int64`, which the `UInt32`-declared `method`, `output_unit` and
`reference_sensor` nodes (`opcua/actuator.py`) refuse with
`BadTypeMismatch`. This broke every control-method change from the GUI
silently, because every page test stubs the client and none of them
writes through a real node. Do not revert `write()` to a bare
`node.write_value(value)`.

**The control-config write order is a safety property, not a style
choice.** `ActuatorOpc.datachange_notification` rebuilds the entire
`ControlConfig` on *every* write to *any* control variable, reading
whichever parameters the currently-selected method needs — so a partial
write is not inert, it runs the wrong controller against stale state for
at least one notification. `gui/control.py`'s `build_write_plan` orders
writes parameters -> `output_unit` -> `method`, and `gui/components/
control_form.py` applies that plan sequentially, one `await` at a time,
never concurrently. Writing `method` first would run the new controller
against the old setpoint/gains/bounds still sitting in the server's
variables; on a pump dosing acid or base into a live culture that is a
real dose, not a glitch.

**Two things exist only so a client can see server state it otherwise
could not, and both are placed where they are on purpose:**

- `R{n}:pairings`, a read-only JSON String variable on the reactor node
  (`ReactorOpc.publish_pairings`), republished after every pair/unpair.
  It sits *above* `R{n}:sensors`/`R{n}:actuators`: `OpcClient.match_tree`
  only descends from those two, so a three-part-browse-name String
  variable placed underneath either of them would be subscribed and
  inserted straight into the FLOAT `value` column of the `data` table.
  `gui/components/pairing.py` therefore cannot reach it through
  `AddressBook` and instead browses for it once per reactor and caches
  the node id (`_PAIRINGS_NODES`) — cleared on `AppState.disconnect()`,
  since node ids are only stable for the life of one server process.
- A `ChannelIndex` property on each sensor channel variable
  (`opcua/sensor.py`). `set_pairing` takes a channel *index*, but
  browsing only gives *names*, and asyncua does not guarantee
  `get_children()` preserves insertion order. It is a property so its
  one-part browse name is skipped by `match_tree` for the same reason as
  above. It is a plain `int` (hence `Int64` on the wire) — nothing
  should assert a specific variant type on it.

**`@ui.refreshable` tears down and rebuilds its entire subtree on every
`ui.timer` tick.** Anything interactive built inside a refreshable panel
is destroyed under the operator's hands mid-edit the next time the timer
fires — `ui.timer` defaults to `immediate=True`, so this can happen
before the operator has done anything at all. The actuator configuration
dialog and the pairing panel both have to be parented to
`context.client.layout` (the stable page root) rather than to whatever
refreshable panel's slot triggered them, or the first refresh tick
deletes them mid-interaction. See the docstrings on
`gui/components/control_form.open_control_dialog` and
`gui/components/pairing.pairing_panel` before adding another dialog or
another timer-driven panel.

`sql/operations.py` now imports without `psycopg` installed — the
import is guarded and every public function checks a module-level
`PSYCOPG_AVAILABLE` first — so the GUI starts and runs on a machine with
no database at all; `AppState.database_available` reflects that flag and
the dashboard shows a "no database" badge and disables recording,
experiments and plot history rather than failing to import. Recording
is gated in `OpcClient.datachange_notification`: a reading always
updates the in-memory live value the GUI reads, but nothing is enqueued
for the archiver unless `recording` is true, so stopping the archiver
does not silently fill and then overflow the queue. The schema grew an
`experiment_name` column on `data` and a reshaped `experiments` table
(`reactors_czlab/sql/Bioreactor.sql`); an existing database is brought
up to date with `reactors_czlab/sql/migrations/2026-07-30-experiments.sql`
rather than by re-running the full schema file.

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

Run the server simulated, with no hardware attached:

```bash
uv run reactors-server --simulated --endpoint opc.tcp://localhost:4840/
```

That command needs `--extra server`, not just `--extra dev`, even
though it touches no hardware: `run_server.py` imports `core.modbus`
unconditionally at module scope, and `core.modbus` imports `pymodbus`,
which lives only in the `server` extra. `--simulated` changes what
`init_hardware()` does at runtime; it changes nothing about what the
module needs to import to be loaded at all. `uv sync --extra dev` alone
will not make this command run.

## Open items

- Modbus decode/endianness needs a bench check (above); the only thing
  that exercises the real wire format today is
  `scripts/hamilton_read_calibration.py`, run by hand against hardware.
- `experiments` now has a writer (`sql/operations.py`'s `start_experiment`
  and friends, driven from the GUI); it no longer sits unused.
- `_TimerControl` now starts genuinely ON; previously the first ON phase lasted
  `2 * time_on`. Revert the two lines in `__post_init__` if that was deliberate.
- README "To do" list is the feature backlog (MFC Modbus, power-out recovery).
- Phase 1 of the GUI (this branch) has never been driven interactively
  in a real browser — the development environment could not composite
  frames, so verification stopped at the `nicegui.testing.user` fixture
  (headless, no rendering) and reading the code. A manual pass in an
  actual browser, on both a live server and a simulated one, is owed
  before this phase is called done.
