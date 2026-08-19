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
reactors-gui --endpoint opc.tcp://10.10.10.20:55488/ --port 8080
```

Run it on a laptop with no hardware attached:

```bash
uv run reactors-server --simulated --endpoint opc.tcp://localhost:4840/
```

```bash
uv run reactors-gui --endpoint opc.tcp://localhost:4840/ --port 8080
```

Then open `http://<host>:8080`. It listens on all interfaces, so the Pi
can serve it to a laptop on the same network.

To recover an incomplete calibration run. If you are lucky and saved your points to a csv

```bash
reactors-server \
    --endpoint opc.tcp://10.10.10.20:55488/ \
    --import-calibration-points Rn:pwmn /absolute/path/to/calibration.csv 
```

## Pumps calibration

Pump calibration is available under each reactor's Calibration tab. Collect
at least four distinct positive-flow duty measurements; the server fits five
safe monotone, invertible models, qualifies the usable maximum from a 95%
prediction band, and selects by AIC. A zero-flow duty can be recorded
separately as stall evidence and never changes the curve fit. The GUI shows
the fitted numeric equation and evidence. PID volume doses choose duty
dynamically to target the middle of their one-second-to-sampling-period timing
window; other volume controls retain their configured fixed dispense duty.

**It replaces `reactors-client`, it does not run beside it.** The GUI
process hosts the archiver itself, so running both against one database
inserts every reading twice.

## Sampling time

The server publishes one sampling period for every reactor. It starts at
10 seconds on a new installation and can be changed to 1--30 seconds from
**Settings** in the GUI header.

## Raspberry Pi power-outage recovery

The server checkpoints its sampling period, exact device topology, pairings,
complete control configurations, and accumulated dispenser totals. The default
file is `~/.reactors_czlab/server-state.json`; set `REACTORS_STATE_FILE` or pass
`--state-file PATH` to put it elsewhere. Accepted configuration, pairing, and
period changes are saved immediately. Changed totals are saved at most once a
minute and again during graceful shutdown.

Recovery is deliberately fail-safe. Outputs and sensor readings are never
replayed, in-flight doses and calibration/autotune runs are cancelled, manual
outputs restart at zero, and PID/timer runtime memory is fresh. Paired automatic
controllers wait for a successful new sensor read. An unpaired timer begins a
new ON phase only after its reactor completes its first sampling cycle;
unpaired boundary and PID configurations restart as manual/zero. A malformed or
hardware-incompatible checkpoint is renamed with a `.rejected-<timestamp>`
suffix and the complete server starts manual/zero instead of restoring a subset.
Pump calibration files remain authoritative, and Hamilton calibration remains
owned by each sensor; neither calibration workflow is duplicated in this file.

Use `--no-state` for a diagnostic safe start that neither reads nor writes the
checkpoint:

```bash
reactors-server --no-state --endpoint opc.tcp://10.10.10.20:55488/
```

An editable systemd template is provided at
[`deploy/reactors-server.service`](deploy/reactors-server.service). Copy it to a
temporary file and replace every `@...@` placeholder:

- `@SERVICE_USER@`: the dedicated Linux account that runs the server.
- `@HARDWARE_GROUPS@`: space-separated Raspberry Pi device groups required by
  the PLC, I2C, GPIO, and serial devices (for example `gpio i2c dialout spi`).
- `@WORKING_DIRECTORY@`: the absolute repository/install directory; `record.log`
  is written here.
- `@EXECUTABLE@`: the absolute path printed by `command -v reactors-server`.
- `@ENDPOINT@`: the OPC UA endpoint, normally
  `opc.tcp://10.10.10.20:55488/`.

Install and enable the edited unit:

```bash
sudo install -m 0644 /path/to/edited-reactors-server.service /etc/systemd/system/reactors-server.service
sudo systemctl daemon-reload
sudo systemctl enable --now reactors-server.service
```

systemd creates the dedicated writable state directory at
`/var/lib/reactors-czlab`; the template stores the checkpoint there. Inspect the
service and its logs with:

```bash
systemctl status reactors-server.service
journalctl -u reactors-server.service -f
```

Verify recovery before relying on it: apply a harmless sampling-period or
control change, confirm `/var/lib/reactors-czlab/server-state.json` exists,
reboot the Pi, then check the OPC read-backs and zero physical outputs before
allowing the first new sample to arm automatic control.

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
- Test client from power out
- Exercise the experiment interface against a real PostgreSQL
- Authentication, before the GUI is on a routable network

Non essential:

- Actuator traces on the plots page. The panels are a
  `(name, channel)` filter list, so this is an entry in
  `gui/controllers/plots.py`'s `PANELS`, not a rewrite.
