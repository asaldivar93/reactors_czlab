# GUI hardening: atomic config, on-demand reads, Plotly

Branch: `feat/operator-gui`. Written 2026-08-04, after the GUI was complete
and verified.

## How to use this document

You are implementing this plan. Before writing any code:

Paths in this document are relative to the repository root, not to this file.
1. **Read [AGENT.md](AGENT.md) in full**, especially "Hard constraints"
   and "GUI invariants that cost real debugging". It records things that were
   expensive to learn — the asyncua watchdog killing long calls, the
   `ui.timer` handler-wiring trap, the short-name/full-id boundary. Every one
   of those was found by something breaking.
2. **Read `.superpowers/sdd/2026-08-02-operator-gui/RESUME.md`** if it is still
   present (it is gitignored). It records what has and has not been verified,
   and the local environment's quirks.
3. Work the stages **in order**. Stage 1 is the only one fixing a verified
   defect; stages 2-8 depend on its interfaces. Stage 0 is a five-minute
   baseline check, not optional — the venv and asyncua version both moved
   since the branch was last verified.
4. Follow this project's conventions, which are not negotiable and are
   documented in CLAUDE.md: lazy `%`-style logging, `error_message = …` then
   `raise`, numpydoc docstrings, ruff line-length 79. Match the surrounding
   code's comment density — this codebase explains *why*, not *what*.
5. **Commit per stage**, with a message that says what was wrong and why the
   change is right. Do not batch stages into one commit.
6. Run `uv run pytest` after every stage. **459 passing is the floor**; a stage
   that reduces it is not finished. Lint with
   `uv run ruff check reactors_czlab tests` — 10 findings are pre-existing, do
   not "fix" unrelated files.
7. When a stage's claim can be checked against the running system, check it.
   The Verification section says how. Report what you actually observed, not
   what should have happened — several bugs on this branch were found only by
   driving a real browser against a real server.

If you find that a decision in this plan is wrong, say so and explain why
before implementing something different. The "Decisions already taken" section
lists choices the human made deliberately; those are not yours to revisit.

## Context

The operator GUI on branch `feat/operator-gui` is complete against
[gui_requirements](gui_requirements) and verified end to end — 12 commits,
459 tests, exercised against a simulated OPC server and a real PostgreSQL 16.

Two independent designs for the same feature were then compared against it —
`2026-07-29-gui-foundation-design.md` (the design behind the abandoned
`gui-foundation` branch) and `codex-gui-plan.md` (a greenfield plan from
another agent). Neither is in this repository; everything from them that
matters is reproduced below, so this document stands alone. That comparison
found ten gaps, one of them a **verified defect**. This plan closes eight of
them plus the defect; timezone handling and touch-target sizing were
deliberately excluded.

Two findings from that review are load-bearing and should not be re-derived:

### 1. Ordered writes still leak intermediate configurations (verified)

The current design writes control config as separate OPC variables in the
order parameters → `output_unit` → `method`, arguing that every intermediate
notification rebuilds the *old* method's config and is therefore a no-op.
**That holds only for a method change.** Retuning a controller in place was
measured against the running server, reading the server's own log of what it
applied:

```
R0:pwm0: _PidControl(SP: 7.0, kp: 100.0, …)     starting point
R0:pwm0: _PidControl(SP: 8.0, kp: 100.0, …)     intermediate: new SP, OLD gain
R0:pwm0: _PidControl(SP: 8.0, kp: 50.0,  …)     intended

R0:pwm1: _OnBoundariesControl(6.0,  8.0, …)     starting point
R0:pwm1: _OnBoundariesControl(9.0,  8.0, …)     intermediate: INVERTED BAND
R0:pwm1: _OnBoundariesControl(9.0, 11.0, …)     intended
```

