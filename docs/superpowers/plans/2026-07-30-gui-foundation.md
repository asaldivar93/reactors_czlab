# GUI Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the server-side enablers and phase 1 of the web GUI - a NiceGUI
app that connects to the OPC UA server and gives an operator a live reactor
dashboard, actuator/controller configuration and pair/unpair, on both the PC
and the Raspberry Pi.

**Architecture:** One process, one asyncio event loop: NiceGUI on uvicorn hosts
the UI, a single `OpcClient`, and the archiver task. `OpcClient.variables` -
the dict the subscription callback already maintains - is the GUI's read model;
pages read it on a `ui.timer`. Server-side work publishes the three things the
GUI cannot otherwise see (channel indices, current pairings, full calibration
state) and makes `sql`/`client` importable without psycopg.

**Tech Stack:** Python 3.11+, asyncua, NiceGUI, psycopg 3 (optional), pytest +
pytest-asyncio, ruff.

**Spec:** `docs/superpowers/specs/2026-07-29-gui-foundation-design.md`

## Global Constraints

- **Python floor is 3.11.** `pyproject.toml` says `requires-python = ">=3.11"`
  and ruff `target-version = "py311"`. No 3.12+ syntax or stdlib APIs.
- **ruff `line-length = 79`.** Run `uv run ruff check .` before every commit.
- **`core` never imports `opcua`. `gui` may import `opcua` and `sql`. Nothing
  imports `gui`.** `gui/__init__.py` and `gui/components/__init__.py` are
  docstring-only. `gui/pages/__init__.py` is the one exception: importing it
  is what registers the `@ui.page` routes.
- **`core/data.py` imports nothing. `core/calibration.py`, `core/dispenser.py`
  and the new `core/hamilton.py` are standard library only.**
- **`tests/` must pass with only `--extra dev` installed** - no pymodbus, no
  psycopg, no polars, no nicegui. Never import `reactors_czlab.core.sensor`
  from `tests/`. Modules needing an optional dependency are imported through
  a fixture that stubs it, as `tests/test_opcua_client.py` already does.
- **Logging is lazy `%`-style**: `_logger.debug("In %s - %s", self.id, msg)`.
  Never f-strings in logging calls.
- **Assign `error_message = ...` then `raise X(error_message)`** (ruff TRY003).
- **numpydoc-style docstrings on public functions**, with `Raises` sections
  where a caller's correctness depends on the exception.
- **Never re-hardcode `-0.111`.** Compare against `core.data.ERROR_VALUE`.
- **OPC browse names are `<reactor>:<name>:<channel>`.** Changing one changes
  the database contents and breaks `run_plots.py`. Any *new* variable with a
  three-part browse name under `R{n}:sensors` or `R{n}:actuators` gets
  subscribed and inserted into the FLOAT `value` column - so new non-float
  variables must go on the reactor node or be OPC *properties* (one-part
  browse names, which `match_tree` skips).
- **Test command:** `uv run pytest`. Full check: `uv run ruff check . && uv run pytest`.

---

## File Structure

**Created:**

| File | Responsibility |
|---|---|
| `reactors_czlab/core/hamilton.py` | `CalibrationStatus` + register slicing. Stdlib only, no pymodbus, so it is testable. |
| `reactors_czlab/gui/__init__.py` | Docstring only. |
| `reactors_czlab/gui/address.py` | `AddressBook`: browse dicts -> `(reactor, name, channel)` and `(reactor, owner, method)` lookups. Pure. |
| `reactors_czlab/gui/format.py` | Value rendering, `ERROR_VALUE` handling, staleness. Pure. |
| `reactors_czlab/gui/control.py` | Builds the ordered `ControlConfig` write plan; client-side unit validation. Pure. |
| `reactors_czlab/gui/state.py` | `AppState`: the one `OpcClient`, the `AddressBook`, availability flags. |
| `reactors_czlab/gui/components/__init__.py` | Docstring only. |
| `reactors_czlab/gui/components/values.py` | Sensor and actuator value panels. |
| `reactors_czlab/gui/components/control_form.py` | The actuator configuration dialog. |
| `reactors_czlab/gui/components/pairing.py` | The pairing panel. |
| `reactors_czlab/gui/pages/__init__.py` | Docstring only. |
| `reactors_czlab/gui/pages/dashboard.py` | `/` and `/reactor/{id}` routes. Assembly only, no logic. |
| `reactors_czlab/run_gui.py` | `cli()` -> `reactors-gui`. |
| `reactors_czlab/sql/migrations/2026-07-30-experiments.sql` | Migration for existing databases. |
| `scripts/hamilton_read_calibration.py` | Bench script: the one way to verify the Modbus path on hardware. |
| `tests/test_hamilton.py`, `tests/test_opcua_sensor.py`, `tests/test_sql_operations.py`, `tests/test_gui_address.py`, `tests/test_gui_format.py`, `tests/test_gui_control.py` | New suites. |

**Modified:**

| File | Change |
|---|---|
| `reactors_czlab/core/sensor.py` | `read_calibration_status`; `write_calibration` refactored onto it. |
| `reactors_czlab/opcua/sensor.py` | `read_calibration_status` method; `ChannelIndex` property per channel. |
| `reactors_czlab/opcua/reactor.py` | `R{n}:pairings` JSON variable, rewritten on every pairing change. |
| `reactors_czlab/opcua/actuator.py` | `get_calibration()` method. |
| `reactors_czlab/sql/operations.py` | Optional psycopg, lazy polars, `experiment_name`, experiment CRUD. |
| `reactors_czlab/sql/Bioreactor.sql` | `experiment_name` column, nullable `end_date`, `TEXT[]` reactors, indexes. |
| `reactors_czlab/opcua/client.py` | Enqueue gating, widened subscription, `experiment_tags`, recording API. |
| `tests/test_opcua_client.py` | Subscription assertions move to enqueue assertions. |
| `tests/test_opcua_pairing.py` | Assert the `pairings` variable tracks the methods. |
| `tests/test_opcua_actuator.py` | Assert `get_calibration()` payload. |
| `pyproject.toml` | `gui` extra, `reactors-gui` script. |
| `README.md` | Install and run the GUI; Pi PostgreSQL setup. |
| `CLAUDE.md` | The `gui` package, its dependency rule, and the new OPC surface. |

---

## Task 1: `core/hamilton.py` - calibration status, free of pymodbus

**Files:**
- Create: `reactors_czlab/core/hamilton.py`
- Test: `tests/test_hamilton.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `CalibrationStatus` (frozen dataclass: `point: str`, `status: str`,
  `quality: float`, `value: float`, `process_value: float`, classmethod
  `unavailable(point: str, status: str) -> CalibrationStatus`);
  `point_name(cal_point: float) -> str | None`;
  `build_calibration_status(point: str, status_registers, quality_registers,
  process_registers, decode) -> CalibrationStatus`;
  constants `CALIBRATION_POINTS`, `CALIBRATION_OK`, `UNSUPPORTED`, `FAILED`,
  `SUCCESSFUL`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hamilton.py`:

```python
"""Tests for the Hamilton calibration status slicing.

This is the part of the Hamilton calibration path that can be tested at
all: ``core/sensor.py`` imports ``core.modbus`` and so needs pymodbus,
which this environment deliberately does not install. The register
slicing lives in ``core/hamilton.py`` for exactly that reason.
"""

from __future__ import annotations

import math

from reactors_czlab.core.hamilton import (
    FAILED,
    SUCCESSFUL,
    CalibrationStatus,
    build_calibration_status,
    point_name,
)


def _decode(registers, cast_type):
    """Stand in for ModbusHandler.decode, keyed by the register pair."""
    values = {
        (0, 0): 0,
        (9, 9): 7,
        (1, 1): 4.01,
        (2, 2): 0.93,
        (3, 3): 7.02,
    }
    value = values[tuple(registers)]
    return int(value) if cast_type == "int" else float(value)


def test_point_name_accepts_the_two_writable_points() -> None:
    """cp1 and cp2 are the points with a writable register."""
    assert point_name(1.0) == "cp1"
    assert point_name(2.0) == "cp2"


def test_point_name_rejects_anything_else() -> None:
    """cp6 has a status block but no writable register."""
    assert point_name(6.0) is None
    assert point_name(0.0) is None


def test_point_name_rejects_non_finite_input() -> None:
    """A Float argument from a generic OPC client can be nan or inf.

    Regression: ``int(cal_point)`` raised ValueError/OverflowError
    straight out of the uamethod, so a mistyped argument took the call
    down instead of being refused.
    """
    assert point_name(math.nan) is None
    assert point_name(math.inf) is None


def test_build_reads_status_value_quality_and_process_value() -> None:
    """Each field comes from its documented register pair."""
    status = build_calibration_status(
        "cp1",
        status_registers=[0, 0, 5, 5, 1, 1],
        quality_registers=[2, 2],
        process_registers=[8, 8, 3, 3],
        decode=_decode,
    )

    assert status == CalibrationStatus(
        point="cp1",
        status=SUCCESSFUL,
        quality=0.93,
        value=4.01,
        process_value=7.02,
    )


def test_a_non_zero_status_code_is_a_failure() -> None:
    """The sensor reports 0 for a successful calibration."""
    status = build_calibration_status(
        "cp2",
        status_registers=[9, 9, 5, 5, 1, 1],
        quality_registers=[2, 2],
        process_registers=[8, 8, 3, 3],
        decode=_decode,
    )

    assert status.status == FAILED


def test_unavailable_carries_no_readings() -> None:
    """A status that could not be read reports zeros, not stale values."""
    status = CalibrationStatus.unavailable("cp1", FAILED)

    assert (status.quality, status.value, status.process_value) == (
        0.0,
        0.0,
        0.0,
    )
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_hamilton.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'reactors_czlab.core.hamilton'`

- [ ] **Step 3: Write the implementation**

Create `reactors_czlab/core/hamilton.py`:

```python
"""Hamilton Arc calibration status, decoded from holding registers.

Standard library only, and deliberately not part of ``core/sensor.py``:
that module imports ``core.modbus`` and cannot be imported without
pymodbus, which the Pi has and the test environment does not. The
register slicing is the part worth testing, so it lives here and
``HamiltonSensor`` calls into it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

#: Calibration points with a writable register in
#: ``HamiltonSensor.REGISTERS``. cp6 has a status block but no writable
#: value register, so it cannot be set over the bus.
CALIBRATION_POINTS = ("cp1", "cp2")

#: Status code the sensor reports for a calibration that succeeded.
CALIBRATION_OK = 0

#: Status strings. These reach the operator through an OPC method's
#: return value, so they are constants rather than inline literals.
SUCCESSFUL = "Successful"
FAILED = "failed"
UNSUPPORTED = "unsupported"

#: Offsets of the 32 bit values inside the register blocks. A Hamilton
#: value spans two registers, so every one of these is two wide.
_FIRST_VALUE = slice(0, 2)  # status code, and the quality block's only value
_STATUS_VALUE = slice(4, 6)  # the stored calibration value
_PROCESS_VALUE = slice(2, 4)  # the measurement inside a PMC block


@dataclass(frozen=True)
class CalibrationStatus:
    """State of one calibration point of a Hamilton sensor.

    Parameters
    ----------
    point:
        ``cp1`` or ``cp2``, or ``unknown`` when the request named
        neither.
    status:
        ``Successful``, ``failed`` or ``unsupported``.
    quality:
        The sensor's quality indicator.
    value:
        The calibration value the sensor currently holds for the point.
    process_value:
        The live PMC1 measurement, for judging whether the sensor has
        settled.

    """

    point: str
    status: str
    quality: float
    value: float
    process_value: float

    @classmethod
    def unavailable(cls, point: str, status: str) -> CalibrationStatus:
        """A status carrying no readings, for a read that did not happen.

        Zeros rather than stale values: an operator must not read a
        number off a failed request and believe it.
        """
        return cls(
            point=point,
            status=status,
            quality=0.0,
            value=0.0,
            process_value=0.0,
        )


def point_name(cal_point: float) -> str | None:
    """Name of the calibration point ``cal_point`` selects.

    The argument arrives from a generic OPC client as a ``Float``, so it
    can be ``nan`` or ``inf``, and ``int()`` raises on both. Returning
    ``None`` keeps that a refusal rather than an exception out of the
    method call.

    Returns
    -------
    str or None
        ``cp1`` / ``cp2``, or ``None`` when ``cal_point`` names neither.

    """
    try:
        name = f"cp{int(cal_point)}"
    except (ValueError, OverflowError):
        return None
    return name if name in CALIBRATION_POINTS else None


def build_calibration_status(
    point: str,
    status_registers: Sequence[int],
    quality_registers: Sequence[int],
    process_registers: Sequence[int],
    decode: Callable[[Sequence[int], str], float],
) -> CalibrationStatus:
    """Slice three register blocks into a ``CalibrationStatus``.

    Parameters
    ----------
    point:
        ``cp1`` or ``cp2``.
    status_registers:
        The six register ``cp{n}_status`` block: code in registers 1-2,
        unit in 3-4, value in 5-6.
    quality_registers:
        The two register ``quality`` block.
    process_registers:
        The ten register ``pmc1`` block; the measurement is in
        registers 3-4.
    decode:
        ``ModbusHandler.decode``, taking two registers and a cast type.
        Injected so this module needs no pymodbus.

    """
    code = decode(status_registers[_FIRST_VALUE], "int")
    return CalibrationStatus(
        point=point,
        status=SUCCESSFUL if code == CALIBRATION_OK else FAILED,
        quality=float(decode(quality_registers[_FIRST_VALUE], "float")),
        value=float(decode(status_registers[_STATUS_VALUE], "float")),
        process_value=float(
            decode(process_registers[_PROCESS_VALUE], "float"),
        ),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_hamilton.py -v`
Expected: 6 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check reactors_czlab/core/hamilton.py tests/test_hamilton.py
git add reactors_czlab/core/hamilton.py tests/test_hamilton.py
git commit -m "feat: decode Hamilton calibration status without pymodbus"
```

---

## Task 2: `HamiltonSensor.read_calibration_status`

**Files:**
- Modify: `reactors_czlab/core/sensor.py:76-92` (base class) and `:345-407` (`write_calibration`)
- Create: `scripts/hamilton_read_calibration.py`

**Interfaces:**
- Consumes: Task 1's `CalibrationStatus`, `point_name`,
  `build_calibration_status`, `FAILED`, `UNSUPPORTED`.
- Produces: `Sensor.read_calibration_status(cal_point: float) ->
  CalibrationStatus` (async, on the base class and overridden on
  `HamiltonSensor`). `write_calibration`'s signature and
  `tuple[str, float, float]` return are unchanged.

**Note for the implementer:** this task has **no unit tests**, and that is not
an oversight. `tests/` cannot import `core.sensor` (it pulls in pymodbus, which
`--extra dev` does not install), and the decodable logic was moved to
`core/hamilton.py` in Task 1 precisely so it could be tested there. What is
left here is Modbus sequencing, which is verified by the bench script in
Step 3 against real hardware. Do not add a test that imports `core.sensor`.

- [ ] **Step 1: Add the base-class method**

In `reactors_czlab/core/sensor.py`, add to the imports:

```python
from reactors_czlab.core.hamilton import (
    FAILED,
    UNSUPPORTED,
    CalibrationStatus,
    build_calibration_status,
    point_name,
)
```

Add to `class Sensor`, directly after `write_calibration`:

```python
    async def read_calibration_status(
        self,
        cal_point: float,
    ) -> CalibrationStatus:
        """Read the state of one calibration point.

        The default reports that the sensor has no calibration points to
        read. Sensors that support it (HamiltonSensor) override this.
        """
        _logger.warning(
            "%s does not support calibration (point %s)",
            self.id,
            cal_point,
        )
        return CalibrationStatus.unavailable(
            point_name(cal_point) or "unknown",
            UNSUPPORTED,
        )
