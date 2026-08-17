# reactors_czlab

Controller interface for bioreactors. A Raspberry Pi PLC reads the sensors
and drives the actuators, and publishes everything over OPC UA; a PC
subscribes to that server and archives the readings in PostgreSQL.

## Layout

| Path | Runs on | What it is |
| --- | --- | --- |
| `reactors_czlab/core/` | Pi | Sensors, actuators, control strategies, the reactor loops |
| `reactors_czlab/opcua/` | Pi + PC | OPC UA server nodes and the client |
| `reactors_czlab/sql/` | PC | PostgreSQL schema and access |
| `reactors_czlab/run_server.py` | Pi | Starts the OPC UA server |
| `reactors_czlab/run_client.py` | PC | Subscribes and archives to PostgreSQL |
| `reactors_czlab/run_plots.py` | PC | Live plots of the archived data |
| `reactors_czlab/export_data.py` | PC | Dumps the archive to csv |
| `scripts/`, `tests_plc/` | Pi | Ad hoc bench scripts, not part of the package |

Requires Python 3.11 or newer (`asyncio.TaskGroup`, `enum.StrEnum`).

## Install

On the Raspberry Pi:

```bash
uv sync --extra server
```

On the PC:

```bash
uv sync --extra client
```

For the web GUI, on either machine:

```bash
uv sync --extra gui
```

For database recording, experiments and migrations as well:

```bash
uv sync --extra gui --extra client
```

The `server` and `client` extras are independent: a client install has
no pymodbus and a server install has no psycopg, so import the
subpackage you need rather than the top level package. `gui` is
independent of both — it carries NiceGUI and Plotly, and the screens that
need a database disable themselves with a reason when psycopg is
missing.

psycopg installs on a Raspberry Pi: its wheel is `py3-none-any`, the
core package being pure Python. What it needs at runtime is libpq:

```bash
sudo apt install libpq5
```

## Running

Start the server on the Pi:

```bash
uv run reactors-server --endpoint opc.tcp://10.10.10.20:55488/
```

Run it on a laptop with no hardware attached:

```bash
uv run reactors-server --simulated --endpoint opc.tcp://localhost:4840/
```

Archive to PostgreSQL from the PC:

```bash
uv run reactors-client --endpoint opc.tcp://10.10.10.20:55488/
```

## The web GUI

```bash
uv run reactors-gui --endpoint opc.tcp://10.10.10.20:55488/ --port 8080
```

Then open `http://<host>:8080`. It listens on all interfaces, so the Pi
can serve it to a laptop on the same network.

**It replaces `reactors-client`, it does not run beside it.** The GUI
process hosts the archiver itself, so running both against one database
inserts every reading twice. Recording is selected per reactor,
independent of any experiment. The selection is persisted in PostgreSQL:
if the GUI process dies, archiving stops while it is down and the selected
reactors resume when it restarts.

The screens are:

| Route | What it does |
|---|---|
| `/` | The reactors and recording summary |
| `/reactor/{r}` | Live values, per-reactor recording, controller configuration, pair/unpair |
| `/reactor/{r}/plots` | pH, dissolved oxygen, temperature, biomass |
| `/reactor/{r}/autotune` | Set up, monitor and apply pH PID autotuning |
| `/reactor/{r}/calibration/sensors` | Hamilton CP1 and CP2 |
| `/reactor/{r}/calibration/pumps` | A full pump calibration run |
| `/experiments` | Create, start, stop and export experiments |

`--period` tells the GUI the server's sampling period so it can grey
out a reading that has stopped arriving; it defaults to 10 s and must
match `SAMPLE_PERIOD` in `run_server.py` to be useful.

There is **no authentication**. Pump control is reachable from a
browser URL by anyone who can route to the port. That is acceptable on
an isolated lab network — the OPC server is already unauthenticated
there — and wants revisiting before this is on a routable network.

Live plots and csv export:

```bash
uv run reactors-plots --hours 12 --reactors R0 R1 R2
```