An inverted band (`lb > ub`) reached the running controller. With a reading of
10 it evaluates `10 > 8` → forces the output OFF, where the intended band would
have held. Severity is bounded — the window is milliseconds and a *paired*
actuator only decides once per 10 s sample — so this is a latent hazard rather
than an observed incident. An **unpaired** actuator, driven at 20 Hz by
`Reactor.actuator_loop()`, is the exposed case. The fix is a single atomic
method, which removes the class of problem outright.

### 2. asyncua deduplicates identical writes — the subscription is not the cost

asyncua 2.0.1's server compares `(StatusCode, Value)` under the `StatusValue`
trigger and **ignores the refreshed `SourceTimestamp`**
(`asyncua/server/monitored_item_service.py`, `_is_data_changed`). Neither this
project's client nor the server's internal subscription supplies a filter, so
that is the effective trigger. Consequences:

- Re-writing a variable with an identical float notifies **nobody**. The 45
  `cal_*` and 240 control-config variables already emit ~zero traffic.
- The real waste is the **writes**: `ActuatorOpc.update_value()` rewrites
  `total_volume` and `cal_a/b/r2` unconditionally every 10 s
  ([opcua/actuator.py:73-80](reactors_czlab/opcua/actuator.py:73)) — ~51
  pointless writes per cycle across 3 reactors, on a Pi.
- The docstring at [opcua/actuator.py:57-64](reactors_czlab/opcua/actuator.py:57)
  claims asyncua "notifies subscribers of a write whether or not the value
  moved". **It is false.** An idle pump's `total_volume` archives nothing.

Reducing the subscription is therefore worth doing for *monitored-item state on
the Pi and a simpler mental model*, not for traffic. Decision taken: do it
anyway, and go further — see Stage 2.

### Exact inventory (3 reactors)

| Category | Count |
|---|---|
| Sensor channels | 42 |
| `curr_value` | 15 |
| `total_volume` | 15 |
| `cal_a` / `cal_b` / `cal_r2` | 45 |
| ControlMethod config variables (16 × 15) | 240 |
| **Total browsed by `match_tree`** | **357** |
| Archived | 72 |

Plus a **separate server-side** subscription of 240 monitored items
([opcua/actuator.py:104-108](reactors_czlab/opcua/actuator.py:104)).

---

## Decisions already taken — do not re-litigate

1. **Drop the server's internal ControlMethod subscription entirely.**
   `apply_control_config()` becomes the *only* path that changes a controller.
   **This deliberately breaks generic OPC clients that retune by writing
   individual variables** (`opcua-client`, ad-hoc scripts). Accepted. The
   config variables become server-written read-back only and must therefore be
   made **read-only** — a writable variable that silently does nothing is a
   worse trap than a read-only one.
2. **`total_volume` stays a step series.** Rows appear only when it changes.
   Fix the false docstring; do not add a periodic sample.
3. **Per-reactor recording**, with independent pause/resume, persisted in a
   `reactor_recording_state` table and restored on GUI startup.
4. **Plotly replaces ECharts** for the plots page.
5. Timezone handling (naive local) and touch-target sizing are **out of
   scope**.

## Invariants that must survive

From [CLAUDE.md](CLAUDE.md) — breaking these breaks a deployment:

- `core` never imports `opcua`; `data.py` imports nothing; `gui` may import
  `opcua` and `sql`; nothing imports `gui`.
- `reactors_czlab/__init__.py` and `core/__init__.py` stay docstring-only.
- `sql/` must not import `core.sensor` or `core.modbus`.
- `core/calibration.py`, `core/dispenser.py`, `core/hamilton.py` are stdlib
  only.
- `tests/` runs with **no hardware, no pymodbus, no psycopg**.
- The browse-name contract `<reactor>:<name>:<channel>` fills the `data`
  table's columns. Changing it changes the database.
- The config/runtime split on `core/control.py` dataclasses (`compare=True` vs
  `field(init=False, compare=False)`) is what makes `adopt_config` safe.
- **`OpcClient.call_slow_method` must keep being used for
  `calibrate_point`** — a call outlasting asyncua's watchdog probe tears down
  the session. Re-measure on asyncua 2.0.1 (Stage 0); keep unless proven fixed.