```

- [ ] **Step 2: Add the override and refactor the write**

Replace `HamiltonSensor.write_calibration` (currently
`reactors_czlab/core/sensor.py:345-407`) with these two methods:

```python
    async def read_calibration_status(
        self,
        cal_point: float,
    ) -> CalibrationStatus:
        """Read status, stored value, quality and the live measurement.

        Writes nothing, but it is not a user-level operation:
        ``CP1Status`` / ``CP2Status`` are documented at "level: A,S" in
        the class docstring's register table, so the read has to be made
        at specialist level and the level dropped back afterwards. That
        cost is why this is an on-demand call and not something the
        sampling loop publishes.

        Returns
        -------
        CalibrationStatus
            With ``status`` set to ``failed`` and zeroed readings if the
            point is not one of ``CALIBRATION_POINTS`` or the bus did
            not answer.

        """
        point = point_name(cal_point)
        if point is None:
            _logger.error(
                "Invalid calibration point %s for %s, expected one of %s",
                cal_point,
                self.id,
                sorted(self.CALIBRATION_POINTS),
            )
            return CalibrationStatus.unavailable("unknown", FAILED)

        try:
            await self.set_operator_level("specialist")
            status_response = await self.read_holding_registers(
                f"{point}_status",
            )
            quality_response = await self.read_holding_registers("quality")
            process_response = await self.read_holding_registers("pmc1")
        except ModbusError:
            _logger.exception(
                "Error reading calibration status of unit %s",
                self.id,
            )
            return CalibrationStatus.unavailable(point, FAILED)
        else:
            status = build_calibration_status(
                point,
                status_response,
                quality_response,
                process_response,
                self.modbus_handler.decode,
            )
            _logger.info(
                "Calibration status at %s - point: %s, status: %s, "
                "cp: %s, quality: %s, pmc1: %s",
                self.id,
                status.point,
                status.status,
                status.value,
                status.quality,
                status.process_value,
            )
            return status
        finally:
            # Never leave the sensor sitting at specialist level.
            try:
                await self.set_operator_level("user")
            except ModbusError:
                _logger.exception(
                    "Failed to drop %s back to user level",
                    self.id,
                )

    async def write_calibration(
        self,
        cal_point: float,
        cal_value: float,
    ) -> tuple[str, float, float]:
        """Write a value to a calibration point and report the outcome.

        Returns ("failed", 0.0, 0.0) if the sensor could not be
        calibrated; the operator level is always dropped back to "user"
        afterwards. The reporting half is
        ``read_calibration_status()``, which escalates and drops the
        level itself - so this method's own escalation covers only the
        write.
        """
        point = point_name(cal_point)
        if point is None:
            _logger.error(
                "Invalid calibration point %s for %s, expected one of %s",
                cal_point,
                self.id,
                sorted(self.CALIBRATION_POINTS),
            )
            return (FAILED, 0.0, 0.0)

        try:
            # A failure here must abort: writing calibration registers
            # without specialist rights silently does nothing.
            await self.set_operator_level("specialist")
            await self.write_registers(point, [cal_value])
        except ModbusError:
            _logger.exception("Error during calibration of unit %s", self.id)
            return (FAILED, 0.0, 0.0)
        finally:
            # Never leave the sensor sitting at specialist level.
            try:
                await self.set_operator_level("user")
            except ModbusError:
                _logger.exception(
                    "Failed to drop %s back to user level",
                    self.id,
                )

        status = await self.read_calibration_status(cal_point)
        return (status.status, status.quality, status.value)
```

Then change the `CALIBRATION_POINTS` class variable (currently
`reactors_czlab/core/sensor.py:188`) to derive from the new module, so the
two cannot drift:

```python
    #: Calibration points that have a writable register in REGISTERS.
    CALIBRATION_POINTS: ClassVar = frozenset(hamilton_points)
```

and add `from reactors_czlab.core.hamilton import CALIBRATION_POINTS as
hamilton_points` to the import block.

- [ ] **Step 3: Write the bench script**

Create `scripts/hamilton_read_calibration.py`:

```python
"""Bench check for HamiltonSensor.read_calibration_status.

Not a pytest suite and not part of the package: it needs real hardware on
the RS485 bus. Run it on the Pi.

    uv run python scripts/hamilton_read_calibration.py --address 1

The numbers it prints cannot be trusted until the Modbus word order has
been verified (see CLAUDE.md, "Modbus byte order - UNVERIFIED"). If the
pH and the calibration values come back nonsensical, flip WORD_ORDER in
core/modbus.py and run this again.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from reactors_czlab.core.data import Channel, PhysicalInfo, PlcOutput
from reactors_czlab.core.modbus import ModbusHandler
from reactors_czlab.core.sensor import HamiltonSensor

MODBUS_PORT = "/dev/ttySC2"
MODBUS_BAUDRATE = 19200
MODBUS_TIMEOUT = 0.1


async def main(address: int) -> None:
    """Read both calibration points of one sensor and print them."""
    handler = ModbusHandler(
        port=MODBUS_PORT,
        baudrate=MODBUS_BAUDRATE,
        timeout=MODBUS_TIMEOUT,
    )
    sensor = HamiltonSensor(
        "bench:ph",
        PhysicalInfo(
            model="ArcPh",
            address=address,
            type=PlcOutput.digital,
            channels=[Channel("pH", "pH", register="pmc1")],
        ),
        handler,
    )

    for point in (1.0, 2.0):
        status = await sensor.read_calibration_status(point)
        print(status)  # noqa: T201


def cli() -> None:
    """Parse the command line and run the check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", type=int, default=1)
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG)
    asyncio.run(main(args.address))


if __name__ == "__main__":
    cli()
```

- [ ] **Step 4: Verify nothing regressed**

Run: `uv run ruff check reactors_czlab/core/sensor.py scripts/hamilton_read_calibration.py && uv run pytest`
Expected: ruff clean, 238 passed (232 existing + Task 1's 6)

- [ ] **Step 5: Commit**

```bash
git add reactors_czlab/core/sensor.py scripts/hamilton_read_calibration.py
git commit -m "feat: read Hamilton calibration status without writing one"
```

---

## Task 3: OPC sensor node - status method and channel index

**Files:**
- Modify: `reactors_czlab/opcua/sensor.py:31-89`
- Test: `tests/test_opcua_sensor.py`

**Interfaces:**
- Consumes: Task 2's `Sensor.read_calibration_status`.
- Produces: OPC method `{sensor_id}:read_calibration_status(Cal_point: Float)
  -> (Status: String, Quality: Float, Value: Float, Process_value: Float)`.
  Each channel variable carries a `ChannelIndex` property (`UInt32`) holding
  its position in `sensor.channels`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_opcua_sensor.py`:

```python
"""Tests for the sensor node's calibration methods and channel indices.

The method bodies are exercised directly against stub nodes: asyncua is a
base dependency but a running server is not needed, and ``core.sensor``
(which needs pymodbus) is never imported - the fake sensor below is the
same duck type ``conftest`` uses.
"""

from __future__ import annotations

import types

import pytest
from asyncua import ua

from reactors_czlab.core.hamilton import CalibrationStatus
from reactors_czlab.opcua.sensor import SensorOpc


class _CalibratableSensor:
    """A sensor duck type that answers both calibration calls."""

    def __init__(self, identifier, channels):
        self.id = identifier
        self.channels = channels
        self.status_calls = []

    async def read(self):
        """Never called by these tests."""

    async def write_calibration(self, cal_point, cal_value):
        """Report a successful write."""
        return ("Successful", 0.9, cal_value)

    async def read_calibration_status(self, cal_point):
        """Record the request and answer with a fixed status."""
        self.status_calls.append(cal_point)
        return CalibrationStatus(
            point="cp1",
            status="Successful",
            quality=0.93,
            value=4.01,
            process_value=7.02,
        )


class _CapturingVariable:
    """An asyncua variable that records the properties added to it."""

    def __init__(self):
        self.properties = {}

    async def set_writable(self):
        """No-op."""

    async def write_attribute(self, attribute, value):
        """No-op."""

    async def add_property(self, idx, name, value):
        """Capture the property under its name."""
        self.properties[name] = value


class _CapturingNode:
    """Stand-in for an asyncua node that records children."""

    def __init__(self, name="R0:ph"):
        self.name = name
        self.methods = {}
        self.variables = []
        self.objects = {}

    async def read_browse_name(self):
        """Return something with a ``.Name``, as asyncua does."""
        return types.SimpleNamespace(Name=self.name)

    async def add_object(self, idx, name):
        """Hand back a child node."""
        child = _CapturingNode(name)
        self.objects[name] = child
        return child

    async def add_variable(self, idx, name, value, **kwargs):
        """Hand back a capturing variable."""
        variable = _CapturingVariable()
        self.variables.append((name, variable))
        return variable

    async def add_method(self, idx, name, callback, *args, **kwargs):
        """Capture the callback under its bare method name."""
        self.methods[name.split(":")[-1]] = callback


async def _call(method, *args):
    """Invoke a captured @uamethod callback the way the server would."""
    result = await method(ua.NodeId(), *(ua.Variant(a) for a in args))
    return [item.Value for item in result]


@pytest.fixture
async def sensor_node():
    """A SensorOpc initialised against capturing nodes."""
    from reactors_czlab.core.data import Channel

    sensor = _CalibratableSensor(
        "R0:ph",
        [
            Channel("pH", "pH", register="pmc1"),
            Channel("oC", "degree_celsius", register="pmc6"),
        ],
    )
    parent = _CapturingNode("R0:sensors")
    node = SensorOpc(sensor)
    await node.init_node(parent, 2, "R0")
    return node, sensor


async def test_read_calibration_status_returns_four_values(
    sensor_node,
) -> None:
    """The GUI needs status, quality, stored value and the live reading."""
    node, sensor = sensor_node

    result = await _call(
        node.node.methods["read_calibration_status"],
        1.0,
    )

    assert result == ["Successful", 0.93, 4.01, 7.02]
    assert sensor.status_calls == [1.0]


async def test_every_channel_carries_its_index(sensor_node) -> None:
    """set_pairing takes a channel index; OPC only gives channel names.

    Regression: deriving the index from browse order would rely on
    get_children() preserving insertion order, which asyncua does not
    guarantee.
    """
    node, _ = sensor_node

    indices = [
        variable.properties["ChannelIndex"]
        for _, variable in node.node.variables
    ]

    assert indices == [0, 1]


async def test_the_calibration_write_method_still_exists(
    sensor_node,
) -> None:
    """The existing method keeps its name and its three return values."""
    node, _ = sensor_node

    result = await _call(node.node.methods["calibration"], 1.0, 4.0)

    assert result == ["Successful", 0.9, 4.0]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_opcua_sensor.py -v`
Expected: FAIL - `KeyError: 'read_calibration_status'` and
`KeyError: 'ChannelIndex'`

- [ ] **Step 3: Add the property and the method**

In `reactors_czlab/opcua/sensor.py`, change the channel loop in `init_node`
(currently lines 40-52) to add the index property:

```python
        # Add channels to store data from the sensor
        for index, channel in enumerate(sensor.channels):
            var = await self.node.add_variable(
                idx,
                f"{self.id}:{channel.units}",
                0.0,
            )
            await var.set_writable()
            await var.write_attribute(
                ua.AttributeIds.Description,
                ua.DataValue(ua.LocalizedText(Text=channel.description)),
            )
            # set_pairing takes the channel's *index*, and a browse gives
            # only its name. A property rather than a variable: its browse
            # name has one part, so OpcClient.match_tree skips it and it
            # never reaches the FLOAT value column of the data table.
            await var.add_property(
                idx,
                "ChannelIndex",
                ua.Variant(index, ua.VariantType.UInt32),
            )
            self.channels.append(var)
```

Add the new method alongside `write_calibration` inside `init_node`:

```python
        @uamethod
        async def read_calibration_status(
            parent: Node,
            cal_point: float,
        ) -> tuple[str, float, float, float]:
            """Read one calibration point without changing it."""
            status = await self.sensor.read_calibration_status(cal_point)
            return (
                status.status,
                status.quality,
                status.value,
                status.process_value,
            )
```

and register it after the existing `add_method` call:

```python
        outarg4 = ua.Argument()
        outarg4.Name = "Process_value"
        outarg4.DataType = ua.NodeId(ua.ObjectIds.Float)

        await self.node.add_method(
            idx,
            f"{self.id}:read_calibration_status",
            read_calibration_status,
            [inarg_calp],
            [outarg1, outarg2, outarg3, outarg4],
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_opcua_sensor.py -v`
Expected: 3 passed

- [ ] **Step 5: Lint, full suite, commit**

```bash
uv run ruff check reactors_czlab/opcua/sensor.py tests/test_opcua_sensor.py
uv run pytest
git add reactors_czlab/opcua/sensor.py tests/test_opcua_sensor.py
git commit -m "feat: expose Hamilton calibration status and channel indices over OPC"
```

---

## Task 4: Publish the current pairings

**Files:**
- Modify: `reactors_czlab/opcua/reactor.py:103-208` (`init_pairing_methods`), `:72-91` (`init_node`)
- Test: `tests/test_opcua_pairing.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `ReactorOpc.pairings_var` (an asyncua variable, browse name
  `R{n}:pairings`) holding a JSON list of
  `{"sensor": str, "actuator": str, "channel": int}`, sorted by
  `(sensor, actuator, channel)`. `ReactorOpc.pairings_json() -> str` builds
  the payload; `ReactorOpc.publish_pairings()` writes it.

- [ ] **Step 1: Write the failing tests**

Add `import json` to the top of `tests/test_opcua_pairing.py`, then append:

```python
class _StubVariable:
    """An asyncua variable that just holds a value."""

    def __init__(self, value: object) -> None:
        self.value = value

    async def write_value(self, value: object) -> None:
        """Store a written value."""
        self.value = value


@pytest.fixture
async def published(make_sensor, make_actuator):
    """A ReactorOpc whose pairings variable is captured."""
    reactor_opc = ReactorOpc(
        "R0",
        volume=5,
        sensors=[make_sensor("R0:ph")],
        actuators=[make_actuator("R0:pwm0"), make_actuator("R0:pwm1")],
        period=10,
    )
    node = _CapturingNode()
    reactor_opc.node = node
    reactor_opc.pairings_var = _StubVariable("[]")
    await reactor_opc.init_pairing_methods(2)
    return reactor_opc, node.methods["set_pairing"], node.methods["unpair"]


