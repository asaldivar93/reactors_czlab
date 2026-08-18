# reactors_czlab

Controller interface for bioreactors. A Raspberry Pi PLC reads the sensors
and drives the actuators, and publishes everything over OPC UA; a PC
subscribes to that server and archives the readings in PostgreSQL.

## Install

On the Raspberry Pi:

```bash
pip install -e ".[server,gui,client]"
```

On the PC:

```bash
uv sync --extra client --extra gui --extra server --no-build-package scipy
```

psycopg installs on a Raspberry Pi. What it needs at runtime is libpq:

```bash
sudo apt install libpq5
```

## Running

Start the server on the Pi:

```bash
reactors-server --endpoint opc.tcp://10.10.10.20:55488/
```

```bash
reactors-gui --endpoint opc.tcp://10.10.10.20:55488/ --port 8080 --period 10
```

Run it on a laptop with no hardware attached:

```bash
uv run reactors-server --simulated --endpoint opc.tcp://localhost:4840/
```

```bash
uv run reactors-gui --endpoint opc.tcp://localhost:4840/ --port 8080 --period 10
```

Then open `http://<host>:8080`. It listens on all interfaces, so the Pi
can serve it to a laptop on the same network.

**It replaces `reactors-client`, it does not run beside it.** The GUI
process hosts the archiver itself, so running both against one database
inserts every reading twice.

`--period` tells the GUI the server's sampling period so it can grey
out a reading that has stopped arriving; it defaults to 10 s and must
match `SAMPLE_PERIOD` in `run_server.py` to be useful.

There is **no authentication**. Pump control is reachable from a
browser URL by anyone who can route to the port. That is acceptable on
an isolated lab network — the OPC server is already unauthenticated
there — and wants revisiting before this is on a routable network.

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
- Exercise the experiment interface against a real PostgreSQL
- Authentication, before the GUI is on a routable network

Non essential:

- Actuator traces on the plots page. The panels are a
  `(name, channel)` filter list, so this is an entry in
  `gui/controllers/plots.py`'s `PANELS`, not a rewrite.