```bash
uv run reactors-export --out run.csv --range 24 --units h
```

## Database

Create the schema once:

```bash
psql -f reactors_czlab/sql/Bioreactor.sql
```

A database created by an older build must be migrated explicitly. The
runner applies every pending migration in order and is safe to run again:

```bash
uv run reactors-db-migrate
```

The GUI checks the recorded schema version at startup. Database-dependent
features stay disabled with the migration command shown until the schema is
current; the server and live OPC displays continue to work.

Connection settings come from the environment, defaulting to the
`bioreactor_db` database as the current OS user:

`BIOREACTOR_DB_NAME`, `BIOREACTOR_DB_USER`, `BIOREACTOR_DB_HOST`,
`BIOREACTOR_DB_PORT`, `BIOREACTOR_DB_PASSWORD`.

## Pairing sensors to actuators

Every actuator starts unpaired and is refreshed by `actuator_loop` at 20 Hz
from its own controller. Calling the `<reactor>:set_pairing` OPC method with
a sensor id, an actuator id and a channel index moves that actuator into
`sampling_loop`, where it is driven from the paired sensor channel once per
sample period. `<reactor>:unpair` hands it back.

## Configuring actuators over OPC UA

Control configuration is one atomic OPC method call:
`<actuator>:apply_control_config`. It returns an accepted flag and the
server's validation message; `<actuator>:get_control_config` returns the
configuration that is actually running plus the server's enum options.

**The individual control variables (`method`, `output_unit`, gains, bounds,
times, and related fields) are read-only.** Generic OPC clients that used to
write those variables one at a time must call `apply_control_config()`
instead. This prevents the 20 Hz actuator loop from observing a partially
written configuration.

## Calibrating a pump

A pump's channel has a `Calibration` slot (`file = "R0_pwm0"`, etc.) that
converts between raw duty counts and mL/min. To fit one from the OPC client:

1. `calibrate_point(duty, seconds)` — run the pump at `duty` for `seconds`.
2. Measure the volume that actually came out, in mL.
3. `record_point(volume_ml)` — attach that measurement to the point just run.
4. Repeat steps 1-3 for at least two different duties.
5. `fit_calibration()` — fit, store and install the line.

Three more methods sit on the same actuator node:

- `clear_points()` — throw the collected points away and start the
  measurements over. The installed line is left alone.
- `set_duties(min_duty, dispense_duty)` — adjust the stall floor and the
  duty a volume bolus is dispensed at, without refitting. Lowering
  `dispense_duty` is how you trade dosing speed for dose accuracy: a
  slower pump runs longer for the same mL, so the delivered volume is
  less sensitive to when the bolus actually stops.
- `reload_calibration()` — re-read the stored file from disk, for after
  editing it by hand.

Each returns a status string, and each refuses a change that would leave
the pump unsafe to drive: the reason comes back in that string rather
than in the log.

Calibrations are saved to `~/.reactors_czlab/calibrations/` as
`<name>.json`; the `REACTORS_CALIBRATION_DIR` environment variable
overrides that directory.

## pH PID autotuning

`/reactor/{r}/autotune` runs a relay-feedback experiment for one pH sensor
and a base/acid pump pair. It tunes pH only; temperature autotuning is out of
scope.

### Before starting

Select the pH sensor and two different pumps for this reactor. The sensor must
have exactly one pH channel, and both pumps must already be paired to that
channel. Each pump must have a fitted, usable calibration and must be
configured for PID control with **volume** output (mL) and the same pH
setpoint. Assign reagents deliberately:

- Base pump: `backwards=False`
- Acid pump: `backwards=True`

The server refuses preflight when any of these requirements is no longer true,
including an unsafe calibration, a lost pairing, or mismatched setpoints. It
also checks that each requested bolus can be delivered by that pump at its
current calibration and control period. A control period above 30 seconds is a
warning, not by itself a refusal.

### Set up and start

