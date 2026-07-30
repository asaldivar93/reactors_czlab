# Web GUI: foundation, server-side enablers, reactor dashboard

Date: 2026-07-29

## Problem

Every read/write variable and every method the OPC UA server exposes is
reachable today only from a generic OPC client (`opcua-client`) or from a
Python REPL. Pairing an actuator to a sensor, retuning a PID, calibrating a
Hamilton probe or running a pump calibration all mean browsing an address
space by hand and typing node ids. Archived data is visible only through
`run_plots.py`, a matplotlib window on the PC.

This design adds a web GUI that runs on both the PC and the Raspberry Pi,
connects to the server over OPC UA, and exposes that surface to an operator.
It is delivered in phases; **this spec covers the server-side enablers and
phase 1**, and sketches phases 2-4 only far enough that phase 1 does not
paint them into a corner. Each later phase gets its own spec and plan.

## Decisions taken during design

- **NiceGUI**, not Dash, Panel, Streamlit or hand-written FastAPI + HTMX.
  Decisive reason: NiceGUI runs on uvicorn, so the UI, the `asyncua` client
  and the archiver task share **one asyncio event loop**. Dash is WSGI and
  would need a background thread bridging to the async client plus a
  shared-state protocol; Streamlit's rerun-the-script model is wrong for a
  control panel with a long-lived connection; FastAPI + HTMX means
  hand-writing every widget and chart for the same result. NiceGUI is also
  a pure-Python wheel (`py3-none-any`), so the Pi install carries no
  compiled GUI dependency of its own.
- **The GUI process hosts the archiver.** One `OpcClient`, one connection,
  and recording is a restartable `asyncio` task the UI starts and stops.
  `reactors-client` remains as a headless entry point over the same code.
  Consequence accepted: if the GUI process dies, recording stops - the same
  exposure `run_client.py` has today.
- **The Pi defaults to a local PostgreSQL**, with `BIOREACTOR_DB_HOST`
  available to point it at the PC's database instead. The optional-import
  guard still exists for installs with no psycopg at all.
- **Concurrent experiments over disjoint reactor sets.** A reactor belongs
  to at most one active experiment; several experiments may run at once.
- **Plots are hybrid**: the database supplies the history for the selected
  window, the OPC subscription supplies the live tail.
- **`OpcClient.variables` is the GUI's read model.** The subscription
  callback writes into the dict it already maintains and NiceGUI pages read
  it on a timer. No callback fan-out, no locks, no second copy of state.
- Hamilton CP status / value / quality is an **on-demand OPC method**, not
  published variables. Polling three extra Modbus reads per sensor per
  sample period would compete with the control loop for the RS485 bus, for
  data that changes only at a calibration.
- **No authentication in any phase.** The OPC server is already
  unauthenticated on the lab network, so the GUI adds no new class of
  exposure. See Risks.

### psycopg3 on the Raspberry Pi - answered

`psycopg-binary` 3.3.4 publishes `manylinux_2_27_aarch64` /
`manylinux_2_28_aarch64` wheels for cp310 through cp313. Raspberry Pi OS
Bookworm (glibc 2.36) and Trixie (glibc 2.41) both satisfy that, so on
**64-bit** Pi OS `psycopg[binary]` installs from a wheel and the existing
`sql/` module works unchanged, with no source build and no `libpq-dev`.

Two caveats:

- 32-bit Pi OS (`armv7l`) has no wheel and would need `libpq-dev` plus a
  source build. The optional-import guard below is what covers that case.
- `pyproject.toml` declares `requires-python = ">=3.13"` while Pi OS
  Bookworm ships 3.11. See Risks - this blocks a Pi install regardless of
  the GUI.

No alternative recording backend is needed, so none is designed. The guard
exists for the 32-bit case and for a deliberately minimal Pi install.

## Architecture

One process, one event loop:

```
uvicorn / NiceGUI ──┬── browser clients (websocket)
                    ├── OpcClient  (asyncua, one connection to the server)
                    └── archiver task (queue -> psycopg via asyncio.to_thread)
```

New package `reactors_czlab/gui/`. The dependency rule in CLAUDE.md extends
to it: **`gui` may import `opcua` and `sql`; nothing imports `gui`; `core`
is untouched by the GUI.** `gui/__init__.py` stays docstring-only, for the
same reason `core/__init__.py` does.