async def test_pairing_is_published_as_json(published) -> None:
    """The GUI cannot read reactor.sampling.pairings; it reads this.

    Regression: pairings lived only as server-side Python state and the
    methods returned a bare bool, so no client could show what was
    paired or recover the picture after a restart.
    """
    reactor_opc, set_pairing, _ = published

    await _call(set_pairing, "R0:ph", "R0:pwm0", 0)

    assert json.loads(reactor_opc.pairings_var.value) == [
        {"sensor": "R0:ph", "actuator": "R0:pwm0", "channel": 0},
    ]


async def test_unpairing_is_published_too(published) -> None:
    """Removing a pairing updates the variable."""
    reactor_opc, set_pairing, unpair = published

    await _call(set_pairing, "R0:ph", "R0:pwm0", 0)
    await _call(unpair, "R0:ph", "R0:pwm0", 0)

    assert json.loads(reactor_opc.pairings_var.value) == []


async def test_a_refused_pairing_does_not_republish(published) -> None:
    """A rejected call leaves the published picture alone."""
    reactor_opc, set_pairing, _ = published
    reactor_opc.pairings_var.value = "sentinel"

    assert await _call(set_pairing, "R9:ph", "R0:pwm0", 0) is False
    assert reactor_opc.pairings_var.value == "sentinel"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_opcua_pairing.py -v`
Expected: FAIL - `AttributeError` / the variable is never written

- [ ] **Step 3: Implement**

In `reactors_czlab/opcua/reactor.py`, add `import json` to the imports and
`self.pairings_var: Node | None = None` to `__init__`.

Add these two methods to `ReactorOpc`:

```python
    def pairings_json(self) -> str:
        """The current pairing table as a JSON list.

        Sorted so an unchanged table serialises identically and a
        subscriber is not woken by key ordering.
        """
        rows = [
            {"sensor": sid, "actuator": aid, "channel": channel}
            for sid, paired in self.reactor.sampling.pairings.items()
            for aid, channel in paired
        ]
        rows.sort(key=lambda row: (row["sensor"], row["actuator"],
                                   row["channel"]))
        return json.dumps(rows)

    async def publish_pairings(self) -> None:
        """Write the pairing table to the OPC variable.

        ``reactor.sampling.pairings`` is server-side Python state and
        the pairing methods answer with a bare bool, so without this a
        client cannot show what is paired, nor recover the picture after
        its own restart.
        """
        if self.pairings_var is None:
            return
        await self.pairings_var.write_value(self.pairings_json())
```

In `init_node`, create the variable **before** `init_pairing_methods(idx)`:

```python
        # Read-only picture of the pairing table. It lives on the reactor
        # node, above R{n}:sensors / R{n}:actuators, so match_tree never
        # sees it - a String here could never be inserted into the FLOAT
        # value column of the data table.
        self.pairings_var = await self.node.add_variable(
            idx,
            f"{self.id}:pairings",
            self.pairings_json(),
        )

        await self.init_pairing_methods(idx)
```

In `set_pairing`, immediately before `return True`:

```python
            await self.publish_pairings()
            _logger.info("Current pairings: %s", dict(sampling.pairings))
            return True
```

In `unpair`, immediately before its `return True`:

```python
            await self.publish_pairings()
            _logger.info("Current pairings: %s", dict(sampling.pairings))
            return True
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_opcua_pairing.py -v`
Expected: 10 passed (7 existing + 3 new)

- [ ] **Step 5: Lint, full suite, commit**

```bash
uv run ruff check reactors_czlab/opcua/reactor.py tests/test_opcua_pairing.py
uv run pytest
git add reactors_czlab/opcua/reactor.py tests/test_opcua_pairing.py
git commit -m "feat: publish the reactor pairing table over OPC"
```

---

## Task 5: Publish the full pump calibration

**Files:**
- Modify: `reactors_czlab/opcua/actuator.py:348-447` (`init_calibration_methods`)
- Test: `tests/test_opcua_actuator.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: OPC method `{actuator_id}:get_calibration() -> String`, a JSON
  object with keys `file`, `a`, `b`, `r2`, `min_duty`, `max_duty`,
  `dispense_duty`, `fitted_at`, `is_fitted`, `points` (list of `[duty, flow]`),
  and `run_points` (the points collected so far by the in-flight
  `CalibrationRun`, same shape). When the channel has no calibration slot,
  every field is `null`/empty and `is_fitted` is `false`.

- [ ] **Step 1: Write the failing tests**

Add `import json` and `from asyncua import ua` to the top of
`tests/test_opcua_actuator.py` (ruff E402 rejects imports further down), then
append:

```python
class _CapturingNode:
    """Stand-in for an asyncua node that records added methods."""

    def __init__(self) -> None:
        self.methods: dict[str, object] = {}

    async def add_method(self, idx, name, callback, *args, **kwargs) -> None:
        """Capture the callback under its bare method name."""
        self.methods[name.split(":")[-1]] = callback


async def _call_method(method, *args):
    """Invoke a captured @uamethod callback the way the server would."""
    result = await method(ua.NodeId(), *(ua.Variant(a) for a in args))
    return result[0].Value


async def test_get_calibration_publishes_the_whole_line(
    make_calibrated_actuator,
) -> None:
    """cal_a/b/r2 are published; the duties and fitted_at were not.

    The config form needs fitted_at to refuse a flow or volume unit
    before writing it, because check_unit() rejects that server-side and
    logs it where an operator will not look.
    """
    actuator = make_calibrated_actuator()
    node = ActuatorOpc(actuator)
    node.node = _CapturingNode()
    await node.init_calibration_methods(2)

    payload = json.loads(
        await _call_method(node.node.methods["get_calibration"]),
    )

    assert payload["min_duty"] == 400.0
    assert payload["max_duty"] == 4000.0
    assert payload["dispense_duty"] == 2000.0
    assert payload["is_fitted"] is True
    assert payload["points"] == [[500.0, 5.0], [2500.0, 25.0]]


async def test_get_calibration_reports_an_unfitted_line(
    make_calibrated_actuator,
) -> None:
    """An unfitted placeholder must be visibly unfitted."""
    actuator = make_calibrated_actuator(fitted=False)
    node = ActuatorOpc(actuator)
    node.node = _CapturingNode()
    await node.init_calibration_methods(2)

    payload = json.loads(
        await _call_method(node.node.methods["get_calibration"]),
    )

    assert payload["is_fitted"] is False
    assert payload["fitted_at"] == ""


async def test_get_calibration_includes_the_in_flight_run_points(
    make_calibrated_actuator,
) -> None:
    """Points collected before a fit are otherwise invisible.

    They live in CalibrationRun.points, not Calibration.points, so a
    second operator or a page reload would show an empty run.
    """
    actuator = make_calibrated_actuator()
    node = ActuatorOpc(actuator)
    node.node = _CapturingNode()
    node.run.points = [(1000.0, 10.0)]
    await node.init_calibration_methods(2)

    payload = json.loads(
        await _call_method(node.node.methods["get_calibration"]),
    )

    assert payload["run_points"] == [[1000.0, 10.0]]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_opcua_actuator.py -v`
Expected: FAIL - `KeyError: 'get_calibration'`

- [ ] **Step 3: Implement**

In `reactors_czlab/opcua/actuator.py`, add `import json` to the imports, and
add this method to `ActuatorOpc`:

```python
    def calibration_json(self) -> str:
        """The installed calibration and the in-flight run, as JSON.

        Only ``cal_a``/``cal_b``/``cal_r2`` are published as variables -
        they are the three that make sense as archived time series. The
        duties, ``fitted_at`` and the measured points are needed by a
        client that has to decide whether a flow or volume unit is
        allowed, or draw the calibration screen, and neither belongs in
        the ``data`` table.

        ``run_points`` is the points collected so far by the
        ``CalibrationRun`` and not yet fitted. They live on the run, not
        on the ``Calibration``, so without this a second operator - or
        the same one after a page reload - would see an empty run.
        """
        cal = self.actuator.channel.calibration
        run_points = [[duty, flow] for duty, flow in self.run.points]
        if cal is None:
            return json.dumps(
                {
                    "file": None,
                    "a": None,
                    "b": None,
                    "r2": None,
                    "min_duty": None,
                    "max_duty": None,
                    "dispense_duty": None,
                    "fitted_at": "",
                    "is_fitted": False,
                    "points": [],
                    "run_points": run_points,
                },
            )
        return json.dumps(
            {
                "file": cal.file,
                "a": cal.a,
                "b": cal.b,
                "r2": cal.r2,
                "min_duty": cal.min_duty,
                "max_duty": cal.max_duty,
                "dispense_duty": cal.dispense_duty,
                "fitted_at": cal.fitted_at,
                "is_fitted": cal.is_fitted,
                "points": [[duty, flow] for duty, flow in cal.points],
                "run_points": run_points,
            },
        )
```

Inside `init_calibration_methods`, add the callback next to the others:

```python
        @uamethod
        def get_calibration(parent: Node) -> str:
            """Report the installed calibration and the in-flight run."""
            return self.calibration_json()
```

and add it to the registration loop's tuple:

```python
        for name, callback, inargs in (
            ("calibrate_point", calibrate_point, [inarg_duty, inarg_seconds]),
            ("record_point", record_point, [inarg_volume]),
            ("fit_calibration", fit_calibration, []),
            ("clear_points", clear_points, []),
            ("reload_calibration", reload_calibration, []),
            ("set_duties", set_duties, [inarg_min_duty, inarg_dispense]),
            ("get_calibration", get_calibration, []),
        ):
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_opcua_actuator.py -v`
Expected: all existing tests plus 3 new, passing

- [ ] **Step 5: Lint, full suite, commit**

```bash
uv run ruff check reactors_czlab/opcua/actuator.py tests/test_opcua_actuator.py
uv run pytest
git add reactors_czlab/opcua/actuator.py tests/test_opcua_actuator.py
git commit -m "feat: expose the full pump calibration over OPC"
```

---

## Task 6: Make `sql` importable without psycopg or polars

**Files:**
- Modify: `reactors_czlab/sql/operations.py:1-47` (imports and schema), `:54-76` (`connect_to_db`), `:151-197`
- Test: `tests/test_sql_operations.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `operations.PSYCOPG_AVAILABLE: bool`;
  `operations.require_psycopg() -> None` raising `SqlError` when it is False.
  Every public function calls it first. `rows_to_polars` additionally raises
  `SqlError` when polars is missing. `SCHEMA` becomes `polars_schema()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sql_operations.py`:

```python
"""Tests for the optional-dependency guards in the sql module.

The Pi may have no psycopg at all (32 bit Pi OS has no wheel), and the
GUI must still import and run with the database features disabled rather
than failing at import. These tests must pass whether or not psycopg is
installed, so they assert the guard's contract, not a particular answer.
"""

from __future__ import annotations

import pytest

from reactors_czlab.sql import operations


def test_the_module_imports_without_its_optional_dependencies() -> None:
    """Regression: importing this module used to need psycopg AND polars.

    opcua/client.py imports it at module scope, so the archiver could
    not even be loaded on a machine that had neither.
    """
    assert hasattr(operations, "PSYCOPG_AVAILABLE")
    assert isinstance(operations.PSYCOPG_AVAILABLE, bool)


def test_require_psycopg_matches_the_flag() -> None:
    """The guard raises exactly when the flag says it should."""
    if operations.PSYCOPG_AVAILABLE:
        assert operations.require_psycopg() is None
    else:
        with pytest.raises(operations.SqlError, match="psycopg"):
            operations.require_psycopg()


def test_insert_names_the_experiment_column() -> None:
    """The archiver tags every row with its reactor's experiment."""
    assert "experiment_name" in operations.INSERT_DATA
    assert operations.INSERT_DATA.count("%s") == len(operations.COLUMNS)


def test_columns_and_select_agree() -> None:
    """A column added to one and not the other misaligns every row."""
    for column in operations.COLUMNS:
        assert column in operations.SELECT_DATA
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_sql_operations.py -v`
Expected: FAIL - `ModuleNotFoundError: No module named 'psycopg'` at import

- [ ] **Step 3: Implement the guards**

In `reactors_czlab/sql/operations.py`, replace the import block (lines 1-47)
with:

```python
"""Store and retrieve reactor readings from PostgreSQL.

Both third-party dependencies are optional. ``psycopg`` has no wheel for
32 bit Raspberry Pi OS, and the GUI has to import and run with the
database features disabled rather than failing at import - so the import
is guarded and every public function checks ``require_psycopg()`` first.
``polars`` is needed by one function and is imported inside it.
"""

from __future__ import annotations

import csv
import getpass
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    import psycopg
except ImportError:  # pragma: no cover - depends on the install
    psycopg = None

if TYPE_CHECKING:
    from psycopg import Connection

#: Whether the database features are available in this install.
PSYCOPG_AVAILABLE = psycopg is not None

_logger = logging.getLogger("client.sql")

# Connection settings, overridable without touching the code.
DB_NAME = os.environ.get("BIOREACTOR_DB_NAME", "bioreactor_db")
DB_USER = os.environ.get("BIOREACTOR_DB_USER") or getpass.getuser()
DB_HOST = os.environ.get("BIOREACTOR_DB_HOST")
DB_PORT = os.environ.get("BIOREACTOR_DB_PORT")
DB_PASSWORD = os.environ.get("BIOREACTOR_DB_PASSWORD")

COLUMNS = (
    "node_id",
    "date",
    "reactor",
    "name",
    "channel",
    "value",
    "experiment_name",
)