Enter the shared setpoint, base and acid boluses (mL), hysteresis (pH), maximum
duration (minutes), phosphate concentration (mM), and base and acid titrant
concentrations (M). The form starts with 0.20 mL boluses, 0.02 pH hysteresis,
30 minutes, 14 mM phosphate, and 0.5 M for each titrant. The 30-minute default
is editable; a run with a long relay period may need a larger duration, and no
completion time is guaranteed.

Preflight calculates the effective safety band as the portion of ±1 pH around
the setpoint that remains within pH 4.0–10.0. It also derives a combined dose
budget from reactor volume, phosphate chemistry, the effective band, and the
two titrant molarities. Leave the budget override empty to use that result. An
override must be positive and has its own explicit acknowledgement. Starting
also requires acknowledgement that the pH excursion may affect other control
loops, then a final confirmation showing the selected pumps, limits, duration,
and dose budget.

### During the experiment

The server first records a no-dose baseline, then progresses through
`baseline`, `adapting`, `settling`, and `collecting` phases. The live view
shows the bounded pH trace with setpoint, hysteresis and safety limits, relay
direction, current or adapted boluses, baseline noise, cycle counts, actual
combined dose, elapsed time, and server status. If the relay amplitude is too
small after an initial cycle, it can increase both boluses together by up to
2× while preserving their ratio and remaining within delivery and dose limits.

Use **Abort** to end the run. On Abort and every terminal path, the server
stops both selected pumps and releases the tuning interlock. It also aborts
for, among other things, a sensor error or
non-finite pH value, timeout, lost pairing/configuration, dose exhaustion, or
two consecutive readings outside the effective safety band. A failed or
aborted result should be reviewed together with its server message before
making another attempt.

### Review and use the result

An identified run reports Ku and Pu and proposes gains for **TL-PI**,
**ZN-PID**, **TL-PID**, and **SIMC**. TL-PI is the default selector. Rules
with derivative action require particular care with pH-signal quality and are
flagged in the screen.

Gains are never applied automatically. Select a candidate, review its
`kp`/`ki`/`kd`, and confirm **Apply gains** to update both selected PID
controllers together. The page also offers two confirmed, server-validated
actions for a previously applied tune:

- **Scale to current setpoint** scales the audited gains to the pumps' current
  shared setpoint and applies them to both pumps.
- **Reapply last tune** reuses the audited gains after revalidating the pump
  identities, pairings, directions, PID/volume configuration, and
  calibrations.

Started runs, terminal results, and apply, scale, and reapply attempts and
outcomes are recorded in the versioned audit document when persistence
succeeds. An audit I/O failure is reported to the operator and does not prevent
pump cleanup. The document is
`~/.reactors_czlab/calibrations/<reactor>_ph_autotune.json` (or in the
directory selected by `REACTORS_CALIBRATION_DIR`). It records the experiment
selection, chemistry, effective limits, deliveries, trace/cycle summaries,
identification when available, and the applied-gain history; it is an audit
record, not a replacement for reviewing the live process.

The run is owned by the server, not the browser. Navigating away, a GUI
disconnect, or a GUI restart does not stop an active experiment. Reopen the
autotune page after reconnecting; it reconstructs the current view from server
status rather than starting a second run.

## Tests

```bash
uv run --extra dev pytest
```

The suite under `tests/` runs without hardware and without pymodbus.
`tests_plc/` is a set of manual bench scripts, not a pytest suite.

## To do

- Mass Flow Controller Modbus
- Restore server from power out
- Restore client from power out
- Run the GUI on the Pi, benchmark its four Plotly WebGL charts, and test
  against a real Hamilton probe: the
  calibration screen's numbers depend on the Modbus word order, which
  has never been checked against hardware (see AGENTS.md)
- Exercise the experiment interface against a real PostgreSQL
- Authentication, before the GUI is on a routable network

Non essential:

- Actuator traces on the plots page. The panels are a
  `(name, channel)` filter list, so this is an entry in
  `gui/controllers/plots.py`'s `PANELS`, not a rewrite.