---

## Stage 0 — Baseline

The venv has been rebuilt on **Python 3.13** and **asyncua 2.0.1** (it was
3.11 / asyncua 1.x when the branch was verified).

1. Rebuild on 3.11, which is what the Pi ships and what CLAUDE.md pins:
   `uv venv --python 3.11 --clear && uv sync --extra dev --extra gui --extra client`
   then `uv pip install pymodbus pyserial "psycopg[binary]"` for local
   verification. Confirm **459 passed**.
2. The APIs the code depends on all survive in asyncua 2.0.1 (checked:
   `Client(auto_reconnect=…, watchdog_intervall=…)`, `UaClientState`,
   `Node.read_data_type_as_variant_type`). No migration needed.
3. **Re-measure the long-call watchdog behaviour on 2.0.1.** On 1.x, any OPC
   method call lasting ≥4 s with `auto_reconnect=True` tore the session down.
   Run a `calibrate_point` of 8 s on the shared connection and see whether the
   session survives. If 2.0.1 fixed it, `call_slow_method` may be simplified —
   but only with evidence. Record the result either way.

---

## Stage 1 — `apply_control_config()` (the verified defect)

**`core/actuator.py`** — `set_control_config(config) -> str | None`
([line 175](reactors_czlab/core/actuator.py:175)). It has two early returns
that discard the reason: the `check_unit` rejection (185-187) and the
`TypeError` from `ControlFactory.create_control` (227-230). Return the reason
string instead of `None`, and `None` on success. Keep the existing logging.
Existing callers ignore the return value and are unaffected.

**`opcua/actuator.py`**:

- `ActuatorOpc.__init__` gains `self._config_lock = asyncio.Lock()`.
- New method `{id}:apply_control_config`, in-args in this order:
  `Method` (UInt32), `Output_unit` (UInt32), `Value` (Double), `Time_on`,
  `Time_off`, `Lb`, `Ub`, `Setpoint`, `Kp`, `Ki`, `Kd`, `Min_integral`,
  `Max_integral` (Double), `Auto_integral_band` (Boolean), `Backwards`
  (Boolean). Out-args: `Accepted` (Boolean), `Message` (String).
- Body, entirely under `_config_lock`:
  1. Map the enum indices through the existing `control_method` /
     `output_unit_map` dicts; an unknown index returns
     `(False, "…")` without touching the actuator.
  2. Build **one** `ControlConfig`, populating only the fields the selected
     method needs — reuse the `match method:` shape currently in
     `datachange_notification` ([line 146](reactors_czlab/opcua/actuator.py:146)).
  3. `reason = self.actuator.set_control_config(config)`; if not None return
     `(False, reason)`.
  4. Still holding the lock, write the accepted values back to the individual
     OPC variables so read-back matches what is running.
  5. Return `(True, "<summary of what is now running>")`.
- **Delete `init_control_subscription` and `ActuatorOpc.datachange_notification`.**
  With the internal subscription gone they are dead code, and the method is the
  only write path.
- **Remove `set_writable()` from every ControlMethod variable** (lines
  188-346) — they are read-back only now.
- **Remove the `reference_sensor` variable.** It is already dead: created and
  subscribed but never read by anything. Publishing dead state is a trap.

**`gui/control.py`**: replace `build_write_plan` with
`build_config_args(method, output_unit, values) -> tuple`, returning the
argument tuple in the method's declared order. Keep `METHOD_FIELDS` — the form
still needs to know which fields to show. **Delete `unit_rejection_reason`**:
the server now returns the reason, and a client-side copy of server validation
is exactly the drift this stage removes.

**`gui/components/control_form.py`**: one `apply_control_config` call on
submit. Disable the form while the call is in flight. On return, show
`Message` and reload the form from `get_control_config()` (Stage 2) so what is
displayed is what the server accepted — including after a rejection.

**Tests** (`tests/test_gui_control.py`, `tests/test_opcua_actuator.py`):