INSERT_DATA = (
    "INSERT INTO data "
    "(node_id, date, reactor, name, channel, value, experiment_name) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)

SELECT_DATA = (
    "SELECT node_id, date, reactor, name, channel, value, experiment_name "
    "FROM data"
)


class SqlError(Exception):
    """Custom sql error."""


def require_psycopg() -> None:
    """Refuse to go further when psycopg is not installed.

    Raises
    ------
    SqlError
        When the install has no psycopg. The message names the extra to
        install, because it is shown to the operator in the GUI.

    """
    if not PSYCOPG_AVAILABLE:
        error_message = (
            "psycopg is not installed, so the database features are "
            "unavailable; install the 'client' extra"
        )
        raise SqlError(error_message)


def polars_schema() -> dict:
    """Column types of the ``data`` table, for polars.

    A function rather than a module constant so ``polars`` - needed by
    one consumer and absent from a minimal Pi install - is imported only
    when it is actually used.

    Raises
    ------
    SqlError
        When polars is not installed.

    """
    try:
        import polars as pl
    except ImportError as err:
        error_message = (
            "polars is not installed; install the 'client' extra"
        )
        raise SqlError(error_message) from err

    return {
        "node_id": pl.String,
        "date": pl.Datetime("ms"),
        "reactor": pl.String,
        "name": pl.String,
        "channel": pl.String,
        "value": pl.Float64,
        "experiment_name": pl.String,
    }
```

Add `require_psycopg()` as the first statement of `connect_to_db` and
`query_data`. Change `store_data`'s `values` tuple to carry the tag:

```python
    values = (
        node_id,
        info["timestamp"].isoformat(timespec="milliseconds"),
        info["reactor"],
        info["name"],
        info["channel"],
        info["value"],
        info.get("experiment_name"),
    )
```

Replace `rows_to_polars` with:

```python
def rows_to_polars(rows: list) -> Any:
    """Export sql queries to a polars dataframe.

    The schema is fixed by the data table, so an empty result set still
    produces a dataframe with the right columns.

    Raises
    ------
    SqlError
        When polars is not installed.

    """
    # polars_schema() first: it is the one that turns a missing polars
    # into an SqlError the GUI can show, rather than an ImportError.
    schema = polars_schema()
    import polars as pl

    return pl.DataFrame(rows, schema=schema, orient="row")
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_sql_operations.py -v`
Expected: 4 passed

- [ ] **Step 5: Lint, full suite, commit**

```bash
uv run ruff check reactors_czlab/sql/operations.py tests/test_sql_operations.py
uv run pytest
git add reactors_czlab/sql/operations.py tests/test_sql_operations.py
git commit -m "feat: make the sql module importable without psycopg or polars"
```

---

## Task 7: Experiment schema and CRUD

**Files:**
- Modify: `reactors_czlab/sql/Bioreactor.sql`
- Create: `reactors_czlab/sql/migrations/2026-07-30-experiments.sql`
- Modify: `reactors_czlab/sql/operations.py` (append the experiment functions)
- Test: `tests/test_sql_operations.py`

**Interfaces:**
- Consumes: Task 6's `require_psycopg`, `SqlError`.
- Produces: `create_experiment(name: str, reactors: list[str]) -> None`;
  `start_experiment(name: str) -> None`; `stop_experiment(name: str) -> None`;
  `list_experiments() -> list[tuple]`;
  `active_experiments() -> dict[str, str]` (reactor id -> experiment name);
  `query_experiment_data(name: str) -> list`. Constants
  `OVERLAP_MESSAGE`, and the statement constants asserted by the tests.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sql_operations.py`:

```python
def test_experiment_statements_use_the_array_column() -> None:
    """reactors is TEXT[], so overlap is an array operator, not LIKE."""
    assert "&&" in operations.SELECT_ACTIVE_OVERLAP


def test_a_running_experiment_has_no_end_date() -> None:
    """end_date NULL is what marks an experiment as active."""
    assert "end_date IS NULL" in operations.SELECT_ACTIVE


def test_every_experiment_function_is_guarded() -> None:
    """None of them may reach psycopg when it is absent."""
    if operations.PSYCOPG_AVAILABLE:
        pytest.skip("psycopg is installed; the guard cannot be observed")

    for call in (
        lambda: operations.create_experiment("e", ["R0"]),
        lambda: operations.start_experiment("e"),
        lambda: operations.stop_experiment("e"),
        operations.list_experiments,
        operations.active_experiments,
        lambda: operations.query_experiment_data("e"),
    ):
        with pytest.raises(operations.SqlError, match="psycopg"):
            call()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_sql_operations.py -v`
Expected: FAIL - `AttributeError: module ... has no attribute 'SELECT_ACTIVE_OVERLAP'`

- [ ] **Step 3: Update the schema**

Replace `reactors_czlab/sql/Bioreactor.sql` with:

```sql
CREATE DATABASE bioreactor_db;
\c bioreactor_db

CREATE TABLE data (
    id SERIAL PRIMARY KEY,
    node_id TEXT NOT NULL,
    date TIMESTAMP(3) NOT NULL,
    reactor TEXT NOT NULL,
    name TEXT NOT NULL,
    channel TEXT NOT NULL,
    value FLOAT NOT NULL,
    -- NULL when the row was recorded outside any experiment.
    experiment_name TEXT
);

-- The plot query filters on exactly these columns and orders by date.
CREATE INDEX data_series_idx ON data (reactor, name, channel, date);
CREATE INDEX data_experiment_idx ON data (experiment_name);

CREATE TABLE experiments (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    reactors TEXT[] NOT NULL,
    start_date TIMESTAMP(3),
    -- NULL while the experiment is running.
    end_date TIMESTAMP(3)
);
```

Create `reactors_czlab/sql/migrations/2026-07-30-experiments.sql`:

```sql
-- Bring an existing bioreactor_db up to the GUI schema.
-- Nothing has ever written to experiments, so it is redefined rather
-- than migrated.
\c bioreactor_db

ALTER TABLE data ADD COLUMN IF NOT EXISTS experiment_name TEXT;

CREATE INDEX IF NOT EXISTS data_series_idx
    ON data (reactor, name, channel, date);
CREATE INDEX IF NOT EXISTS data_experiment_idx ON data (experiment_name);

DROP TABLE IF EXISTS experiments;
CREATE TABLE experiments (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    reactors TEXT[] NOT NULL,
    start_date TIMESTAMP(3),
    end_date TIMESTAMP(3)
);
```

- [ ] **Step 4: Add the experiment functions**

Append to `reactors_czlab/sql/operations.py`:

```python
INSERT_EXPERIMENT = (
    "INSERT INTO experiments (name, reactors) VALUES (%s, %s)"
)

UPDATE_START = (
    "UPDATE experiments SET start_date = %s "
    "WHERE name = %s AND start_date IS NULL"
)

UPDATE_STOP = (
    "UPDATE experiments SET end_date = %s "
    "WHERE name = %s AND end_date IS NULL"
)

SELECT_EXPERIMENTS = (
    "SELECT name, reactors, start_date, end_date FROM experiments "
    "ORDER BY id"
)

#: Running experiments: started and not yet stopped.
SELECT_ACTIVE = (
    "SELECT name, reactors FROM experiments "
    "WHERE start_date IS NOT NULL AND end_date IS NULL"
)

#: Whether any running experiment already owns one of these reactors.
#: ``&&`` is the array-overlap operator; the reactor set is a TEXT[], so
#: this is one indexable comparison rather than a LIKE over a joined
#: string, which would match R1 inside R10.
SELECT_ACTIVE_OVERLAP = (
    "SELECT name FROM experiments "
    "WHERE start_date IS NOT NULL AND end_date IS NULL "
    "AND reactors && %s"
)

SELECT_EXPERIMENT_DATA = SELECT_DATA + (
    " WHERE experiment_name = %s ORDER BY date"
)


def _execute(statement: str, params: tuple) -> None:
    """Run one statement in its own connection and commit it.

    Raises
    ------
    SqlError
        If psycopg is missing or the statement failed.

    """
    require_psycopg()
    connection = connect_to_db()
    try:
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
        connection.commit()
    except psycopg.Error as err:
        error_message = f"Error running {statement} with {params}"
        raise SqlError(error_message) from err
    finally:
        connection.close()


def _fetch(statement: str, params: tuple = ()) -> list:
    """Run one query in its own connection and return every row.

    Raises
    ------
    SqlError
        If psycopg is missing or the query failed.

    """
    require_psycopg()
    connection = connect_to_db()
    try:
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            return cursor.fetchall()
    except psycopg.Error as err:
        error_message = f"Error running {statement} with {params}"
        raise SqlError(error_message) from err
    finally:
        connection.close()


def create_experiment(name: str, reactors: list[str]) -> None:
    """Record a new experiment, not yet started.

    Raises
    ------
    SqlError
        If psycopg is missing, the name is already taken, or the insert
        failed.

    """
    require_psycopg()
    _execute(INSERT_EXPERIMENT, (name, list(reactors)))
    _logger.info("Created experiment %s on %s", name, reactors)


def start_experiment(name: str) -> None:
    """Mark an experiment as running from now.

    Raises
    ------
    SqlError
        If psycopg is missing, the experiment does not exist, it has
        already been started, or one of its reactors already belongs to
        a running experiment. Overlapping reactor sets are refused
        because a row can carry only one experiment name.

    """
    require_psycopg()
    rows = _fetch(SELECT_EXPERIMENTS)
    matched = [row for row in rows if row[0] == name]
    if not matched:
        error_message = f"No experiment named {name}"
        raise SqlError(error_message)

    _name, reactors, start_date, end_date = matched[0]
    if start_date is not None and end_date is None:
        error_message = f"{name} is already running"
        raise SqlError(error_message)

    reactors = list(reactors)
    clashes = _fetch(SELECT_ACTIVE_OVERLAP, (reactors,))
    if clashes:
        error_message = (
            f"Cannot start {name}: {clashes[0][0]} is already running on "
            f"one of {reactors}"
        )
        raise SqlError(error_message)

    _execute(UPDATE_START, (datetime.now(), name))
    _logger.info("Started experiment %s on %s", name, reactors)


def stop_experiment(name: str) -> None:
    """Mark a running experiment as finished.

    Raises
    ------
    SqlError
        If psycopg is missing or the update failed.

    """
    require_psycopg()
    _execute(UPDATE_STOP, (datetime.now(), name))
    _logger.info("Stopped experiment %s", name)


def list_experiments() -> list:
    """Every experiment as ``(name, reactors, start_date, end_date)``.

    Raises
    ------
    SqlError
        If psycopg is missing or the query failed.

    """
    return _fetch(SELECT_EXPERIMENTS)


def active_experiments() -> dict[str, str]:
    """Map each reactor to the running experiment that owns it.

    This is what the archiver tags rows with.

    Raises
    ------
    SqlError
        If psycopg is missing or the query failed.

    """
    tags: dict[str, str] = {}
    for name, reactors in _fetch(SELECT_ACTIVE):
        for reactor in reactors:
            tags[reactor] = name
    return tags


def query_experiment_data(name: str) -> list:
    """Every archived row tagged with this experiment.

    Raises
    ------
    SqlError
        If psycopg is missing or the query failed.

    """
    return _fetch(SELECT_EXPERIMENT_DATA, (name,))
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_sql_operations.py -v`
Expected: 7 passed (one may skip if psycopg is installed)

- [ ] **Step 6: Lint, full suite, commit**

```bash
uv run ruff check reactors_czlab/sql/ tests/test_sql_operations.py
uv run pytest
git add reactors_czlab/sql/ tests/test_sql_operations.py
git commit -m "feat: experiment records with disjoint reactor sets"
```

---

## Task 8: Client - gate the queue, widen the subscription, tag rows

**Files:**
- Modify: `reactors_czlab/opcua/client.py:200-260`, and add the recording API
- Test: `tests/test_opcua_client.py`

**Interfaces:**
- Consumes: Task 6/7's `operations` module.
- Produces: `OpcClient.experiment_tags: dict[str, str]`;
  `OpcClient.recording -> bool`;
  `OpcClient.start_recording()` / `stop_recording()` (async);
  `OpcClient.archives(nodeid: str, info: dict) -> bool`. The subscription now
  covers every variable in `sensor_vars` and `actuator_vars`.

**Warning for the implementer:** `test_total_volume_is_subscribed_and_cal_fields_are_not`
in `tests/test_opcua_client.py` currently asserts the *subscription* is
filtered. That assertion is being deliberately inverted - the subscription
widens and the filter moves to enqueue time - so rewrite it as instructed
below rather than deleting it. Its `Regression:` note explains a real bug and
must survive in the rewritten test.

- [ ] **Step 1: Rewrite and extend the tests**

In `tests/test_opcua_client.py`, replace
`test_total_volume_is_subscribed_and_cal_fields_are_not` with:

```python
async def test_every_published_variable_is_subscribed(
    client_module: Any,
) -> None:
    """The GUI reads live values off the subscription, so it needs all.

    The archived set is unchanged - the filter moved from subscribe time
    to enqueue time (see test_only_the_two_series_are_archived). Without
    this the GUI could not show a control config written by another OPC
    client, nor the calibration a refit had just installed.
    """
    opc = _client_with(
        client_module,
        ["curr_value", "total_volume", "cal_a", "cal_b", "cal_r2"],
    )

    await opc.init_subscriptions()

    watched = {node.nodeid for node in opc.client.subscription.subscribed}
    assert watched == {
        "R0:ph:pH",
        "R0:pwm0:curr_value",
        "R0:pwm0:total_volume",
        "R0:pwm0:cal_a",
        "R0:pwm0:cal_b",
        "R0:pwm0:cal_r2",
    }


def test_only_the_two_series_are_archived(client_module: Any) -> None:
    """The archived set is enforced at enqueue, and is unchanged.

    Regression: this used to be a subscription filter that named
    curr_value alone, so every new pump variable was published and
    archived by nothing, and run_plots.py filtering on total_volume
    found an empty table. The cal_* variables stay unarchived on
    purpose: they move only on a refit, so at the 500 ms publishing
    interval they would fill the table with constants.
    """
    opc = _client_with(
        client_module,
        ["curr_value", "total_volume", "cal_a", "cal_b", "cal_r2"],
    )

    archived = {
        nodeid
        for nodeid, info in opc.actuator_vars.items()
        if opc.archives(nodeid, info)
    }

    assert archived == {"R0:pwm0:curr_value", "R0:pwm0:total_volume"}


class _NotifyingNode:
    """A node shaped the way datachange_notification reads one.

    ``_StubNode.nodeid`` is a bare string, which is enough for the
    subscription assertions but not here: the callback calls
    ``node.nodeid.to_string()``.
    """

    def __init__(self, nodeid: str) -> None:
        self.nodeid = types.SimpleNamespace(to_string=lambda: nodeid)


async def test_values_update_with_recording_off(client_module: Any) -> None:
    """The GUI reads live values whether or not anything is archiving.

    Regression: datachange_notification enqueued unconditionally. With
    no archiver draining it, the 1000 slot queue filled and then logged
    an error every sample forever - latent until the GUI subscribed
    with recording off.
    """
    opc = _client_with(client_module, ["curr_value"])
    opc.variables = {**opc.sensor_vars, **opc.actuator_vars}

    for _ in range(client_module.QUEUE_MAXSIZE + 10):
        await opc.datachange_notification(
            _NotifyingNode("R0:ph:pH"),
            7.5,
            None,
        )

    assert opc.variables["R0:ph:pH"]["value"] == 7.5
    assert opc._queue.qsize() == 0


async def test_rows_are_tagged_with_the_reactor_experiment(
    client_module: Any,
) -> None:
    """A reactor in a running experiment tags every row it produces."""
    opc = _client_with(client_module, ["curr_value"])
    opc.variables = {**opc.sensor_vars, **opc.actuator_vars}
    opc.experiment_tags = {"R0": "growth-curve-1"}
    opc._db_task = object()  # pretend the archiver is running

    await opc.datachange_notification(_NotifyingNode("R0:ph:pH"), 7.5, None)

    _, info = opc._queue.get_nowait()
    assert info["experiment_name"] == "growth-curve-1"


async def test_untagged_rows_carry_none(client_module: Any) -> None:
    """Recording outside an experiment is allowed and leaves it NULL."""
    opc = _client_with(client_module, ["curr_value"])
    opc.variables = {**opc.sensor_vars, **opc.actuator_vars}
    opc._db_task = object()

    await opc.datachange_notification(_NotifyingNode("R0:ph:pH"), 7.5, None)

    _, info = opc._queue.get_nowait()
    assert info["experiment_name"] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_opcua_client.py -v`
Expected: FAIL - `AttributeError: 'OpcClient' object has no attribute 'archives'`

- [ ] **Step 3: Implement**

In `reactors_czlab/opcua/client.py`, add to `__init__`:

```python
        #: reactor id -> the running experiment that owns it. Consulted
        #: when a row is enqueued, so several experiments over disjoint
        #: reactor sets need nothing more than this dict.
        self.experiment_tags: dict[str, str] = {}
```

Replace `init_subscriptions`'s node list (lines 209-216) with:

```python
        # Everything published is subscribed: the GUI reads its live
        # values off this dict, including the control config and the
        # calibration, which are not archived. What reaches the database
        # is decided by archives(), at enqueue time.
        vars_to_sub = [
            self.client.get_node(nodeid) for nodeid in self.variables
        ]
```

Add these methods:

```python
    def archives(self, nodeid: str, info: dict) -> bool:
        """Whether a variable's changes belong in the ``data`` table.

        Sensor channels are archived wholesale. Of the actuator
        variables only ``ARCHIVED_ACTUATOR_CHANNELS`` are: the ``cal_*``
        variables move only on a refit, so at the 500 ms publishing
        interval, across every actuator on every reactor, they would
        fill the table with constants.
        """
        if nodeid not in self.actuator_vars:
            return True
        return info["channel"] in ARCHIVED_ACTUATOR_CHANNELS

    @property
    def recording(self) -> bool:
        """Whether the archiver task is running."""
        return self._db_task is not None

    async def start_recording(self) -> None:
        """Begin archiving queued readings."""
        await self.start_psql()

    async def stop_recording(self) -> None:
        """Stop archiving. Live values keep updating."""
        await self.stop_psql()
```

Replace the body of `datachange_notification` (lines 228-249) with:

```python
        nodeid = node.nodeid.to_string()
        info = self.variables.get(nodeid)
        if info is None:
            return
        if val == ERROR_VALUE:
            # The server could not read the device; do not archive it.
            # The live value is still updated: the GUI must be able to
            # show that a probe is failing.
            _logger.debug("Skipping error value from %s", nodeid)
            info["value"] = val
            info["timestamp"] = datetime.now()
            return

        info["value"] = val
        info["timestamp"] = datetime.now()

        # Nothing drains the queue when the archiver is stopped, so
        # enqueuing anyway fills the 1000 slot buffer and then logs an
        # error every sample forever.
        if not self.recording or not self.archives(nodeid, info):
            return

        row = dict(info)
        row["experiment_name"] = self.experiment_tags.get(info["reactor"])
        try:
            self._queue.put_nowait((nodeid, row))
        except asyncio.QueueFull:
            _logger.error(
                "Database queue is full (%s items), dropping %s",
                QUEUE_MAXSIZE,
                nodeid,
            )
        else:
            _logger.debug("Data change in %s: %s", nodeid, row)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_opcua_client.py -v`
Expected: 7 passed

- [ ] **Step 5: Lint, full suite, commit**

```bash
uv run ruff check reactors_czlab/opcua/client.py tests/test_opcua_client.py
uv run pytest
git add reactors_czlab/opcua/client.py tests/test_opcua_client.py
git commit -m "feat: live values without recording, and experiment-tagged rows"
```

---

## Task 9: `gui/address.py` - the AddressBook

**Files:**
- Create: `reactors_czlab/gui/__init__.py`, `reactors_czlab/gui/address.py`
- Test: `tests/test_gui_address.py`

**Interfaces:**
- Consumes: nothing (pure over the browse dicts `OpcClient` builds).
- Produces: `VariableRef` (frozen dataclass: `nodeid`, `reactor`, `name`,
  `channel`); `AddressBook` with
  `build(sensor_vars, actuator_vars, methods) -> AddressBook` (classmethod),
  `from_client(client) -> AddressBook` (classmethod),
  `reactors -> tuple[str, ...]`,
  `sensors(reactor) -> dict[str, tuple[VariableRef, ...]]`,
  `actuators(reactor) -> dict[str, dict[str, VariableRef]]`,
  `variable(reactor, name, channel) -> str | None`,
  `method(reactor, owner, name) -> str | None` (`owner=None` for
  reactor-level methods).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gui_address.py`:

```python
"""Tests for the AddressBook.

It is a pure function of the three dicts OpcClient builds when it
browses, so no server and no nicegui are needed.
"""

from __future__ import annotations

import pytest

from reactors_czlab.gui.address import AddressBook

SENSOR_VARS = {
    "ns=2;i=10": {
        "reactor": "R0",
        "name": "ph",
        "channel": "pH",
        "value": 7.0,
    },
    "ns=2;i=11": {
        "reactor": "R0",
        "name": "ph",
        "channel": "oC",
        "value": 30.0,
    },
    "ns=2;i=12": {
        "reactor": "R1",
        "name": "do",
        "channel": "ppm",
        "value": 6.0,
    },
}

ACTUATOR_VARS = {
    "ns=2;i=20": {
        "reactor": "R0",
        "name": "pwm0",
        "channel": "curr_value",
        "value": 0.0,
    },
    "ns=2;i=21": {
        "reactor": "R0",
        "name": "pwm0",
        "channel": "setpoint",
        "value": 7.0,
    },
}

METHODS = {
    "ns=2;i=30": {"reactor": "R0", "name": ["set_pairing"]},
    "ns=2;i=31": {"reactor": "R0", "name": ["unpair"]},
    "ns=2;i=32": {"reactor": "R0", "name": ["pwm0", "get_calibration"]},
    "ns=2;i=33": {"reactor": "R0", "name": ["ph", "calibration"]},
}


@pytest.fixture
def book() -> AddressBook:
    """An AddressBook over the fixture dicts."""
    return AddressBook.build(SENSOR_VARS, ACTUATOR_VARS, METHODS)


def test_reactors_are_sorted_and_unique(book) -> None:
    """The dashboard lists reactors in a stable order."""
    assert book.reactors == ("R0", "R1")


def test_sensor_channels_are_grouped_by_sensor(book) -> None:
    """One row per sensor, one value per channel."""
    sensors = book.sensors("R0")

    assert set(sensors) == {"ph"}
    assert [ref.channel for ref in sensors["ph"]] == ["oC", "pH"]


def test_actuator_channels_are_keyed_by_channel(book) -> None:
    """The config form looks up one named variable at a time."""
    actuators = book.actuators("R0")

    assert set(actuators["pwm0"]) == {"curr_value", "setpoint"}
    assert actuators["pwm0"]["setpoint"].nodeid == "ns=2;i=21"


def test_variable_resolves_a_nodeid(book) -> None:
    """Writing a config means turning a name into a node id."""
    assert book.variable("R0", "pwm0", "setpoint") == "ns=2;i=21"


def test_variable_returns_none_when_absent(book) -> None:
    """A missing variable is a refusal, not a KeyError in a page."""
    assert book.variable("R0", "pwm0", "nonesuch") is None


def test_reactor_level_methods_have_no_owner(book) -> None:
    """set_pairing hangs off the reactor node itself."""
    assert book.method("R0", None, "set_pairing") == "ns=2;i=30"


def test_owned_methods_need_their_owner(book) -> None:
    """Two actuators expose the same method name on one reactor."""
    assert book.method("R0", "pwm0", "get_calibration") == "ns=2;i=32"
    assert book.method("R0", None, "get_calibration") is None


def test_sensor_methods_resolve_too(book) -> None:
    """The calibration screens call methods owned by a sensor."""
    assert book.method("R0", "ph", "calibration") == "ns=2;i=33"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_gui_address.py -v`
Expected: FAIL - `ModuleNotFoundError: No module named 'reactors_czlab.gui'`

- [ ] **Step 3: Implement**

Create `reactors_czlab/gui/__init__.py`:

```python
"""Web GUI for the bioreactor controller.

Docstring only, like core/ and opcua/: a re-export here would make every
install carry nicegui.
"""
```

Create `reactors_czlab/gui/address.py`:

```python
"""Index the OPC address space by reactor, name and channel.

``OpcClient`` browses the server into three flat ``{nodeid: info}``
dicts. Every screen needs the opposite lookup - given a reactor, a device
and a channel, what is the node id - and the ``<reactor>:<name>:<channel>``
browse-name contract is unwound here, once, rather than in each page.

Pure: it touches no network and imports no nicegui, so it is testable
with fixture dicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reactors_czlab.opcua.client import OpcClient


@dataclass(frozen=True)
class VariableRef:
    """One published variable, and where it sits."""

    nodeid: str
    reactor: str
    name: str
    channel: str


class AddressBook:
    """Lookups over a browsed OPC address space."""

    def __init__(
        self,
        sensors: dict[str, dict[str, tuple[VariableRef, ...]]],
        actuators: dict[str, dict[str, dict[str, VariableRef]]],
        methods: dict[tuple[str, str | None, str], str],
    ) -> None:
        """Store the prepared indices. Use ``build`` instead."""
        self._sensors = sensors
        self._actuators = actuators
        self._methods = methods

    def __repr__(self) -> str:
        """Print how many reactors are indexed."""
        return f"AddressBook({len(self.reactors)} reactors)"

    @classmethod
    def build(
        cls,
        sensor_vars: dict[str, dict],
        actuator_vars: dict[str, dict],
        methods: dict[str, dict],
    ) -> AddressBook:
        """Index the three dicts ``OpcClient`` produces.

        Parameters
        ----------
        sensor_vars, actuator_vars:
            ``{nodeid: {"reactor", "name", "channel", ...}}``.
        methods:
            ``{nodeid: {"reactor": str, "name": list[str]}}``, where
            ``name`` is the browse name split on ``:`` with the reactor
            dropped - one element for a reactor-level method, two for
            one owned by a sensor or an actuator.

        """
        sensors: dict[str, dict[str, list[VariableRef]]] = {}
        for nodeid, info in sensor_vars.items():
            ref = VariableRef(
                nodeid,
                info["reactor"],
                info["name"],
                info["channel"],
            )
            sensors.setdefault(ref.reactor, {}).setdefault(
                ref.name,
                [],
            ).append(ref)

        actuators: dict[str, dict[str, dict[str, VariableRef]]] = {}
        for nodeid, info in actuator_vars.items():
            ref = VariableRef(
                nodeid,
                info["reactor"],
                info["name"],
                info["channel"],
            )
            actuators.setdefault(ref.reactor, {}).setdefault(ref.name, {})[
                ref.channel
            ] = ref

        by_key: dict[tuple[str, str | None, str], str] = {}
        for nodeid, info in methods.items():
            parts = list(info["name"])
            if len(parts) == 1:
                by_key[(info["reactor"], None, parts[0])] = nodeid
            elif len(parts) >= 2:  # noqa: PLR2004 - owner plus method
                by_key[(info["reactor"], parts[0], parts[-1])] = nodeid

        frozen_sensors = {
            reactor: {
                name: tuple(sorted(refs, key=lambda r: r.channel))
                for name, refs in names.items()
            }
            for reactor, names in sensors.items()
        }
        return cls(frozen_sensors, actuators, by_key)

    @classmethod
    def from_client(cls, client: OpcClient) -> AddressBook:
        """Index a connected client's browse results."""
        return cls.build(
            client.sensor_vars,
            client.actuator_vars,
            client.methods,
        )

    @property
    def reactors(self) -> tuple[str, ...]:
        """Every reactor id, sorted, so the UI order is stable."""
        return tuple(sorted({*self._sensors, *self._actuators}))

    def sensors(self, reactor: str) -> dict[str, tuple[VariableRef, ...]]:
        """Sensor name -> its channel variables, sorted by channel."""
        return self._sensors.get(reactor, {})

    def actuators(self, reactor: str) -> dict[str, dict[str, VariableRef]]:
        """Actuator name -> {channel: variable}."""
        return self._actuators.get(reactor, {})

    def variable(
        self,
        reactor: str,
        name: str,
        channel: str,
    ) -> str | None:
        """Node id of one variable, or ``None`` if it is not published."""
        actuator = self._actuators.get(reactor, {}).get(name, {})
        if channel in actuator:
            return actuator[channel].nodeid
        for ref in self._sensors.get(reactor, {}).get(name, ()):
            if ref.channel == channel:
                return ref.nodeid
        return None

    def method(
        self,
        reactor: str,
        owner: str | None,
        name: str,
    ) -> str | None:
        """Node id of a method.

        Parameters
        ----------
        reactor:
            Reactor id, e.g. ``R0``.
        owner:
            The sensor or actuator name the method hangs off, or
            ``None`` for a reactor-level method such as ``set_pairing``.
        name:
            The bare method name.

        """
        return self._methods.get((reactor, owner, name))
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_gui_address.py -v`
Expected: 8 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check reactors_czlab/gui/ tests/test_gui_address.py
git add reactors_czlab/gui/ tests/test_gui_address.py
git commit -m "feat: index the OPC address space for the GUI"
```

---

## Task 10: `gui/format.py` - rendering values honestly

**Files:**
- Create: `reactors_czlab/gui/format.py`
- Test: `tests/test_gui_format.py`

**Interfaces:**
- Consumes: `core.data.ERROR_VALUE`.
- Produces: `is_error(value: float) -> bool`;
  `render_value(value: float, units: str = "", digits: int = 3) -> str`;
  `is_stale(timestamp: datetime | None, now: datetime, period: float) -> bool`;
  constants `ERROR_TEXT`, `MISSING_TEXT`, `STALE_FACTOR`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gui_format.py`:

```python
"""Tests for how the GUI renders a reading."""

from __future__ import annotations

from datetime import datetime, timedelta

from reactors_czlab.core.data import ERROR_VALUE
from reactors_czlab.gui.format import (
    ERROR_TEXT,
    MISSING_TEXT,
    is_error,
    is_stale,
    render_value,
)


def test_the_error_sentinel_is_recognised() -> None:
    """A failed read is -0.111, compared against the constant."""
    assert is_error(ERROR_VALUE) is True
    assert is_error(7.0) is False


def test_the_sentinel_never_renders_as_a_number() -> None:
    """An operator must not read -0.111 as a measurement."""
    assert render_value(ERROR_VALUE, units="pH") == ERROR_TEXT


def test_a_reading_renders_with_its_units() -> None:
    """Units come from the channel, not from the value."""
    assert render_value(7.1234, units="pH") == "7.123 pH"


def test_a_reading_without_units_renders_bare() -> None:
    """Biomass channels are dimensionless."""
    assert render_value(1234.5, units="") == "1234.500"


def test_a_missing_value_is_distinct_from_a_failed_read() -> None:
    """Nothing published yet is not the same as a probe that failed."""
    assert render_value(None) == MISSING_TEXT


def test_a_fresh_reading_is_not_stale() -> None:
    """One sample period old is still current."""
    now = datetime(2026, 7, 30, 12, 0, 0)
    assert is_stale(now - timedelta(seconds=5), now, period=10.0) is False


def test_an_old_reading_is_stale() -> None:
    """Past the grace factor the UI must say so, not show a number."""
    now = datetime(2026, 7, 30, 12, 0, 0)
    assert is_stale(now - timedelta(seconds=60), now, period=10.0) is True


def test_a_reading_that_never_arrived_is_stale() -> None:
    """No timestamp means nothing has been published."""
    now = datetime(2026, 7, 30, 12, 0, 0)
    assert is_stale(None, now, period=10.0) is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_gui_format.py -v`
Expected: FAIL - `ModuleNotFoundError: No module named 'reactors_czlab.gui.format'`

- [ ] **Step 3: Implement**

Create `reactors_czlab/gui/format.py`:

```python
"""Turn a published reading into something safe to show an operator.

Pure, and separate from the pages for that reason: the one rule worth
testing here is that ``ERROR_VALUE`` never reaches a screen looking like
a measurement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from reactors_czlab.core.data import ERROR_VALUE

if TYPE_CHECKING:
    from datetime import datetime

#: Shown in place of a reading the server could not take.
ERROR_TEXT = "read failed"

#: Shown for a variable that has published nothing yet.
MISSING_TEXT = "-"

#: How many sample periods a reading may be old before it is called
#: stale. Three, so one missed sample does not flag the whole dashboard.
STALE_FACTOR = 3.0


def is_error(value: float | None) -> bool:
    """Whether a value is the failed-read sentinel."""
    return value == ERROR_VALUE


def render_value(
    value: float | None,
    units: str = "",
    digits: int = 3,
) -> str:
    """Format a reading for display.

    Parameters
    ----------
    value:
        The published value, or ``None`` if nothing has arrived.
    units:
        The channel's units, appended when there are any.
    digits:
        Decimal places.

    Returns
    -------
    str
        ``ERROR_TEXT`` for the sentinel, ``MISSING_TEXT`` for ``None``,
        otherwise the number and its units. The sentinel is never shown
        as ``-0.111``: an operator reading that as a pH would act on a
        dead probe.

    """
    if value is None:
        return MISSING_TEXT
    if is_error(value):
        return ERROR_TEXT
    rendered = f"{value:.{digits}f}"
    return f"{rendered} {units}" if units else rendered


def is_stale(
    timestamp: datetime | None,
    now: datetime,
    period: float,
) -> bool:
    """Whether a reading is too old to be shown as current.

    A subscription that has quietly died leaves the last value in place
    forever, which looks exactly like a steady process. Age is the only
    thing that distinguishes them.
    """
    if timestamp is None:
        return True
    return (now - timestamp).total_seconds() > period * STALE_FACTOR
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_gui_format.py -v`
Expected: 8 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check reactors_czlab/gui/format.py tests/test_gui_format.py
git add reactors_czlab/gui/format.py tests/test_gui_format.py
git commit -m "feat: render readings without ever showing the error sentinel"
```

---

## Task 11: `gui/control.py` - the ordered write plan

**Files:**
- Create: `reactors_czlab/gui/control.py`
- Test: `tests/test_gui_control.py`

**Interfaces:**
- Consumes: `core.data.ControlMethod`, `core.data.OutputUnit`,
  `opcua.actuator.control_method`, `opcua.actuator.output_unit_map`;
  Task 5's `get_calibration()` payload shape.
- Produces: `Write` (frozen dataclass: `channel: str`, `value: object`);
  `METHOD_FIELDS: dict[ControlMethod, tuple[str, ...]]`;
  `build_write_plan(method, unit, fields) -> list[Write]`;
  `unit_rejection_reason(unit, calibration) -> str | None`;
  `method_index(method) -> int`; `unit_index(unit) -> int`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gui_control.py`:

```python
"""Tests for the control-config write plan.

The server rebuilds an entire ControlConfig on every variable change, so
the order the GUI writes in is a safety property, not a style choice.
"""

from __future__ import annotations

from reactors_czlab.core.data import ControlMethod, OutputUnit
from reactors_czlab.gui.control import (
    METHOD_FIELDS,
    build_write_plan,
    method_index,
    unit_index,
    unit_rejection_reason,
)

FITTED = {"is_fitted": True, "fitted_at": "2026-07-27T10:00:00+00:00"}
UNFITTED = {"is_fitted": False, "fitted_at": ""}


def test_method_is_always_written_last() -> None:
    """Regression: writing method first applies the new controller
    against whatever stale setpoint and gains are still in the server's
    variables. ActuatorOpc.datachange_notification rebuilds the whole
    config on every notification, so a manual -> pid switch could drive
    hard for one notification before the real setpoint landed.
    """
    plan = build_write_plan(
        ControlMethod.pid,
        OutputUnit.duty,
        {"setpoint": 7.0, "kp": 50.0, "ki": 0.02, "kd": 0.0,
         "backwards": False, "min_integral": 0.0, "max_integral": 100.0,
         "auto_integral_band": True, "value": 0.0},
    )

    assert plan[-1].channel == "method"
    assert plan[-2].channel == "output_unit"


def test_every_selected_field_is_written() -> None:
    """The plan covers exactly the fields the method uses, plus value."""
    plan = build_write_plan(
        ControlMethod.timer,
        OutputUnit.duty,
        {"time_on": 5.0, "time_off": 10.0, "value": 2000.0},
    )

    written = {write.channel for write in plan}
    assert written == {
        "time_on",
        "time_off",
        "value",
        "output_unit",
        "method",
    }


def test_unrelated_fields_are_not_written() -> None:
    """A manual config must not push stale PID gains at the server."""
    plan = build_write_plan(
        ControlMethod.manual,
        OutputUnit.duty,
        {"value": 1500.0, "kp": 999.0},
    )

    assert [write.channel for write in plan] == [
        "value",
        "output_unit",
        "method",
    ]


def test_the_enum_indices_match_the_server() -> None:
    """The GUI writes integers; the server maps them back to enums."""
    assert method_index(ControlMethod.pid) == 3
    assert unit_index(OutputUnit.volume) == 2


def test_method_fields_covers_every_method() -> None:
    """A new ControlMethod without an entry would write nothing."""
    assert set(METHOD_FIELDS) == set(ControlMethod)


def test_duty_is_allowed_without_a_calibration() -> None:
    """Raw counts need nothing from the calibration."""
    assert unit_rejection_reason(OutputUnit.duty, UNFITTED) is None


def test_flow_needs_a_fitted_calibration() -> None:
    """check_unit() rejects this server-side and only logs it.

    The form has to refuse first, or the operator writes a config that
    is silently dropped and sees no reason why.
    """
    reason = unit_rejection_reason(OutputUnit.flow, UNFITTED)

    assert reason is not None
    assert "fitted" in reason


def test_volume_is_allowed_on_a_fitted_calibration() -> None:
    """A fitted line converts mL to a duty."""
    assert unit_rejection_reason(OutputUnit.volume, FITTED) is None


def test_a_missing_calibration_payload_is_refused() -> None:
    """No calibration slot at all is not a fitted calibration."""
    assert unit_rejection_reason(OutputUnit.flow, None) is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_gui_control.py -v`
Expected: FAIL - `ModuleNotFoundError: No module named 'reactors_czlab.gui.control'`

- [ ] **Step 3: Implement**

Create `reactors_czlab/gui/control.py`:

```python
"""Turn a filled-in configuration form into an ordered set of writes.

``ActuatorOpc.datachange_notification`` rebuilds an entire
``ControlConfig`` on *every* variable change and hands it to
``Actuator.set_control_config``. So the GUI cannot write a configuration
atomically, and the order it writes in decides what the actuator does in
between. Writing ``method`` first would apply the new controller against
whatever stale setpoint, bounds and gains are still sitting in the
server's variables.

The rule: parameters first, ``output_unit`` next, ``method`` last. Every
intermediate notification then keeps the *old* method, and only the last
write commits the new one.

Pure: no nicegui, no network.
"""

from __future__ import annotations

from dataclasses import dataclass

from reactors_czlab.core.data import ControlMethod, OutputUnit
from reactors_czlab.opcua.actuator import control_method, output_unit_map

#: Which configuration variables each method actually consumes. Mirrors
#: the ``match`` in ``ActuatorOpc.datachange_notification``: writing a
#: variable the selected method ignores is harmless but pointless, and
#: writing a stale one the method *does* consume is not.
METHOD_FIELDS: dict[ControlMethod, tuple[str, ...]] = {
    ControlMethod.manual: (),
    ControlMethod.timer: ("time_on", "time_off"),
    ControlMethod.on_boundaries: ("lb", "ub", "backwards"),
    ControlMethod.pid: (
        "setpoint",
        "kp",
        "ki",
        "kd",
        "backwards",
        "min_integral",
        "max_integral",
        "auto_integral_band",
    ),
}


@dataclass(frozen=True)
class Write:
    """One variable write: the channel segment and the value."""

    channel: str
    value: object


def method_index(method: ControlMethod) -> int:
    """The integer the server's ``method`` variable expects."""
    return next(k for k, v in control_method.items() if v is method)


def unit_index(unit: OutputUnit) -> int:
    """The integer the server's ``output_unit`` variable expects."""
    return next(k for k, v in output_unit_map.items() if v is unit)


def build_write_plan(
    method: ControlMethod,
    unit: OutputUnit,
    fields: dict[str, object],
) -> list[Write]:
    """Order the writes that install a configuration.

    Parameters
    ----------
    method:
        The control method to install.
    unit:
        The output unit the demand is expressed in.
    fields:
        Form values, keyed by the channel segment of the browse name
        (``setpoint``, ``kp``, ``value``, ...). Keys the selected method
        does not consume are ignored.

    Returns
    -------
    list of Write
        Parameters first, then ``output_unit``, then ``method``. Callers
        must write them in this order and must not parallelise them.

    """
    plan = [
        Write(field, fields[field])
        for field in METHOD_FIELDS[method]
        if field in fields
    ]
    if "value" in fields:
        plan.append(Write("value", fields["value"]))
    plan.append(Write("output_unit", unit_index(unit)))
    plan.append(Write("method", method_index(method)))
    return plan


def unit_rejection_reason(
    unit: OutputUnit,
    calibration: dict | None,
) -> str | None:
    """Why this output unit cannot be used on this pump, if it cannot.

    ``core.dispenser.check_unit()`` asks the same question server-side
    and rejects the whole configuration when the answer is no - but it
    only logs the reason, and ``set_control_config`` returns nothing, so
    the operator would see a write succeed and nothing happen. Asking it
    here first is the only way the reason reaches a screen.

    Parameters
    ----------
    unit:
        The unit the form is about to install.
    calibration:
        The payload from the actuator's ``get_calibration()`` method, or
        ``None`` if it could not be read.

    Returns
    -------
    str or None
        ``None`` when the unit is usable, otherwise a reason safe to
        show verbatim.

    """
    if unit is OutputUnit.duty:
        return None
    if calibration is None:
        return (
            f"{unit} needs a calibration, and this actuator's could not "
            "be read"
        )
    if not calibration.get("is_fitted"):
        return (
            f"{unit} needs a fitted calibration; this pump has never "
            "been calibrated, so mL cannot be converted to a duty. Run "
            "a calibration first, or use duty."
        )
    return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_gui_control.py -v`
Expected: 9 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check reactors_czlab/gui/control.py tests/test_gui_control.py
uv run pytest
git add reactors_czlab/gui/control.py tests/test_gui_control.py
git commit -m "feat: order control-config writes so method lands last"
```

---

## Task 12: Packaging, app state and the entry point

**Files:**
- Modify: `pyproject.toml`
- Create: `reactors_czlab/gui/state.py`, `reactors_czlab/gui/pages/__init__.py`,
  `reactors_czlab/gui/components/__init__.py`, `reactors_czlab/run_gui.py`

**Interfaces:**
- Consumes: Task 9's `AddressBook`, Task 8's `OpcClient` recording API,
  Task 6's `operations.PSYCOPG_AVAILABLE`.
- Produces: `AppState` with `client: OpcClient | None`,
  `book: AddressBook | None`, `endpoint: str`, `period: float`,
  `connected: bool`, `database_available: bool`, `connection_error: str | None`,
  `async connect()`, `async disconnect()`,
  `reading(reactor, name, channel) -> tuple[float | None, datetime | None]`,
  `async write_variable(reactor, name, channel, value) -> bool`,
  `async call(reactor, owner, method, *args) -> object`;
  module-level `STATE: AppState`. `run_gui.cli()`.

**Note:** this task has no unit tests. `AppState` is a thin adapter over
`OpcClient` plus a live network connection, and `tests/` has no nicegui and no
server; every decision it could make has already been factored into
`address.py`, `format.py` and `control.py`, which are tested. Step 5 is a
manual smoke check against the simulated server, and it is not optional.

- [ ] **Step 1: Add the extra and the script**

In `pyproject.toml`, add to `[project.optional-dependencies]`:

```toml
gui = [
    "nicegui>=2.0",
]
```

and to `[project.scripts]`:

```toml
reactors-gui = "reactors_czlab.run_gui:cli"
```

- [ ] **Step 2: Write the app state**

Create `reactors_czlab/gui/components/__init__.py` containing only:

```python
"""Docstring only: see reactors_czlab/gui/__init__.py."""
```

`reactors_czlab/gui/pages/__init__.py` is created in Step 3 - it is the one
package init in `gui/` that is not docstring-only, because importing it is
what registers the `@ui.page` routes.

Create `reactors_czlab/gui/state.py`:

```python
"""The one connection and the one address book, shared by every page.

The GUI hosts the OPC client and the archiver in its own event loop, so
there is exactly one of each per process. ``OpcClient.variables`` - the
dict its subscription callback already maintains - is the read model;
pages poll it on a timer rather than being pushed to.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from reactors_czlab.gui.address import AddressBook
from reactors_czlab.opcua.client import OpcClient
from reactors_czlab.sql import operations

if TYPE_CHECKING:
    from datetime import datetime

_logger = logging.getLogger("gui")

DEFAULT_ENDPOINT = "opc.tcp://10.10.10.20:55488/"

#: Assumed sampling period, for judging whether a reading is stale. The
#: server's SAMPLE_PERIOD is not published, so this is a setting.
DEFAULT_PERIOD = 10.0


class AppState:
    """Everything the pages share."""

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        period: float = DEFAULT_PERIOD,
    ) -> None:
        """Store the connection settings; nothing connects yet."""
        self.endpoint = endpoint
        self.period = period
        self.client: OpcClient | None = None
        self.book: AddressBook | None = None
        self.connection_error: str | None = None

    def __repr__(self) -> str:
        """Print the endpoint and whether it is connected."""
        return f"AppState({self.endpoint}, connected={self.connected})"

    @property
    def connected(self) -> bool:
        """Whether the OPC client is up and the address space browsed."""
        return self.book is not None

    @property
    def database_available(self) -> bool:
        """Whether recording, experiments and plot history can work.

        Import-level, not connection-level: a missing psycopg disables
        those screens, and the reason is shown rather than raised.
        """
        return operations.PSYCOPG_AVAILABLE

    @property
    def recording(self) -> bool:
        """Whether the archiver is running."""
        return self.client is not None and self.client.recording

    async def connect(self) -> None:
        """Connect, browse and subscribe. Never raises.

        A server that is not up yet is the normal state on boot, so the
        failure is recorded for the UI to show and retried by the page's
        reconnect button rather than taking the process down.
        """
        client = OpcClient(self.endpoint)
        try:
            await client.connect()
            await client.init_subscriptions()
        except Exception as err:  # noqa: BLE001 - reported, not raised
            self.connection_error = f"{type(err).__name__}: {err}"
            _logger.warning(
                "Could not connect to %s: %s",
                self.endpoint,
                self.connection_error,
            )
            await client.disconnect()
            return

        self.client = client
        self.book = AddressBook.from_client(client)
        self.connection_error = None
        _logger.info("Connected to %s", self.endpoint)

    async def disconnect(self) -> None:
        """Drop the connection and the address book."""
        if self.client is not None:
            await self.client.disconnect()
        self.client = None
        self.book = None

    def reading(
        self,
        reactor: str,
        name: str,
        channel: str,
    ) -> tuple[float | None, datetime | None]:
        """The last published value of one variable, and when it arrived."""
        if self.client is None or self.book is None:
            return (None, None)
        nodeid = self.book.variable(reactor, name, channel)
        if nodeid is None:
            return (None, None)
        info = self.client.variables.get(nodeid, {})
        return (info.get("value"), info.get("timestamp"))

    async def write_variable(
        self,
        reactor: str,
        name: str,
        channel: str,
        value: object,
    ) -> bool:
        """Write one variable. Returns False if it could not be written."""
        if self.client is None or self.book is None:
            return False
        nodeid = self.book.variable(reactor, name, channel)
        if nodeid is None:
            _logger.error("No node for %s:%s:%s", reactor, name, channel)
            return False
        return await self.client.write(nodeid, value)

    async def call(
        self,
        reactor: str,
        owner: str | None,
        method: str,
        *args: object,
    ) -> object:
        """Call an OPC method by name.

        Raises
        ------
        LookupError
            If the method is not in the address book. A page that calls
            a method the server does not have is a bug, not an
            operational failure.

        """
        if self.client is None or self.book is None:
            error_message = "not connected"
            raise LookupError(error_message)
        nodeid = self.book.method(reactor, owner, method)
        if nodeid is None:
            error_message = f"No method {method} on {reactor}/{owner}"
            raise LookupError(error_message)
        return await self.client.call_method(nodeid, *args)


#: The process-wide state. Pages import this.
STATE = AppState()
```

- [ ] **Step 3: Write the entry point**

Create `reactors_czlab/run_gui.py`:

```python
"""Web GUI for the bioreactor controller.

Runs on the PC and on the Raspberry Pi. It hosts the OPC UA client and
the archiver in its own event loop, so recording is a task this process
starts and stops.
"""

from __future__ import annotations

import argparse
import logging

from nicegui import app, ui

from reactors_czlab.gui import pages  # noqa: F401 - registers the routes
from reactors_czlab.gui.state import DEFAULT_ENDPOINT, DEFAULT_PERIOD, STATE

_logger = logging.getLogger("gui")

DEFAULT_PORT = 8080


def setup_logging(verbose: bool = True) -> None:
    """Attach the file and stream handlers to the gui logger."""
    _logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter(
        "%(name)s: %(asctime)s %(levelname)s - %(message)s",
    )

    file_handler = logging.FileHandler("gui.log")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(logging.DEBUG if verbose else logging.INFO)

    _logger.addHandler(file_handler)
    _logger.addHandler(stream_handler)


def cli() -> None:
    """Parse the command line and serve the GUI."""
    parser = argparse.ArgumentParser(description="Run the bioreactor GUI")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--period", type=float, default=DEFAULT_PERIOD)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    setup_logging(verbose=not args.quiet)
    STATE.endpoint = args.endpoint
    STATE.period = args.period

    app.on_startup(STATE.connect)
    app.on_shutdown(STATE.disconnect)

    ui.run(
        host=args.host,
        port=args.port,
        title="Bioreactors",
        reload=False,
        show=False,
    )


if __name__ in {"__main__", "__mp_main__"}:
    cli()
```

Create a placeholder `reactors_czlab/gui/pages/dashboard.py` so the import
in `run_gui.py` resolves - Task 13 fills it in:

```python
"""Reactor dashboard routes."""

from __future__ import annotations

from nicegui import ui


@ui.page("/")
def index() -> None:
    """Placeholder, replaced in the dashboard task."""
    ui.label("Bioreactors")
```

and create `reactors_czlab/gui/pages/__init__.py`:

```python
"""Page modules. Importing this registers every route."""

from reactors_czlab.gui.pages import dashboard  # noqa: F401
```

- [ ] **Step 4: Verify nothing regressed**

Run: `uv run ruff check reactors_czlab/ && uv run pytest`
Expected: ruff clean, all tests pass (the GUI is not imported by any test)

- [ ] **Step 5: Manual smoke check - required**

In one terminal:

```bash
uv run --extra gui reactors-server --simulated --endpoint opc.tcp://localhost:4840/
```

In another:

```bash
uv run --extra gui reactors-gui --endpoint opc.tcp://localhost:4840/ --port 8080
```

Open `http://localhost:8080`. Expected: the page loads, and `gui.log` contains
`Connected to opc.tcp://localhost:4840/`. If it says "Could not connect",
stop and fix before continuing - every later task depends on this.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml reactors_czlab/gui/ reactors_czlab/run_gui.py
git commit -m "feat: serve the GUI and hold one OPC connection for it"
```

---

## Task 13: The reactor dashboard

**Files:**
- Create: `reactors_czlab/gui/components/values.py`
- Modify: `reactors_czlab/gui/pages/dashboard.py`

**Interfaces:**
- Consumes: Task 9's `AddressBook`, Task 10's `render_value`/`is_stale`,
  Task 12's `STATE`.
- Produces: `sensor_panel(reactor: str) -> None` and
  `actuator_panel(reactor: str) -> None`, both `@ui.refreshable`;
  `header(reactor: str | None) -> None`. Routes `/` and `/reactor/{reactor}`.

- [ ] **Step 1: Write the value panels**

Create `reactors_czlab/gui/components/values.py`:

```python
"""Live sensor and actuator panels.

Every decision worth testing already lives in ``gui/format.py`` and
``gui/address.py``; what is here is assembly. The panels are
``ui.refreshable`` and are driven by a ``ui.timer`` on the page, reading
``STATE`` - which reads ``OpcClient.variables``, the dict the
subscription callback maintains.
"""

from __future__ import annotations

from datetime import datetime

from nicegui import ui

from reactors_czlab.gui.format import is_stale, render_value
from reactors_czlab.gui.state import STATE

#: Actuator channels shown on the card, in order, with their labels.
ACTUATOR_CHANNELS = (
    ("curr_value", "Output", ""),
    ("total_volume", "Delivered", "mL"),
)

#: Calibration channels shown under the fitted line.
CALIBRATION_CHANNELS = (
    ("cal_a", "a"),
    ("cal_b", "b"),
    ("cal_r2", "r2"),
)


def _value_chip(label: str, text: str, stale: bool) -> None:
    """One labelled reading, greyed out when it is stale."""
    with ui.column().classes("gap-0 items-start"):
        ui.label(label).classes("text-xs text-gray-500")
        chip = ui.label(text).classes("text-lg font-mono")
        if stale:
            chip.classes("text-gray-400 line-through")


@ui.refreshable
def sensor_panel(reactor: str) -> None:
    """One row per sensor, one chip per channel."""
    if STATE.book is None:
        ui.label("Not connected")
        return

    now = datetime.now()
    sensors = STATE.book.sensors(reactor)
    if not sensors:
        ui.label("No sensors on this reactor")
        return

    for name, refs in sorted(sensors.items()):
        with ui.card().classes("w-full"):
            ui.label(name).classes("text-sm font-semibold")
            with ui.row().classes("gap-6 flex-wrap"):
                for ref in refs:
                    value, stamp = STATE.reading(reactor, name, ref.channel)
                    _value_chip(
                        ref.channel,
                        render_value(value, units=ref.channel),
                        is_stale(stamp, now, STATE.period),
                    )


@ui.refreshable
def actuator_panel(reactor: str) -> None:
    """One card per actuator, with its output, totals and fitted line."""
    if STATE.book is None:
        ui.label("Not connected")
        return

    now = datetime.now()
    actuators = STATE.book.actuators(reactor)
    if not actuators:
        ui.label("No actuators on this reactor")
        return

    for name in sorted(actuators):
        with ui.card().classes("w-full"):
            ui.label(name).classes("text-sm font-semibold")
            with ui.row().classes("gap-6 flex-wrap"):
                for channel, label, units in ACTUATOR_CHANNELS:
                    value, stamp = STATE.reading(reactor, name, channel)
                    _value_chip(
                        label,
                        render_value(value, units=units),
                        is_stale(stamp, now, STATE.period),
                    )
            with ui.row().classes("gap-4 flex-wrap"):
                for channel, label in CALIBRATION_CHANNELS:
                    value, _ = STATE.reading(reactor, name, channel)
                    ui.label(
                        f"{label} {render_value(value, digits=4)}",
                    ).classes("text-xs text-gray-500 font-mono")
```

- [ ] **Step 2: Write the pages**

Replace `reactors_czlab/gui/pages/dashboard.py`:

```python
"""Reactor dashboard routes.

Assembly only: no decisions are made here. Anything that had to be
decided is in gui/address.py, gui/format.py or gui/control.py, where a
test can reach it.
"""

from __future__ import annotations

from nicegui import ui

from reactors_czlab.gui.components.values import (
    actuator_panel,
    sensor_panel,
)
from reactors_czlab.gui.state import STATE

#: How often the panels re-read the in-memory values, in seconds. The
#: server publishes on its sampling period; this only has to be fast
#: enough to feel live.
REFRESH_SECONDS = 1.0


def header(reactor: str | None = None) -> None:
    """The bar every page carries: connection, recording, reactor."""
    with ui.header().classes("items-center justify-between"):
        with ui.row().classes("items-center gap-4"):
            ui.link("Bioreactors", "/").classes(
                "text-lg font-semibold text-white no-underline",
            )
            if reactor is not None:
                ui.label(reactor).classes("text-white")
        with ui.row().classes("items-center gap-3"):
            if STATE.connected:
                ui.badge("connected", color="green")
            else:
                ui.badge("disconnected", color="red")
            if not STATE.database_available:
                ui.badge("no database", color="orange")
            elif STATE.recording:
                ui.badge("recording", color="blue")
            else:
                ui.badge("not recording", color="grey")


@ui.page("/")
def index() -> None:
    """List the reactors, or say why there are none."""
    header()
    with ui.column().classes("w-full p-4 gap-4"):
        if not STATE.connected:
            ui.label(
                STATE.connection_error
                or f"Connecting to {STATE.endpoint}...",
            ).classes("text-red-600")
            ui.button("Retry", on_click=STATE.connect)
            return

        ui.label("Reactors").classes("text-xl font-semibold")
        with ui.row().classes("gap-4 flex-wrap"):
            for reactor in STATE.book.reactors:
                with ui.card().classes("w-64"):
                    ui.label(reactor).classes("text-lg font-semibold")
                    ui.label(
                        f"{len(STATE.book.sensors(reactor))} sensors, "
                        f"{len(STATE.book.actuators(reactor))} actuators",
                    ).classes("text-sm text-gray-500")
                    ui.button(
                        "Open",
                        on_click=lambda r=reactor: ui.navigate.to(
                            f"/reactor/{r}",
                        ),
                    )


@ui.page("/reactor/{reactor}")
def reactor_page(reactor: str) -> None:
    """Live values for one reactor."""
    header(reactor)
    with ui.column().classes("w-full p-4 gap-4"):
        if not STATE.connected:
            ui.label("Not connected").classes("text-red-600")
            return

        ui.label("Sensors").classes("text-lg font-semibold")
        sensor_panel(reactor)

        ui.label("Actuators").classes("text-lg font-semibold")
        actuator_panel(reactor)

    def refresh() -> None:
        """Re-read the in-memory values."""
        sensor_panel.refresh()
        actuator_panel.refresh()

    ui.timer(REFRESH_SECONDS, refresh)
```

- [ ] **Step 3: Lint and run the suite**

Run: `uv run ruff check reactors_czlab/gui/ && uv run pytest`
Expected: ruff clean, all tests pass

- [ ] **Step 4: Manual check - required**

With the simulated server and the GUI running (as in Task 12 Step 5), open
`http://localhost:8080`. Expected: three reactor cards; opening R0 shows
`ph`, `do` and `biomass` with values changing about once a second, and four
`pwm` cards plus `mfc`. Confirm the values move.

Then stop the server. Expected: within ~30 s every reading greys out and is
struck through rather than sitting at its last value.

- [ ] **Step 5: Commit**

```bash
git add reactors_czlab/gui/
git commit -m "feat: live reactor dashboard"
```

---

## Task 14: The actuator configuration dialog

**Files:**
- Create: `reactors_czlab/gui/components/control_form.py`
- Modify: `reactors_czlab/gui/components/values.py` (add the button)

**Interfaces:**
- Consumes: Task 11's `build_write_plan`, `unit_rejection_reason`,
  `METHOD_FIELDS`; Task 12's `STATE`.
- Produces: `async open_control_dialog(reactor: str, name: str) -> None`.

- [ ] **Step 1: Write the dialog**

Create `reactors_czlab/gui/components/control_form.py`:

```python
"""Configure one actuator's controller.

The write ordering is not this module's decision - ``gui/control.py``
builds the plan and a test pins it. This applies the plan in order,
sequentially. It must never write the plan concurrently: every write
triggers a full config rebuild on the server, and the whole point of the
ordering is that ``method`` lands last.
"""

from __future__ import annotations

import json
import logging

from nicegui import ui

from reactors_czlab.core.data import ControlMethod, OutputUnit
from reactors_czlab.gui.control import (
    METHOD_FIELDS,
    build_write_plan,
    unit_rejection_reason,
)
from reactors_czlab.gui.state import STATE

_logger = logging.getLogger("gui")

#: Numeric fields and their labels, keyed by the channel segment.
FIELD_LABELS = {
    "value": "Demand",
    "time_on": "Time on (s)",
    "time_off": "Time off (s)",
    "lb": "Lower bound",
    "ub": "Upper bound",
    "setpoint": "Setpoint",
    "kp": "kp",
    "ki": "ki",
    "kd": "kd",
    "min_integral": "Min integral",
    "max_integral": "Max integral",
}

#: Fields rendered as switches rather than number inputs.
BOOLEAN_FIELDS = ("backwards", "auto_integral_band")


async def _read_calibration(reactor: str, name: str) -> dict | None:
    """The actuator's calibration payload, or None if unreadable."""
    try:
        raw = await STATE.call(reactor, name, "get_calibration")
    except Exception:  # noqa: BLE001 - a missing payload, not a crash
        _logger.warning(
            "Could not read the calibration of %s:%s",
            reactor,
            name,
            exc_info=True,
        )
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        _logger.warning("Unreadable calibration payload for %s", name)
        return None


async def open_control_dialog(reactor: str, name: str) -> None:
    """Open the configuration dialog for one actuator."""
    calibration = await _read_calibration(reactor, name)

    current = {
        field: STATE.reading(reactor, name, field)[0]
        for field in (*FIELD_LABELS, *BOOLEAN_FIELDS)
    }

    with ui.dialog() as dialog, ui.card().classes("w-[32rem]"):
        ui.label(f"{name} control").classes("text-lg font-semibold")

        method_select = ui.select(
            {m: m.value for m in ControlMethod},
            value=ControlMethod.manual,
            label="Method",
        ).classes("w-full")
        unit_select = ui.select(
            {u: u.value for u in OutputUnit},
            value=OutputUnit.duty,
            label="Output unit",
        ).classes("w-full")

        warning = ui.label("").classes("text-orange-600 text-sm")
        inputs: dict[str, object] = {}

        @ui.refreshable
        def fields() -> None:
            """Show only what the selected method consumes."""
            inputs.clear()
            shown = ("value", *METHOD_FIELDS[method_select.value])
            for field in shown:
                if field in BOOLEAN_FIELDS:
                    inputs[field] = ui.switch(
                        field.replace("_", " "),
                        value=bool(current.get(field) or False),
                    )
                else:
                    inputs[field] = ui.number(
                        FIELD_LABELS[field],
                        value=float(current.get(field) or 0.0),
                    ).classes("w-full")

        def on_unit_change() -> None:
            """Warn before an unusable unit is written, not after."""
            reason = unit_rejection_reason(unit_select.value, calibration)
            warning.set_text(reason or "")

        method_select.on_value_change(lambda _: fields.refresh())
        unit_select.on_value_change(lambda _: on_unit_change())
        fields()
        on_unit_change()

        async def apply() -> None:
            """Write the plan in order, stopping at the first failure."""
            reason = unit_rejection_reason(unit_select.value, calibration)
            if reason is not None:
                ui.notify(reason, type="negative")
                return

            plan = build_write_plan(
                method_select.value,
                unit_select.value,
                {
                    field: widget.value
                    for field, widget in inputs.items()
                },
            )
            for write in plan:
                ok = await STATE.write_variable(
                    reactor,
                    name,
                    write.channel,
                    write.value,
                )
                if not ok:
                    ui.notify(
                        f"Failed writing {write.channel}; the actuator "
                        "is part configured - check record.log",
                        type="negative",
                    )
                    return
            ui.notify(f"{name} configured", type="positive")
            dialog.close()

        with ui.row().classes("justify-end w-full gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Apply", on_click=apply)

    dialog.open()
```

- [ ] **Step 2: Add the button to the actuator card**

In `reactors_czlab/gui/components/values.py`, add the import:

```python
from reactors_czlab.gui.components.control_form import open_control_dialog
```

and inside `actuator_panel`'s per-actuator card, after the calibration row:

```python
            with ui.row().classes("gap-2"):
                ui.button(
                    "Configure",
                    on_click=lambda r=reactor, n=name: open_control_dialog(
                        r,
                        n,
                    ),
                ).props("outline size=sm")
```

- [ ] **Step 3: Lint and run the suite**

Run: `uv run ruff check reactors_czlab/gui/ && uv run pytest`
Expected: ruff clean, all tests pass

- [ ] **Step 4: Manual check - required**

With the simulated server running, open R0, click Configure on `pwm0`:

1. Select `manual`, unit `duty`, demand 1500, Apply. Expected: the card's
   Output reaches 1500 within a couple of seconds.
2. Select `pid`, set a setpoint and gains, Apply. Expected: the server log
   shows one `Control config update` line, and the config variables read
   back the values you typed.
3. Select unit `flow` on an uncalibrated pump. Expected: the orange warning
   appears immediately and Apply refuses with a notification - **the server
   log must show no rejected-config warning**, because the write never
   happened.

- [ ] **Step 5: Commit**

```bash
git add reactors_czlab/gui/
git commit -m "feat: configure an actuator's controller from the dashboard"
```

---

## Task 15: The pairing panel

**Files:**
- Create: `reactors_czlab/gui/components/pairing.py`
- Modify: `reactors_czlab/gui/pages/dashboard.py`

**Interfaces:**
- Consumes: Task 4's `R{n}:pairings` variable and the `ChannelIndex`
  properties from Task 3; Task 12's `STATE`.
- Produces: `pairing_panel(reactor: str) -> None` (`@ui.refreshable`);
  `async read_pairings(reactor: str) -> list[dict]`;
  `async read_channel_indices(reactor: str, sensor: str) -> dict[str, int]`.

- [ ] **Step 1: Write the panel**

Create `reactors_czlab/gui/components/pairing.py`:

```python
"""Pair and unpair actuators from the dashboard.

``set_pairing`` and ``unpair`` answer with a bare bool and log the reason
server-side, so this panel pre-checks everything ``_validate_pair`` and
``set_pairing`` check - reactor membership, and that the actuator is not
already paired. A False from the server is therefore genuinely
unexpected and is reported as such rather than being the normal failure
path.
"""

from __future__ import annotations

import json
import logging

from nicegui import ui

from reactors_czlab.gui.state import STATE

_logger = logging.getLogger("gui")

#: reactor id -> the node id of its pairings variable, found by browsing
#: once. Node ids are stable for the life of a server process, and the
#: address book cannot hold these (see read_pairings).
_PAIRINGS_NODES: dict[str, str] = {}


async def read_pairings(reactor: str) -> list[dict]:
    """The reactor's published pairing table.

    Returns
    -------
    list of dict
        ``{"sensor", "actuator", "channel"}`` rows, or an empty list if
        the variable is missing or unreadable.

    """
    if STATE.client is None or STATE.book is None:
        return []
    # The pairings variable hangs off the reactor node, above
    # R{n}:sensors / R{n}:actuators, so match_tree never indexes it and
    # the address book cannot resolve it. Browse for it once and cache.
    nodeid = _PAIRINGS_NODES.get(reactor)
    if nodeid is None:
        nodeid = await _find_pairings_node(reactor)
        if nodeid is None:
            return []
        _PAIRINGS_NODES[reactor] = nodeid
    try:
        raw = await STATE.client.client.get_node(nodeid).get_value()
        return json.loads(raw)
    except Exception:  # noqa: BLE001 - a stale picture, not a crash
        _logger.warning("Could not read %s pairings", reactor, exc_info=True)
        return []


async def _find_pairings_node(reactor: str) -> str | None:
    """Browse the objects folder for ``R{n}:pairings``."""
    if STATE.client is None:
        return None
    objects = STATE.client.client.nodes.objects
    for node in await objects.get_children():
        name = (await node.read_browse_name()).Name
        if name != reactor:
            continue
        for child in await node.get_children():
            child_name = (await child.read_browse_name()).Name
            if child_name == f"{reactor}:pairings":
                return child.nodeid.to_string()
    return None


async def read_channel_indices(reactor: str, sensor: str) -> dict[str, int]:
    """Channel name -> the index ``set_pairing`` expects.

    Read from the ``ChannelIndex`` property on each channel variable,
    not from browse order: asyncua does not guarantee ``get_children()``
    returns children in the order they were added.
    """
    if STATE.client is None or STATE.book is None:
        return {}
    indices: dict[str, int] = {}
    for ref in STATE.book.sensors(reactor).get(sensor, ()):
        node = STATE.client.client.get_node(ref.nodeid)
        try:
            for prop in await node.get_properties():
                name = (await prop.read_browse_name()).Name
                if name == "ChannelIndex":
                    indices[ref.channel] = int(await prop.get_value())
        except Exception:  # noqa: BLE001 - reported as a missing channel
            _logger.warning(
                "No channel index on %s:%s",
                sensor,
                ref.channel,
                exc_info=True,
            )
    return indices


@ui.refreshable
def pairing_panel(reactor: str) -> None:
    """Current pairings, with an add form and per-row unpair."""
    if STATE.book is None:
        ui.label("Not connected")
        return

    rows_container = ui.column().classes("w-full gap-1")
    sensors = sorted(STATE.book.sensors(reactor))
    actuators = sorted(STATE.book.actuators(reactor))

    sensor_select = ui.select(sensors, label="Sensor").classes("w-48")
    channel_select = ui.select([], label="Channel").classes("w-40")
    actuator_select = ui.select([], label="Actuator").classes("w-40")

    state: dict[str, object] = {"indices": {}, "paired": set()}

    async def reload() -> None:
        """Re-read the published table and rebuild the row list."""
        pairings = await read_pairings(reactor)
        state["paired"] = {row["actuator"] for row in pairings}
        rows_container.clear()
        with rows_container:
            if not pairings:
                ui.label("Nothing paired").classes("text-sm text-gray-500")
            for row in pairings:
                with ui.row().classes("items-center gap-3"):
                    ui.label(
                        f"{row['sensor']} ch{row['channel']} "
                        f"-> {row['actuator']}",
                    ).classes("font-mono text-sm")
                    ui.button(
                        "Unpair",
                        on_click=lambda r=row: do_unpair(r),
                    ).props("flat dense size=sm color=negative")
        # Only actuators that are free can be paired.
        actuator_select.set_options(
            [a for a in actuators if a not in state["paired"]],
        )

    async def on_sensor(_: object) -> None:
        """Load the selected sensor's channel indices."""
        if sensor_select.value is None:
            return
        indices = await read_channel_indices(reactor, sensor_select.value)
        state["indices"] = indices
        channel_select.set_options(sorted(indices))

    async def do_pair() -> None:
        """Pair after checking everything the server would check."""
        sensor = sensor_select.value
        channel = channel_select.value
        actuator = actuator_select.value
        if not (sensor and channel is not None and actuator):
            ui.notify("Choose a sensor, a channel and an actuator",
                      type="warning")
            return
        if actuator in state["paired"]:
            ui.notify(f"{actuator} is already paired", type="warning")
            return

        index = state["indices"].get(channel)
        if index is None:
            ui.notify(f"No channel index for {channel}", type="negative")
            return

        ok = await STATE.call(
            reactor,
            None,
            "set_pairing",
            f"{reactor}:{sensor}",
            f"{reactor}:{actuator}",
            index,
        )
        if ok:
            ui.notify(f"{actuator} follows {sensor}:{channel}",
                      type="positive")
        else:
            ui.notify(
                "The server refused the pairing; check record.log",
                type="negative",
            )
        await reload()

    async def do_unpair(row: dict) -> None:
        """Hand an actuator back to the unpaired loop."""
        ok = await STATE.call(
            reactor,
            None,
            "unpair",
            row["sensor"],
            row["actuator"],
            row["channel"],
        )
        if not ok:
            ui.notify(
                "The server refused the unpair; check record.log",
                type="negative",
            )
        await reload()

    sensor_select.on_value_change(on_sensor)
    with ui.row().classes("items-end gap-2"):
        ui.button("Pair", on_click=do_pair)

    ui.timer(0.1, reload, once=True)
```

- [ ] **Step 2: Add it to the reactor page**

In `reactors_czlab/gui/pages/dashboard.py`, add the import:

```python
from reactors_czlab.gui.components.pairing import pairing_panel
```

and inside `reactor_page`, after the actuators section:

```python
        ui.label("Pairings").classes("text-lg font-semibold")
        pairing_panel(reactor)
```

- [ ] **Step 3: Lint and run the suite**

Run: `uv run ruff check reactors_czlab/gui/ && uv run pytest`
Expected: ruff clean, all tests pass

- [ ] **Step 4: Manual check - required**

With the simulated server running, open R0:

1. Pair `ph` channel `pH` to `pwm0`. Expected: the row appears, `pwm0`
   disappears from the actuator dropdown, and the server log shows the new
   pairing table.
2. Reload the browser page. Expected: the row is still there - this is what
   `R{n}:pairings` exists for.
3. Unpair it. Expected: the row goes and `pwm0` returns to the dropdown.
4. Pair `ph` channel `oC` to `pwm1`, and confirm the server log shows
   channel index 1, not 0. This is the `ChannelIndex` property working.

- [ ] **Step 5: Commit**

```bash
git add reactors_czlab/gui/
git commit -m "feat: pair and unpair actuators from the dashboard"
```

---

## Task 16: Documentation

**Files:**
- Modify: `README.md`, `CLAUDE.md`

- [ ] **Step 1: Update the README**

Add a `## GUI` section covering:

- Install: `uv sync --extra gui` (PC or Pi), plus `--extra client` for
  recording, experiments and plot history.
- Run: `uv run reactors-gui --endpoint opc.tcp://<pi>:55488/`, then open
  `http://<host>:8080`.
- PostgreSQL on the Pi: install `postgresql`, run `Bioreactor.sql`, or set
  `BIOREACTOR_DB_HOST` to point at the PC instead. State that recording from
  two machines at once produces two divergent copies.
- Existing databases: apply `sql/migrations/2026-07-30-experiments.sql`.
- Remove `GUI` from the "Non essential" To do list.

- [ ] **Step 2: Update CLAUDE.md**

- Add `gui/` to the Layout tree with a one-line description per module.
- Add to the dependency direction line: `gui` may import `opcua` and `sql`;
  nothing imports `gui`; `core` never imports `gui`.
- Add a "GUI" subsection under "Model you need to hold" recording the two
  non-obvious rules: **the control-config write order is
  parameters -> output_unit -> method**, because the server rebuilds the whole
  config on every notification; and **a new three-part-browse-name variable
  under `R{n}:sensors` or `R{n}:actuators` gets archived**, so `pairings` is
  on the reactor node and `ChannelIndex` is a property.
- Update the "Open items" list: drop the `requires-python` item (fixed), note
  that the `experiments` table is now written to, and note that the Hamilton
  calibration Modbus path is verified only by
  `scripts/hamilton_read_calibration.py` on hardware.

- [ ] **Step 3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: GUI install, run and the invariants worth knowing"
```

---

## Self-review notes

**Spec coverage.** Every section of the spec maps to a task: E1 -> Tasks 1-3;
E2 -> Tasks 6-7; E3 -> Tasks 6, 8; E4 -> Tasks 4-5; AddressBook -> Task 9;
dashboard -> Task 13; config dialog -> Tasks 11, 14; pairing panel -> Task 15;
packaging and the `gui` extra -> Task 12; testing strategy -> distributed
across the task test steps; documentation -> Task 16. Phases 2-4 are
deliberately out of scope and get their own specs.

**Deliberately untested, and why.** Task 2 (`core/sensor.py`) cannot be
imported by `tests/`; its testable half was extracted to `core/hamilton.py` in
Task 1 and the rest is verified by `scripts/hamilton_read_calibration.py` on
hardware. Task 12's `AppState` and Tasks 13-15's pages are assembly over
tested pure functions, verified by the required manual checks.
`_PAIRINGS_NODES` in Task 15 is process-global and would need clearing if
`AppState.connect()` ever reconnected to a *different* endpoint; it cannot
today, so it does not, but a later phase adding endpoint switching must. Every SQL
function in Task 7 is tested for its guard and its statement text, not against
a live database.

**Known gap carried forward.** The Hamilton numbers this GUI renders cannot be
trusted until the Modbus word order is checked on hardware (CLAUDE.md,
"Modbus byte order - UNVERIFIED"). Nothing in this plan resolves that; Task 2's
bench script is what makes it checkable.