A new `gui` extra (`nicegui`) installs the dashboard, actuator
configuration, pairing and - in phase 2 - both calibration interfaces.
Recording, experiments and plot history additionally require psycopg;
without it those screens render a disabled state naming the reason instead
of failing to import.

```
reactors_czlab/
  gui/
    __init__.py      docstring only
    state.py         AppState: the one OpcClient, availability flags,
                     recording and experiment state
    address.py       AddressBook: indexes the browsed dicts
    format.py        value rendering, ERROR_VALUE handling, staleness
    control.py       builds the ControlConfig write plan from form fields
    components/      values panel, control form, pairing panel
    pages/           dashboard.py  (later: calibration, experiments, plots)
  run_gui.py         cli() -> reactors-gui, matching the run_*.py convention
```

**Pages contain no logic.** A page assembles components; every decision -
what to show, what to enable, what to write, in what order - lives in a
plain function taking data and returning data. None of this can be tested
through a rendered browser, so anything worth testing must not live in a
page function.

## Server-side enablers

These land first and are independently testable without any GUI.

### E1 - Split the Hamilton calibration read from the write

`core/sensor.py`:

- New `HamiltonSensor.read_calibration_status(cal_point) ->
  CalibrationStatus`. Reads `cp{n}_status` (status code from registers 1-2,
  written value from registers 5-6), `quality`, and the live `pmc1` process
  value. Read-only: it stays at user operator level and performs no
  escalation.
- `Sensor.read_calibration_status()` on the base class returns an
  "unsupported" result, mirroring how `write_calibration` already
  advertises that a sensor cannot be calibrated over the bus.
- `HamiltonSensor.write_calibration()` **keeps its exact signature and
  return shape**, so `opcua/sensor.py`'s existing `{id}:calibration` method
  and any current client are unaffected. Its body becomes: escalate to
  specialist -> write the point -> call `read_calibration_status()` ->
  drop back to user in `finally`.

`core/data.py` gains a `CalibrationStatus` dataclass (status string,
quality, written value, process value). It goes there because
`opcua/sensor.py` needs the same shape, and `data.py` imports nothing, so
the dependency-set split is undisturbed.

`opcua/sensor.py`:

- New method `{id}:read_calibration_status(Cal_point) -> (Status, Quality,
  Value, Process_value)`.
- Each sensor channel variable gains a **`ChannelIndex` property**
  (`UInt32`). `set_pairing` takes a channel *index*, OPC gives channel
  *names*, and deriving the index from browse order would rely on
  `get_children()` preserving insertion order, which asyncua does not
  guarantee. Properties are Variables with one-part browse names, so
  `OpcClient.match_tree` skips them - the same reason the existing
  `EnumStrings` property on the actuator method variable is harmless.

### E2 - Schema and archiver tagging

`sql/Bioreactor.sql`, plus a `sql/migrations/2026-07-29-experiments.sql`
for databases that already exist:

- `data` gains `experiment_name TEXT NULL`. Nullable because recording
  outside any experiment is an explicit requirement.
- `experiments`: `end_date` becomes nullable (a running experiment has no
  end), `reactors` becomes `TEXT[]`, `name` becomes `NOT NULL UNIQUE`.
  Nothing has ever written to this table, so this is a fresh definition
  rather than a data migration.
- Indexes on `data(reactor, name, channel, date)` and
  `data(experiment_name)`. There is no index on `data` at all today, and
  `run_plots.py` full-scans it once a second.

`sql/operations.py`:

- `store_data` takes the experiment name (or `None`).
- New: `create_experiment`, `start_experiment`, `stop_experiment`,
  `list_experiments`, `active_experiments`, `query_experiment_data`.
  `start_experiment` enforces that the reactor set does not overlap any
  active experiment.

`opcua/client.py` gains `experiment_tags: dict[str, str]` mapping reactor
id to active experiment name, consulted when a row is enqueued. Concurrent
experiments over disjoint reactor sets fall out of that for free.

### E3 - Make the client usable without a database, and truthful live

`sql/operations.py`:

- `psycopg` becomes a guarded import with a module-level availability flag;
  every public function raises `SqlError` naming the missing dependency
  before doing anything else. Today the module cannot even be *imported*
  without psycopg, and `opcua/client.py` imports it at module scope, so the
  archiver cannot be loaded on a machine that has neither it nor polars.