- `Regression:` — a same-method retune applies **exactly one** configuration.
  Drive `apply_control_config` against a fake actuator recording every
  `set_control_config` call and assert `len(calls) == 1`. This is the test that
  would have caught the inverted band.
- No intermediate controller is observable: assert an `lb`/`ub` swap never
  produces a config with `lb > ub`.
- Invalid enum index, rejected unit, and bad field types each leave the
  actuator's controller unchanged and return `(False, reason)`.
- Same-method tuning preserves runtime state (integral, timer phase).
- The GUI issues one method call and **never** sequential variable writes —
  keep the old ordering test's `Regression:` note, retargeted.

---

## Stage 2 — Read-back model and subscription tiering

**Principle to state in the code and in CLAUDE.md: subscribe what you archive;
read everything else on demand.**

**`opcua/actuator.py`**:

- New `{id}:get_control_config() -> String` (JSON), mirroring the existing
  `calibration_json()` / `{id}:get_calibration` pattern
  ([line 322](reactors_czlab/opcua/actuator.py:322)). Payload: the running
  method and output-unit **names**, every config field, and
  `{"methods": [...], "output_units": [...]}` read from the server's own
  `control_method` / `output_unit_map`. That last part also closes gap 6 — the
  GUI stops hardcoding `METHOD_CODES`/`OUTPUT_UNIT_CODES` and takes the
  index↔name mapping from the server, so a reordered enum can no longer
  silently select the wrong controller.
- `update_value()` ([line 53](reactors_czlab/opcua/actuator.py:53)): gate the
  `total_volume` and `cal_a/b/r2` writes the way `curr_value` already is.
  **Correct the docstring at lines 57-64** — asyncua does *not* notify on an
  unchanged write, and `total_volume` is a step series (decision 2).

**`opcua/sensor.py`**: remove `set_writable()` from the channel variables
([line 53](reactors_czlab/opcua/sensor.py:53)). A client writing a sensor
value currently has it published and archived as if it were a reading.

**`opcua/client.py`**:

- `init_subscriptions` subscribes only variables for which `archives()` is
  True — 357 → 72 monitored items.
- New `read_many(nodeids) -> list` over `Client.read_values(nodes)`
  (one Read service call; confirmed present in asyncua 2.0.1
  `client/client.py:1463`). Nothing in the project uses it today.
- At browse time, batch-read the `Description` attribute of every sensor
  channel via `Client.read_attributes(nodes, ua.AttributeIds.Description)` —
  one round trip — and carry it into the browse dicts. This closes gap 7: the
  server already publishes `"dissolved_oxygen"` for `R0:do:ppm`, and the GUI
  currently shows a bare `ppm`.

**`gui/address.py`**: `VariableRef` gains `description`. `AddressBook` exposes
it; no other change.

**Tests**: `archives()` and the subscribed set are the same set; the
description survives browse into the `AddressBook`; `get_control_config`
round-trips every field and lists the enum options.

---

## Stage 3 — Connection lifecycle (gap 1)

`AddressBook` caches node ids at connect and never refreshes. asyncua's
auto-reconnect restores the *session*, but if the **server process** restarted,
node ids can differ and every read degrades silently to `None`.

**`gui/state.py`**:

- `generation: int`, incremented on each successful browse. Pages that cache
  anything derived from the address space compare against it.
- A supervisor task started in `connect()` watching `client.state`. On a
  transition into `CONNECTED` from `RECONNECTING`: rebrowse, rebuild the
  `AddressBook`, clear `pairing._PAIRINGS_NODES` and `_CHANNEL_INDICES`,
  re-read the running experiments, bump `generation`.
  **Verify first whether asyncua 2.0.1 restores monitored items itself** — if
  it does, do not resubscribe or every notification arrives twice.
- `writable` property: False unless `connected`. `write_variable()` and
  `call()` refuse with a clear message rather than raising from deep inside
  asyncua.

