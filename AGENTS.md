
# reactors_czlab

Bioreactor controller. A Raspberry Pi PLC reads sensors / drives actuators and publishes over OPC UA; a PC subscribes and archives to PostgreSQL. User-facing install and run instructions live in [README.md](README.md) — this file is the stuff that is expensive to re-derive from the source.

## Layout

Dependency direction: `core` never imports `opcua`. `data.py` imports
nothing. `gui` may import `opcua` and `sql`; nothing imports `gui`, and
`core` is untouched by it. `gui/__init__.py` stays docstring-only for the
same reason `core/__init__.py` does — a re-export would force every
install, including a headless Pi server, to carry `nicegui` and `plotly`.

## Hard constraints — breaking these breaks a deployment

- **Python >= 3.11.** `asyncio.TaskGroup` and `enum.StrEnum` are used.
- `sql/` must not import `core.sensor` or `core.modbus`.
- **`core/hardware.py` is the only module that may import `librpiplc`, `board`, `busio` or `adafruit_tlc59711`**, and it does no hardware work at import time. Entry points call `init_hardware()` explicitly. Never move hardware setup back to module scope — it was there before and made the package untestable. `core/sensor.py` imports `adafruit_as7341` under `if IN_RASPBERRYPI` only.

## Model you need to hold


### OPC naming contract

Browse names are `<reactor>:<name>:<channel>` — e.g. `R0:ph:pH`, `R0:pwm0:curr_value`. `OpcClient.match_tree` splits on `:` to fill the `reactor` / `name` / `channel` columns of the `data` table, and `run_plots.py` filters on those columns. Changing a browse name changes the database contents and breaks the plots.

Two sides of the same name. A *device* id is `<reactor>:<name>` — `R0:biomass` — and that is what `set_pairing`/`unpair` validate against (`sampling.sensors` holds full ids) and what `R{n}:pairings` publishes. The GUI's `AddressBook` keys devices by the **middle part only** (`biomass`), because that is what `match_tree` puts in the `name` column. `device_id()` and `short_name()` in `gui/components/pairing.py` are the only crossing point; three separate bugs came from crossing it by hand, each failing silently with `biomass is not a sensor of R0` in the *server's* log and a bare `False` at
the client.

### GUI invariants that cost real debugging

- **A long OPC method call kills the session when `auto_reconnect` is on.** asyncua's supervisor probes every `watchdog_intervall` (1 s) with a probe timeout of the same length; a call outlasting it reads as a dead link and the session is torn down, subscription and all. Measured: 4 s or more fails, whatever `timeout` says. `calibrate_point` runs a pump for up to `MAX_RUN_SECONDS` (600), so it goes through `OpcClient.call_slow_method`, which opens a throwaway session. **Any future long-running method must do the same.**
- **Control configuration is one atomic method call.** Individual control variables are read-only. `{id}:apply_control_config` constructs and validates the complete candidate under `_config_lock`, applies it once, and publishes the read-back variables before releasing the lock. It returns `(accepted, message)`; the GUI always reloads `{id}:get_control_config` afterward, so a rejected unit or invalid band is visible instead of looking accepted. A generic OPC client must use the method, never try to make the fields writable again or restore the old per-variable subscription.
- **Subscribe what is archived.** `OpcClient.init_subscriptions()` monitors sensor channels plus actuator `curr_value` and `total_volume`; configuration, calibration and pairing state are read on demand. The old `ActuatorOpc` internal subscription was removed with per-variable configuration writes. asyncua 2.0.1 recreates its existing live subscriptions after reconnect, so `AppState` rebrowses node ids and rebuilds the address book but must not call `init_subscriptions()` a second time — doing so duplicates notifications.
- **Elements built inside a `ui.timer` callback render but their event handlers never fire.** This made the control dialog's Apply and Cancel dead. Pass async handlers to `on_click` directly, and let a page `await` its own initial load rather than deferring it — a `once=True` timer can also fire after the client is gone and raise against a page nobody is looking at.
- **`on_value_change` handlers take an argument:** `lambda _: f()`.
- **Do not put controls inside a refreshable a timer drives.** The dashboard rebuilt every Configure button once a second, destroying it under the operator's pointer. Only the readings refresh; the cards are built once.
- **Quasar's `outline` button takes the primary colour**, which on the header is the header's own background — the Record button was invisible. Header buttons need `color=white`.
- Every sensor node carries the calibration methods, so nothing in the address space distinguishes a Hamilton probe from a spectral one. The sensor calibration screen asks each sensor and hides the ones that answer `unsupported`.

## Conventions

- Logging is lazy `%`-style: `_logger.debug("In %s - %s", self.id, msg)`. Never f-strings in logging calls (these loops run at 20 Hz on a Pi).
- Assign `error_message = ...` then `raise X(error_message)` (ruff TRY003 style).
- numpydoc-style docstrings on public functions; `Raises` sections where a caller's correctness depends on the exception.
- ruff `line-length = 150`, `target-version = "py311"`.
- Do not add `__eq__` that compares an object to a bare id string. Objects are looked up through the `dict[str, ...]` collections; a custom `__eq__` also sets `__hash__ = None`.
- Failed device reads log at `warning` (they must appear in `record.log`, which is INFO-level), not `debug`.
- Do not re-invent the wheel, If a solution for a problem is already available in a library prefer the library solution instead. You are allowed to add packages to the pyproject.toml file.
- Use the pathlib library instead of the os library for every os method for which pathlib offers a replacement.

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

uv run reactors-gui --endpoint opc.tcp://localhost:4840/ --port 8080

That command needs `--extra server`, not just `--extra dev`, even
though it touches no hardware: `run_server.py` imports `core.modbus`
unconditionally at module scope, and `core.modbus` imports `pymodbus`,
which lives only in the `server` extra. `--simulated` changes what
`init_hardware()` does at runtime; it changes nothing about what the
module needs to import to be loaded at all. `uv sync --extra dev` alone
will not make this command run.

## Open items

- README "To do" list is the feature backlog (MFC Modbus, power-out recovery).