- `polars` moves to a lazy import inside `rows_to_polars`, its only
  consumer. The GUI and archiver paths then never load it.

`opcua/client.py`:

- `datachange_notification` always updates the in-memory value, but
  **enqueues only when the archiver is running**. Today it enqueues
  unconditionally; with nothing draining the queue, the 1000-slot buffer
  fills and then logs an error every sample forever. Latent today, immediate
  once the GUI subscribes with recording off.
- Subscribe to **all** sensor and actuator variables, and move the
  `ARCHIVED_ACTUATOR_CHANNELS` filter from subscribe-time to enqueue-time.
  The archived set is unchanged. The GUI gains live readback of
  `cal_a/b/r2` and of every control-configuration variable, so a config
  written by another OPC client shows up in the UI. Cost: slightly more
  subscription traffic for the headless `reactors-client`.
- `start_recording()` / `stop_recording()` and a `recording` property, thin
  over the existing `start_psql` / `stop_psql`.

### E4 - Publish what the GUI cannot otherwise see

`opcua/reactor.py`:

- A read-only JSON `String` variable **`R{n}:pairings`** on the reactor
  node, rewritten whenever `set_pairing` or `unpair` succeeds. Shape:
  `[{"sensor": "R0:ph", "actuator": "R0:pwm0", "channel": 0}, ...]`.
  `reactor.sampling.pairings` is server-side Python state today and the
  pairing methods return only `bool`, so the GUI can neither show what is
  paired nor recover after a restart. The variable sits directly on the
  reactor node, above `R{n}:sensors` / `R{n}:actuators`, so `match_tree`
  never sees it and a String can never reach the FLOAT `value` column.

`opcua/actuator.py`:

- A `{id}:get_calibration() -> str` JSON method returning the installed
  `Calibration` in full - `a`, `b`, `r2`, `min_duty`, `max_duty`,
  `dispense_duty`, `fitted_at`, `points` - plus the points collected so far
  by the in-flight `CalibrationRun`. Only `cal_a/b/r2` are published today.
  Phase 1 needs `fitted_at` to warn before writing a flow or volume unit,
  because `core.dispenser.check_unit()` rejects that server-side and logs
  it where an operator will not look. Phase 2's pump-calibration screen
  reuses the same payload, including the in-flight points, which are held
  in `CalibrationRun.points` and are otherwise invisible to a second
  operator or after a page reload.

The pairing methods keep returning `bool`. Changing them to the status-string
convention the calibration methods use would break `test_opcua_pairing.py`
and any existing OPC client, for a case the GUI can pre-validate away.

## Phase 1 - dashboard, actuator configuration, pairing

### AddressBook

A pure function of `client.sensor_vars`, `client.actuator_vars` and
`client.methods`. It resolves `(reactor, name, channel) -> nodeid` and
`(reactor, owner, method) -> nodeid`, unwinding the
`<reactor>:<name>:<channel>` browse-name contract exactly once, in one
place. Reactor-level methods (`set_pairing`, `unpair`) have no owner
segment; actuator and sensor methods do. It needs no server to test -
fixture dicts are enough.

### Dashboard

Routes `/` (index of reactors with connection state) and `/reactor/{id}`.

Per reactor: connection state, recording badge, active-experiment badge.

- **Sensors panel** - one row per sensor, one value per channel with its
  units and description. A reading equal to `core.data.ERROR_VALUE` renders
  as a "read failed" state, never as `-0.111`. The comparison is against the
  constant; the literal is not repeated.
- **Actuators panel** - one card per actuator: `curr_value`,
  `total_volume`, the fitted line, a control-method summary, a paired-to
  badge, and buttons opening the configuration dialog and the pairing panel.

### Actuator configuration dialog

Fields shown follow the selected method, mirroring the `match` in
`ActuatorOpc.datachange_notification`. Gains, bounds, times, the anti-windup
band, `auto_integral_band`, `backwards` and `output_unit` are all writable.

**Write order is a safety property.** The server rebuilds an entire
`ControlConfig` on *every* variable change notification, so writing `method`
first would apply the new controller against whatever stale setpoint, bounds
and gains are still sitting in the server's variables - a manual -> pid
switch could drive hard for one notification. The write plan is therefore
**parameters first, `output_unit` next, `method` last**: every intermediate
notification keeps the old method, and only the final write commits the new
one. `gui/control.py` builds that plan and a test asserts the ordering with
a `Regression:` note.

