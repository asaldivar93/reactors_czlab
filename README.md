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
| `reactors_czlab/gui/` | Pi + PC | Web dashboard (NiceGUI) |
| `reactors_czlab/run_server.py` | Pi | Starts the OPC UA server |
| `reactors_czlab/run_client.py` | PC | Subscribes and archives to PostgreSQL |
| `reactors_czlab/run_plots.py` | PC | Live plots of the archived data |
| `reactors_czlab/run_gui.py` | Pi + PC | Starts the web dashboard |
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

The two extras are independent: a client install has no pymodbus and a
server install has no psycopg, so import the subpackage you need rather
than the top level package.

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

## GUI

A web dashboard for watching live readings, configuring an actuator's
controller and pairing sensors to actuators, built on NiceGUI. It runs
on the PC or on the Pi.

Install:

```bash
uv sync --extra gui
```

Add `--extra client` as well to get recording, experiments and plot
history — without it the GUI still runs, but those features are
disabled because `psycopg` is not installed (see `sql/operations.py`,
`PSYCOPG_AVAILABLE`).

Run:

```bash
uv run reactors-gui --endpoint opc.tcp://<pi>:55488/
```

Then open `http://<host>:8080` in a browser.

Recording needs a PostgreSQL server reachable from wherever the GUI
runs. On the Pi, either install `postgresql` locally and run
`Bioreactor.sql` there, or set `BIOREACTOR_DB_HOST` to point at the
PC's database instead. Do not record from two machines against two
separate local databases at once — there is nothing that merges them
after the fact, so you end up with two divergent copies of the run.

Bringing an existing database up to date for the GUI (the
`experiment_name` column and the `experiments` table's new shape):

```bash
psql -f reactors_czlab/sql/migrations/2026-07-30-experiments.sql
```

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