**`gui/components/shell.py`**: the existing badges already distinguish
connected / reconnecting / disconnected. Add a page-level disable: while not
`writable`, every control that writes or calls a method is disabled with a
tooltip saying why. Readings stay on screen and go stale on their own through
`format.is_stale`.

**Tests**: `AppState` driven through a fake client whose `state` is flipped —
a reconnect rebrowses and bumps the generation; writes are refused while
disconnected; caches are cleared.

---

## Stage 4 — Database schema versioning (gap 3)

Against an out-of-date database the GUI currently fails with a raw psycopg
error about a missing column.

**Schema** (`sql/Bioreactor.sql`, `sql/migrations/`):

- `schema_migrations(version TEXT PRIMARY KEY, applied_at TIMESTAMP NOT NULL)`.
- Every migration file is `NNNN-name.sql`, wrapped in a transaction, ending
  with its own `INSERT INTO schema_migrations`. The existing
  `2026-08-02-experiments.sql` becomes `0001-experiments.sql`.
- `Bioreactor.sql` creates the current schema *and* stamps every version as
  applied, so a fresh database is not asked to migrate.

**`sql/operations.py`**:

- `SCHEMA_VERSION` — the version this code requires.
- `check_schema() -> str | None` — None when usable, otherwise an
  operator-readable reason ("database is at 0001, this build needs 0002; run
  `reactors-db-migrate`"). Must behave when the table itself is missing.
- Follow the existing `require_psycopg()` pattern: one recognisable `SqlError`
  with an actionable message.

**New entry point** `reactors_czlab/run_migrate.py` → `reactors-db-migrate` in
`[project.scripts]`. Applies pending migrations in order, prints what it did.
**Never runs automatically** — a GUI that silently migrates a production
database is worse than one that refuses to start.

**`gui/state.py`**: `database_available` becomes availability **and** schema
compatibility; `database_reason` carries whichever failed. The experiments page
and plot history already key off those two properties and need no change.

**Tests**: guard tests for the new functions; `check_schema` reports a missing
table, an older version and a match; migration files parse and are ordered.
No live-database tests — the existing suite must still run with no psycopg.

---

## Stage 5 — Per-reactor recording (gap 10)

**Schema** (new migration `0002-recording-state.sql`):

```sql
CREATE TABLE reactor_recording_state (
    reactor TEXT PRIMARY KEY,
    recording BOOLEAN NOT NULL,
    updated_at TIMESTAMP(3) NOT NULL
);
```

Experiment membership stays in `experiments.reactors`; `active_experiments()`
already derives reactor → experiment, so it is not duplicated here.

**`sql/operations.py`**: `set_recording_state(reactor, recording)`,
`recording_state() -> dict[str, bool]`.

**`opcua/client.py`**: `recording` becomes per reactor —
`recording_reactors: set[str]`, `is_recording(reactor)`,
`start_recording(reactor)` / `stop_recording(reactor)`. The archiver task runs
while **any** reactor records. `datachange_notification` gates on
`info["reactor"] in self.recording_reactors` instead of a single flag. Keep the
existing invariant that nothing is enqueued while not recording — that is what
stops the 1000-slot queue filling.

**`gui/state.py`**: on connect, restore from `recording_state()`; persist on
every toggle. Expose `any_recording` for the header badge.

**`gui/components/shell.py`** / **`pages/dashboard.py`**: the header badge
summarises ("2 of 3 recording"); the per-reactor toggle lives on the reactor
page beside its active-experiment badge. Starting an experiment starts
recording for **its** reactors only.

**Tests**: rows are enqueued only for recording reactors; a reactor not
recording produces nothing while another does; state round-trips through the
fake SQL layer; the queue stays empty with everything stopped
(`Regression:` — keep the existing note).

---

## Stage 6 — Dashboard truthfulness (gaps 4, 6, 7)

**`gui/components/values.py`**, **`pages/dashboard.py`**:

- Each actuator card shows its **control method, output unit and demand**,
  from `get_control_config()` fetched on page load and after every apply. An
  operator cannot currently tell manual from PID without opening the dialog.
- Each actuator card shows **what it is paired to**, reusing the pairings JSON
  the pairing panel already reads — fetch once per page and share it.
- Each reactor page shows its **active experiment** and its **recording
  state**.
- Sensor chips carry the channel `description` (Stage 2) as a tooltip, so
  `ppm` reads as dissolved oxygen and `445` as a wavelength.

Keep the existing structure: only the readings are inside the timer-driven
refreshable; **cards and buttons are built once**. The config summary is not a
reading — refresh it on demand, not on the 1 s timer.

---

## Stage 7 — Confirmation and in-flight locking (gap 8)

**`gui/components/confirm.py`** (new): `async def confirm(title, message,
danger=False) -> bool`, a dialog returning the operator's answer. Docked
buttons, explicit height — the NiceGUI layout rules already documented.

Wrap the actions that move hardware or destroy state: **starting a pump run**,
**writing a Hamilton calibration point**, **pairing and unpairing**, **stopping
an experiment**, **`clear_points`**. Reading a calibration point does not need
one.

Independently, disable the triggering control while its call is in flight.
Today the pump-calibration buttons are gated on run state *read back from the
server*, so a double-click before the refresh can fire twice.

While a `CalibrationRun` owns an actuator, disable that actuator's Configure
button and its pairing rows. Note the server already interlocks the *actuation*
— `write_output()` and `tick()` return early while `actuator.calibrating`
([core/actuator.py:145,160](reactors_czlab/core/actuator.py:145)) — so this is
about not offering a control that will confuse, not about safety.

---

## Stage 8 — Plotly, point caps and in-memory history (gaps 2, 9)

**`pyproject.toml`**: add `plotly` to the `gui` extra.

**`gui/controllers/plots.py`** (pure, testable — extend, do not rewrite; keep
the `PANELS` filter-list shape so actuator panels stay a config entry):

- `downsample(points, limit=MAX_TRACE_POINTS) -> list` — at or below the limit,
  return everything. Above it, deterministic time-bucket downsampling
  preserving the **first, last, minimum and maximum** of each bucket, so
  spikes and endpoints survive. `MAX_TRACE_POINTS = 4000`.
- `merge_history(db_rows, memory_points)` — dedupe on `(timestamp, nodeid)`.

**`opcua/client.py`**: an optional bounded in-memory history —
`history_seconds` (0 disables) and a per-variable `deque` trimmed by age.
**Appended in `datachange_notification`, i.e. when data actually arrives** —
never on a timer. `run_gui` sets 8 h; `reactors-client` leaves it 0 so the
headless archiver keeps its current footprint. This is what makes plots useful
with no database at all.

**`gui/pages/plots.py`**: replace `ui.echart` with `ui.plotly`.

- `scattergl` traces on a datetime axis (hardware-accelerated).
- Rebuild the whole figure **only** when the reactor, window, selected
  channels or `generation` change.
- Otherwise append incrementally via `Plotly.extendTraces` through
  `ui.run_javascript`, with a per-trace point cap. If `extendTraces` proves
  awkward through NiceGUI, fall back to a full `.update()` — but measure
  before settling.
- **Do not batch on a fixed interval.** The codex plan's 3 s batching is wrong
  for this system: the sampling period is 10 s, so data arrives *more slowly*
  than that window and batching would only add latency. Keep the existing
  arrangement, which is already effectively event-driven — a short poll
  (`TAIL_SECONDS = 2.0`) that redraws **only when `append_live_point` reports
  a genuinely new point**, and does nothing otherwise. The poll exists because
  a NiceGUI page cannot safely be updated from the OPC callback, which has no
  client context; it is a cheap way to notice new data, not a batching window.
  It also coalesces for free if a fast-changing channel is ever plotted —
  `curr_value` on an unpaired actuator moves at 20 Hz, which is the case to
  keep in mind when actuator panels are added.
- Keep the pH / dissolved oxygen / temperature / biomass panels, the two
  temperature probes as separate labelled series, the biomass multi-select and
  the window selector. Keep the axis pinned to the window cutoff — that fix is
  already in and tested.
- **Benchmark four charts on the Pi browser.** If its WebGL context limit is
  below four, render only the visible chart in a tab set so inactive charts
  release their contexts.

**Tests**: downsampling never exceeds the cap, preserves endpoints and extrema,
and is deterministic; the merge dedupes; the figure is rebuilt only on the
listed changes; the memory buffer is bounded by age and grows only when a
notification arrives; a poll with no new data triggers no redraw.

---

## Testing rules

`tests/` must keep running with **no hardware, no pymodbus, no psycopg** — the
459 existing tests are the floor and must not regress. Follow the layering
already in place:

- Pure logic (`address`, `format`, `control`, `controllers/`) — fixture dicts.
- OPC methods — the stub-node pattern in `tests/test_opcua_pairing.py`, which
  captures `@uamethod` callbacks without a server. Invoke with `ua.NodeId()`
  and `ua.Variant(...)` per the note at the top of
  `tests/test_opcua_calibration.py`.
- `AppState` — the fake `OpcClient` in `tests/test_gui_state.py`.
- Routes — NiceGUI's `user` fixture (`tests/test_gui_pages.py`);
  `tests/gui_main.py` must call `ui.run()`.
- Modbus orchestration — behind `pytest.importorskip("pymodbus")`.
- Carry a `Regression:` note on any test pinning a named bug.

---

## Verification

```bash
uv run pytest
```

Then end to end against the simulated server and the local database:

```bash
uv run reactors-server --simulated --endpoint opc.tcp://localhost:4840/
```

```bash
uv run reactors-gui --endpoint opc.tcp://localhost:4840/ --port 8080
```

PostgreSQL 16 is installed locally; `bioreactor_db` exists, created from
`Bioreactor.sql`, owned by the OS user, and currently empty.

1. **The defect is gone.** Retune a running PID's setpoint and gain, and move
   an on_boundaries band upward. The server log must show **exactly one**
   `Control config update` per apply, and never an inverted band.
2. **Rejections surface.** Select a flow/volume unit on an unfitted pump; the
   dialog shows the server's own reason and the form reloads to what is
   actually running.
3. **Subscription count.** The startup log reports **72 variables, 72 of them
   archived**. Confirm the config variables are refused on a direct write.
4. **Reconnect.** Stop and restart the server under a running GUI; the page
   recovers, the address book is rebuilt, writes are refused meanwhile.
5. **Schema.** Point the GUI at a database missing the new migration; it says
   so and names `reactors-db-migrate` rather than throwing.
6. **Per-reactor recording.** Record R0 only; confirm rows land for R0 and not
   R1. Restart the GUI and confirm R0 resumes.
7. **Plots.** With ≥8 000 archived points in a window, confirm no trace exceeds
   4 000 displayed points and that extrema survive. Confirm a new point appears
   within one poll of the sample landing, and that an idle page issues no
   redraws at all. Confirm plots still work with the database stopped.
8. Re-run the browser pass over all six routes; every route must return 200
   with no server-side exception.

Still out of reach here and to be recorded, not claimed: a real Hamilton probe
(`--simulated` builds `RandomSensor`, so calibration answers `unsupported`),
anything on a Raspberry Pi, and the Modbus word order — which the sensor
calibration screen's numbers depend on.

## Documentation to update on completion

- **README**: `reactors-db-migrate`; per-reactor recording; the plotly
  dependency; and prominently, that **individual control variables are no
  longer writable** — `apply_control_config()` is the only path, which changes
  how a generic OPC client interacts with this server.
- **CLAUDE.md**: replace the "control-config writes are ordered" invariant with
  the atomic method; add "subscribe what you archive"; record the asyncua
  dedup finding so nobody re-derives it; note the dropped internal
  subscription and the read-only config variables.