Before writing `output_unit` as flow or volume, the form checks
`get_calibration()`: an unfitted calibration means `check_unit()` will
reject the config server-side, so the form refuses and says why rather than
writing a configuration that is silently dropped.

### Pairing panel

A table of current pairings read from `R{n}:pairings`, each row with an
Unpair button. An add form: sensor -> channel (labelled by name, submitted
as the `ChannelIndex` value) -> actuator, listing only actuators that are
not already paired.

The GUI pre-validates everything `_validate_pair` and `set_pairing` check -
reactor membership, and that the actuator is not already paired - so a
`False` return is genuinely unexpected and is reported as "the server
refused; check record.log" rather than being the normal failure path.

## Later phases

Sketched only. Each gets its own spec.

- **Phase 2 - Calibration.** Hamilton page: CP1 and CP2 side by side per
  sensor, each with status, stored value, sensor quality and the live
  process value, refreshed on demand through `read_calibration_status`; a
  value entry per point calling the existing `{id}:calibration` method, with
  the two points settable independently. Pump page: drives a full
  `CalibrationRun` (`calibrate_point` -> `record_point` -> repeat ->
  `fit_calibration`, plus `clear_points`, `reload_calibration`,
  `set_duties`), showing the collected points and the fitted line from
  `get_calibration()`. A run drives a pump, so the UI locks pairing and
  configuration changes for that actuator while one is active.
- **Phase 3 - Experiments and recording.** Recording start / stop / resume
  in the app header, independent of any experiment. Experiments page:
  create with a reactor set, start (rejecting a set that overlaps an active
  experiment), stop, list, export to CSV. Starting an experiment starts
  recording if it is not already running; the two states are shown
  separately so the operator can see which is which.
- **Phase 4 - Plots.** Per reactor: pH, dissolved oxygen, temperature and
  biomass panels; multi-select over biomass channels; a window selector
  (2 h, 24 h, and so on); ECharts scatter on a datetime axis with
  `dataZoom`. History from the database on window change, live tail
  appended from `OpcClient.variables` on a timer. Panels are defined by a
  `(name, channel)` filter list - the same shape as today's `PLOT_FILTERS` -
  so adding actuator panels later is a configuration entry, not a rewrite.

## Testing

`tests/` must keep running with no hardware, no pymodbus and no psycopg.

- `AddressBook`, the format helpers and the enable/disable predicates are
  pure functions over fixture dicts.
- The control write plan is tested against a fake client recording the order
  of writes, asserting `method` is written last. `Regression:` note.
- The new OPC methods and the `pairings` variable are tested through a stub
  node capturing the callbacks, exactly as `test_opcua_pairing.py` does.
  This matters because `tests/` deliberately does not import `core.sensor`,
  so `read_calibration_status` cannot be unit tested directly there.
- `sql` gets guard tests - every public function raises `SqlError` when
  psycopg is absent - and statement-construction tests. No live-database
  tests.
- Page functions are not tested. That is the reason they hold no logic.

## Risks and open items

- **`requires-python = ">=3.13"` blocks a Pi install.** Raspberry Pi OS
  Bookworm ships Python 3.11 and CLAUDE.md documents 3.11 as the floor.
  This is independent of the GUI and must be resolved before anything runs
  on the Pi: either move the Pi to Trixie, or drop the floor back to 3.11
  after confirming no 3.12/3.13-only syntax is in use.
- **polars 1.43 publishes only a pure-Python wheel plus an sdist**, which
  wants a check on the Pi. Making the import lazy contains the risk: the
  GUI and archiver paths never load it.
- **The Modbus word order is still unverified against hardware**
  (CLAUDE.md, "Modbus byte order - UNVERIFIED"). The Hamilton calibration
  screen in phase 2 renders decoded floats, so its numbers cannot be
  trusted before that bench check. The screen carries the caveat visibly.
- **Two databases can diverge.** With PostgreSQL on the Pi and on the PC,
  running the archiver from both at once produces two divergent copies.
  Recording is expected to be driven from one machine at a time; nothing
  enforces it.
- **No authentication.** Pump control becomes reachable from a browser URL.
  Acceptable on an isolated lab network - the OPC server is already
  unauthenticated there - and a decision to revisit before the GUI is on a
  routable network.
