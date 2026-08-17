# Pump Calibration and Flow / Volume Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Calibrate DC peristaltic pumps on the Pi, then let the existing PID and
on-boundaries controllers command them in mL/min or mL instead of raw PLC counts.

**Architecture:** `ControlMethod` and every control strategy stay exactly as they
are. A controller keeps answering *what should I demand?*; a new `Dispenser`,
owned by the `Actuator` next to the channel's calibration, answers *how do I
deliver that?* — converting a demand in mL/min to a duty value, or turning a
demand in mL into a timed bolus ended by the existing 20 Hz loop.

**Tech Stack:** Python 3.11+, stdlib only for the new modules (`json`,
`pathlib`, `dataclasses`, `time.perf_counter`), asyncua for the OPC surface,
pytest + pytest-asyncio for tests.

**Spec:** `docs/superpowers/specs/2026-07-27-pump-flow-volume-control-design.md`

## Global Constraints

- **Python >= 3.11.** `asyncio.TaskGroup` and `enum.StrEnum` are used.
- **The new modules `core/calibration.py` and `core/dispenser.py` are standard
  library only.** No numpy, no pymodbus. The Pi has no psycopg and the PC has no
  pymodbus, so both extras must stay independent.
- **`core/data.py` imports nothing.** Do not add an import to it.
- **`sql/` must not import `core.sensor` or `core.modbus`.** Nothing in this plan
  touches `sql/`.
- **`reactors_czlab/__init__.py` and `core/__init__.py` stay docstring-only.**
- **No test may import `reactors_czlab.core.sensor`** — it pulls in pymodbus,
  which is not installed for the test suite.
- **Units, fixed:** duty is raw PLC counts `0 .. MAX_OUTPUT` (4095); flow is
  mL/min; volume is mL. The calibration line is `flow = a * duty + b`.
- **Logging is lazy `%`-style**: `_logger.debug("In %s - %s", self.id, msg)`.
  Never f-strings inside a logging call — these loops run at 20 Hz on a Pi.
- **Failed device reads and rejected configs log at `warning`**, not `debug`, so
  they appear in `record.log` (INFO level).
- **Error style:** assign `error_message = ...` then `raise X(error_message)`.
- **Docstrings** are numpydoc-style on every public function, with a `Raises`
  section wherever a caller's correctness depends on the exception.
- **ruff:** `line-length = 79`, `target-version = "py311"`. Run
  `uv run ruff check .` and `uv run ruff format --check .` before every commit.
- **Do not add `__eq__` comparing an object to a bare id string.**
- Full test command: `uv sync --extra dev && uv run pytest`

---

## File Structure

| File | Responsibility |
| --- | --- |
| `reactors_czlab/core/data.py` | *modify* — add `OutputUnit`, extend `Calibration`, add `ControlConfig.output_unit` |
| `reactors_czlab/core/calibration.py` | *create* — fit, save, load, and the `CalibrationRun` state machine |
| `reactors_czlab/core/dispenser.py` | *create* — demand → duty, bolus timing, volume totals |
| `reactors_czlab/core/control.py` | *modify* — `create_control` accepts demand limits |
| `reactors_czlab/core/actuator.py` | *modify* — own a `Dispenser`, add `tick()`, `calibrating`, `ERROR_VALUE` skip |
| `reactors_czlab/core/reactor.py` | *modify* — `unpaired_loop` → `actuator_loop`, stamp `control_period`, reset boluses in `stop()` |
| `reactors_czlab/opcua/actuator.py` | *modify* — `output_unit` variable, published totals, calibration node and methods |
| `reactors_czlab/run_server.py` | *modify* — load calibrations at startup, renamed loop |
| `reactors_czlab/server_info.py` | *modify* — give every pump channel a unique `Calibration` |
| `tests/conftest.py` | *modify* — `FakeClock` fixture, calibrated-actuator factory |
| `tests/test_data.py` | *create* |
| `tests/test_calibration.py` | *create* |
| `tests/test_dispenser.py` | *create* |
| `tests/test_control.py` | *modify* |
| `tests/test_opcua_actuator.py` | *create* |
| `tests/test_actuator.py` | *modify* |
| `tests/test_reactor.py` | *modify* |
| `tests/test_opcua_calibration.py` | *create* |
| `CLAUDE.md`, `README.md` | *modify* |

---

### Task 1: Data model — `OutputUnit` and the extended `Calibration`

**Files:**
- Modify: `reactors_czlab/core/data.py:52-108`
- Test: `tests/test_data.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `OutputUnit(StrEnum)` with members `duty`, `flow`, `volume`.
  - `Calibration(file: str, a: float = 1.0, b: float = 0.0, min_duty: float = 0.0,
    max_duty: float = MAX_OUTPUT, dispense_duty: float = MAX_OUTPUT,
    points: list[tuple[float, float]] = [], fitted_at: str = "", r2: float = 0.0)`
    with `is_fitted: bool` property, `flow_at(duty: float) -> float` and
    `duty_for(flow: float) -> float`.
  - `ControlConfig.output_unit: OutputUnit = OutputUnit.duty`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_data.py`:

```python
"""Tests for the shared dataclasses."""

from __future__ import annotations

from reactors_czlab.core.data import (
    MAX_OUTPUT,
    Calibration,
    ControlConfig,
    ControlMethod,
    OutputUnit,
)


def test_a_fresh_calibration_is_unfitted() -> None:
    """A calibration with no fit timestamp must not be trusted."""
    cal = Calibration("pump_0")

    assert cal.is_fitted is False
    assert cal.points == []
    assert cal.max_duty == MAX_OUTPUT
    assert cal.dispense_duty == MAX_OUTPUT


def test_calibration_points_are_not_shared_between_instances() -> None:
    """points must be a per-instance list, not a class-level default."""
    first = Calibration("pump_0")
    second = Calibration("pump_1")

    first.points.append((100.0, 1.0))

    assert second.points == []


def test_the_line_converts_both_ways() -> None:
    """flow_at and duty_for are inverses of flow = a * duty + b."""
    cal = Calibration("pump_0", a=0.01, b=-2.0)

    assert cal.flow_at(1000.0) == 8.0
    assert cal.duty_for(8.0) == 1000.0


def test_a_fitted_calibration_reports_itself_fitted() -> None:
    """A non-empty fitted_at is what makes a calibration usable."""
    cal = Calibration("pump_0", fitted_at="2026-07-27T10:00:00+00:00")

    assert cal.is_fitted is True


def test_control_config_defaults_to_duty() -> None:
    """Existing callers get today's behaviour with no change."""
    config = ControlConfig(ControlMethod.manual, value=150)

    assert config.output_unit is OutputUnit.duty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_data.py -v`
Expected: FAIL — `ImportError: cannot import name 'OutputUnit'`

- [ ] **Step 3: Extend `core/data.py`**

Add `field` to the existing dataclasses import (`from dataclasses import dataclass, field`) — this is stdlib, so the no-imports rule is not broken. Replace the `Calibration` class and add `OutputUnit`:

```python
@dataclass
class Calibration:
    """Linear calibration of a pump: ``flow = a * duty + b``.

    Flow is mL/min and duty is raw PLC counts. ``fitted_at`` empty means the
    calibration has never been fitted and must not be used to convert.

    Parameters
    ----------
    file:
        File stem the calibration is stored under, e.g. ``R0_pwm0``.
    a, b:
        Slope and intercept of the fitted line.
    min_duty:
        Stall floor. Below this the pump does not turn.
    max_duty:
        Highest duty the pump may be driven at.
    dispense_duty:
        Duty used for volume boluses.
    points:
        Measured ``(duty, flow)`` pairs the fit was built from.
    fitted_at:
        ISO timestamp of the fit, empty when unfitted.
    r2:
        Fit quality. Informational: it is trivially 1.0 for two points.

    """

    file: str
    a: float = 1.0
    b: float = 0.0
    min_duty: float = 0.0
    max_duty: float = MAX_OUTPUT
    dispense_duty: float = MAX_OUTPUT
    points: list[tuple[float, float]] = field(default_factory=list)
    fitted_at: str = ""
    r2: float = 0.0

    @property
    def is_fitted(self) -> bool:
        """Whether the calibration has ever been fitted."""
        return bool(self.fitted_at)

    def flow_at(self, duty: float) -> float:
        """Flow in mL/min produced at ``duty`` counts."""
        return self.a * duty + self.b

    def duty_for(self, flow: float) -> float:
        """Duty counts needed for ``flow`` mL/min.

        Raises
        ------
        ZeroDivisionError
            If the slope is zero. Loading and fitting both reject a
            non-positive slope, so this only happens on a hand-edited object.

        """
        return (flow - self.b) / self.a


class OutputUnit(StrEnum):
    """Unit a controller's demand is expressed in.

    Parameters
    ----------
    duty, flow, volume

    """

    duty = auto()
    flow = auto()
    volume = auto()
```

Then add the field to `ControlConfig`, at the end so existing positional callers are unaffected, and document it in the class docstring's parameter list:

```python
    output_unit: OutputUnit = OutputUnit.duty
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_data.py -v && uv run pytest`
Expected: PASS, and the whole existing suite still passes.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add reactors_czlab/core/data.py tests/test_data.py
git commit -m "feat: add OutputUnit and extend Calibration with the pump line"
```

---

### Task 2: Calibration fit and persistence

**Files:**
- Create: `reactors_czlab/core/calibration.py`
- Test: `tests/test_calibration.py` (create)

**Interfaces:**
- Consumes: `Calibration`, `Channel` from Task 1.
- Produces:
  - `CALIBRATION_ENV = "REACTORS_CALIBRATION_DIR"`
  - `MIN_POINTS = 2`
  - `calibration_dir() -> Path`
  - `calibration_path(name: str) -> Path`
  - `fit_line(points: list[tuple[float, float]]) -> tuple[float, float, float]`
    returning `(a, b, r2)`, raising `ValueError`.
  - `save_calibration(cal: Calibration) -> None`
  - `load_calibration(name: str) -> Calibration | None`
  - `load_into(channel: Channel) -> bool`

- [ ] **Step 1: Write the failing test**

Create `tests/test_calibration.py`:

```python
"""Tests for fitting, saving and loading pump calibrations."""

from __future__ import annotations

import json

import pytest

from reactors_czlab.core.calibration import (
    CALIBRATION_ENV,
    calibration_path,
    fit_line,
    load_calibration,
    load_into,
    save_calibration,
)
from reactors_czlab.core.data import Calibration, Channel


@pytest.fixture(autouse=True)
def _cal_dir(tmp_path, monkeypatch) -> None:
    """Keep every test out of the operator's real calibration directory."""
    monkeypatch.setenv(CALIBRATION_ENV, str(tmp_path))


def test_fit_recovers_a_known_line() -> None:
    """Points taken from flow = 0.01 * duty - 2 fit back to it."""
    points = [(500.0, 3.0), (1500.0, 13.0), (2500.0, 23.0)]

    a, b, r2 = fit_line(points)

    assert a == pytest.approx(0.01)
    assert b == pytest.approx(-2.0)
    assert r2 == pytest.approx(1.0)


def test_fit_rejects_too_few_distinct_duties() -> None:
    """Two measurements at the same duty do not define a line."""
    with pytest.raises(ValueError, match="distinct"):
        fit_line([(1000.0, 5.0), (1000.0, 5.2)])


def test_fit_rejects_a_non_positive_slope() -> None:
    """More duty must mean more flow, or the pump is wired backwards."""
    with pytest.raises(ValueError, match="slope"):
        fit_line([(500.0, 20.0), (2500.0, 4.0)])


def test_save_then_load_round_trips() -> None:
    """A saved calibration comes back with its points as tuples."""
    cal = Calibration(
        "R0_pwm0",
        a=0.01,
        b=-2.0,
        min_duty=400.0,
        max_duty=4000.0,
        dispense_duty=2000.0,
        points=[(500.0, 3.0), (2500.0, 23.0)],
        fitted_at="2026-07-27T10:00:00+00:00",
        r2=1.0,
    )

    save_calibration(cal)
    loaded = load_calibration("R0_pwm0")

    assert loaded == cal
    assert loaded.points == [(500.0, 3.0), (2500.0, 23.0)]


def test_load_returns_none_when_there_is_no_file() -> None:
    """A pump that has never been calibrated is not an error."""
    assert load_calibration("R0_pwm0") is None


def test_load_survives_a_corrupt_file() -> None:
    """A truncated file must not take the server down."""
    calibration_path("R0_pwm0").write_text("{not json", encoding="utf-8")

    assert load_calibration("R0_pwm0") is None


def test_load_rejects_a_non_positive_slope_on_disk() -> None:
    """A hand-edited file cannot install a line that cannot be inverted."""
    calibration_path("R0_pwm0").write_text(
        json.dumps({"file": "R0_pwm0", "a": 0.0, "b": 1.0}),
        encoding="utf-8",
    )

    assert load_calibration("R0_pwm0") is None


def test_load_into_installs_the_stored_calibration() -> None:
    """A channel picks up what was saved under its calibration name."""
    save_calibration(
        Calibration("R0_pwm0", a=0.01, fitted_at="2026-07-27T10:00:00+00:00"),
    )
    channel = Channel("pwm0", "pwm", calibration=Calibration("R0_pwm0"))

    assert load_into(channel) is True
    assert channel.calibration.is_fitted is True
    assert channel.calibration.a == 0.01


def test_load_into_keeps_the_unfitted_calibration_when_absent() -> None:
    """With no stored file the channel keeps its placeholder calibration."""
    channel = Channel("pwm0", "pwm", calibration=Calibration("R0_pwm0"))

    assert load_into(channel) is False
    assert channel.calibration.is_fitted is False


def test_load_into_ignores_a_channel_with_no_calibration() -> None:
    """Channels that are not pumps are skipped, not crashed on."""
    assert load_into(Channel("pwm1", "pwm")) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_calibration.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reactors_czlab.core.calibration'`

- [ ] **Step 3: Write `core/calibration.py`**

```python
"""Fit, store and reload the linear calibration of a pump.

Standard library only: this module runs on the Pi, which carries neither
numpy nor psycopg. The fit is an ordinary least squares of ``flow`` on
``duty`` and needs no more than that.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from reactors_czlab.core.data import Calibration

if TYPE_CHECKING:
    from reactors_czlab.core.data import Channel

_logger = logging.getLogger("server.calibration")

#: Environment variable overriding where calibrations are stored.
CALIBRATION_ENV = "REACTORS_CALIBRATION_DIR"

#: Fewest distinct duty points a fit will accept.
MIN_POINTS = 2


def calibration_dir() -> Path:
    """Directory holding the calibration files, created if missing."""
    override = os.environ.get(CALIBRATION_ENV)
    path = (
        Path(override)
        if override
        else Path.home() / ".reactors_czlab" / "calibrations"
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def calibration_path(name: str) -> Path:
    """Path of the calibration file for ``name``."""
    return calibration_dir() / f"{name}.json"


def fit_line(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Fit ``flow = a * duty + b`` by ordinary least squares.

    Parameters
    ----------
    points:
        Measured ``(duty, flow)`` pairs.

    Returns
    -------
    tuple
        ``(a, b, r2)``.

    Raises
    ------
    ValueError
        If fewer than ``MIN_POINTS`` distinct duty values were measured, or
        if the fitted slope is not positive - a pump that delivers less at a
        higher duty is wired backwards or was measured wrongly, and its line
        cannot be safely inverted.

    """
    if len({duty for duty, _ in points}) < MIN_POINTS:
        error_message = (
            f"need at least {MIN_POINTS} distinct duty points, got "
            f"{len(points)} measurements"
        )
        raise ValueError(error_message)

    n = len(points)
    mean_x = sum(duty for duty, _ in points) / n
    mean_y = sum(flow for _, flow in points) / n
    sxx = sum((duty - mean_x) ** 2 for duty, _ in points)
    sxy = sum((duty - mean_x) * (flow - mean_y) for duty, flow in points)

    a = sxy / sxx
    if a <= 0:
        error_message = (
            f"fitted slope {a:.6g} is not positive; the pump delivers less "
            "at a higher duty"
        )
        raise ValueError(error_message)
    b = mean_y - a * mean_x

    syy = sum((flow - mean_y) ** 2 for _, flow in points)
    r2 = 0.0 if syy == 0 else (sxy**2) / (sxx * syy)

    return a, b, r2


def save_calibration(cal: Calibration) -> None:
    """Write a calibration to its file atomically.

    The temp-file-then-replace dance means a power cut during the write
    leaves either the old calibration or the new one, never a half file.
    """
    path = calibration_path(cal.file)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(cal), indent=2), encoding="utf-8")
    os.replace(tmp, path)
    _logger.info("Saved calibration %s: a=%s b=%s", cal.file, cal.a, cal.b)


def load_calibration(name: str) -> Calibration | None:
    """Read a stored calibration.

    Returns
    -------
    Calibration or None
        ``None`` when the file is absent, unreadable, malformed, or holds a
        line that cannot be inverted. Every one of those is logged and left
        for the operator; none of them may take the server down.

    """
    path = calibration_path(name)
    if not path.exists():
        _logger.warning("No stored calibration for %s at %s", name, path)
        return None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        cal = Calibration(**raw)
        cal.points = [(float(d), float(f)) for d, f in cal.points]
    except (OSError, ValueError, TypeError):
        _logger.exception("Unreadable calibration file %s", path)
        return None

    if cal.a <= 0:
        _logger.warning(
            "Calibration %s has a non-positive slope %s, ignoring",
            name,
            cal.a,
        )
        return None
    return cal


def load_into(channel: Channel) -> bool:
    """Install the stored calibration for ``channel``, if there is one.

    Returns
    -------
    bool
        True when a stored calibration replaced the channel's placeholder.

    """
    if channel.calibration is None:
        return False

    stored = load_calibration(channel.calibration.file)
    if stored is None:
        return False

    channel.calibration = stored
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_calibration.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add reactors_czlab/core/calibration.py tests/test_calibration.py
git commit -m "feat: fit, save and load pump calibrations"
```

---

### Task 3: Dispenser — duty and flow units

**Files:**
- Create: `reactors_czlab/core/dispenser.py`
- Create: `tests/test_dispenser.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: `Calibration`, `Channel`, `OutputUnit`, `MAX_OUTPUT` (Task 1).
- Produces:
  - `DEFAULT_CONTROL_PERIOD = 10.0`
  - `check_unit(unit: OutputUnit, channel: Channel) -> str | None` — `None` when
    the unit is usable on that channel, otherwise the reason.
  - `Dispenser(unit: OutputUnit, channel: Channel, control_period: float = DEFAULT_CONTROL_PERIOD, clock: Callable[[], float] = perf_counter)`
    with attributes `unit`, `channel`, `control_period`, `total_volume`, and
    methods `duty(demand: float) -> float`, `tick() -> float | None`,
    `demand_limits() -> tuple[float, float]`, `reset() -> None`.
  - `FakeClock` and `make_calibrated_actuator` test fixtures.

Volume mode arrives in Task 4; this task builds the object with `duty` and
`flow` working and `volume` raising `NotImplementedError`.

- [ ] **Step 1: Add the shared test fixtures**

Append to `tests/conftest.py` (and add `Calibration` to its existing
`reactors_czlab.core.data` import):

```python
class FakeClock:
    """Monotonic clock the tests drive by hand.

    Bolus timing is measured in seconds; sleeping through it would make the
    suite slow and flaky, so the dispenser takes its clock as a parameter.
    """

    def __init__(self) -> None:
        """Start at zero."""
        self.now = 0.0

    def __call__(self) -> float:
        """Read the clock, matching the perf_counter signature."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        self.now += seconds


def _build_calibration(name: str = "R0_pwm0") -> Calibration:
    """A fitted pump line with round numbers.

    flow = 0.01 * duty, so the dispense duty of 2000 gives 20 mL/min and a
    1 mL bolus takes exactly 3 s.
    """
    return Calibration(
        name,
        a=0.01,
        b=0.0,
        min_duty=400.0,
        max_duty=4000.0,
        dispense_duty=2000.0,
        points=[(500.0, 5.0), (2500.0, 25.0)],
        fitted_at="2026-07-27T10:00:00+00:00",
        r2=1.0,
    )


def _build_calibrated_actuator(
    identifier: str = "R0:pwm0",
    *,
    fitted: bool = True,
) -> RandomActuator:
    """An actuator whose channel carries a pump calibration."""
    calibration = _build_calibration() if fitted else Calibration("R0_pwm0")
    info = PhysicalInfo(
        model="pwm",
        address=0,
        type=PlcOutput.pwm,
        channels=[
            Channel("pwm0", "pwm", pin="Q2.7", calibration=calibration),
        ],
    )
    return RandomActuator(identifier, info)


@pytest.fixture
def clock() -> FakeClock:
    """A hand-driven clock."""
    return FakeClock()


@pytest.fixture
def calibration() -> Calibration:
    """A fitted pump calibration."""
    return _build_calibration()


@pytest.fixture
def make_calibrated_actuator() -> Callable[..., RandomActuator]:
    """Factory for actuators with a calibrated pump channel."""
    return _build_calibrated_actuator
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_dispenser.py`:

```python
"""Tests for the demand-to-duty dispenser."""

from __future__ import annotations

import pytest

from reactors_czlab.core.data import MAX_OUTPUT, Calibration, Channel, OutputUnit
from reactors_czlab.core.dispenser import Dispenser, check_unit


@pytest.fixture
def channel(calibration: Calibration) -> Channel:
    """A pump channel with a fitted calibration."""
    return Channel("pwm0", "pwm", pin="Q2.7", calibration=calibration)


def test_duty_unit_is_a_passthrough(channel: Channel, clock) -> None:
    """The default unit must behave exactly as the code did before."""
    disp = Dispenser(OutputUnit.duty, channel, clock=clock)

    assert disp.duty(1234.0) == 1234.0
    assert disp.tick() is None


def test_flow_inverts_the_calibration(channel: Channel, clock) -> None:
    """20 mL/min on a 0.01 mL/min-per-count pump is 2000 counts."""
    disp = Dispenser(OutputUnit.flow, channel, clock=clock)

    assert disp.duty(20.0) == pytest.approx(2000.0)


def test_flow_is_a_level_not_an_event(channel: Channel, clock) -> None:
    """Flow mode never asks for a duty change from the fast loop."""
    disp = Dispenser(OutputUnit.flow, channel, clock=clock)
    disp.duty(20.0)

    clock.advance(60.0)

    assert disp.tick() is None


def test_zero_flow_turns_the_pump_off(channel: Channel, clock) -> None:
    """Off must be 0, not the stall floor."""
    disp = Dispenser(OutputUnit.flow, channel, clock=clock)

    assert disp.duty(0.0) == 0.0
    assert disp.duty(-5.0) == 0.0


def test_flow_below_the_stall_floor_is_raised(channel: Channel, clock) -> None:
    """A pump cannot turn slower than min_duty, so it over-delivers."""
    disp = Dispenser(OutputUnit.flow, channel, clock=clock)

    # 1 mL/min would be 100 counts, below the 400 count stall floor.
    assert disp.duty(1.0) == 400.0


def test_flow_is_capped_at_max_duty(channel: Channel, clock) -> None:
    """A demand beyond the pump's range saturates."""
    disp = Dispenser(OutputUnit.flow, channel, clock=clock)

    assert disp.duty(999.0) == 4000.0


def test_flow_accumulates_delivered_volume(channel: Channel, clock) -> None:
    """Running at 20 mL/min for 3 s delivers 1 mL."""
    disp = Dispenser(OutputUnit.flow, channel, clock=clock)
    disp.duty(20.0)

    clock.advance(3.0)
    disp.tick()

    assert disp.total_volume == pytest.approx(1.0)


def test_nothing_accumulates_while_the_pump_is_off(
    channel: Channel,
    clock,
) -> None:
    """An idle pump must not invent delivered volume."""
    disp = Dispenser(OutputUnit.flow, channel, clock=clock)
    disp.duty(0.0)

    clock.advance(600.0)
    disp.tick()

    assert disp.total_volume == 0.0


def test_demand_limits_per_unit(channel: Channel, clock) -> None:
    """Limits are handed to the controller in the config's own unit."""
    duty = Dispenser(OutputUnit.duty, channel, clock=clock)
    flow = Dispenser(OutputUnit.flow, channel, clock=clock)

    assert duty.demand_limits() == (0.0, MAX_OUTPUT)
    assert flow.demand_limits() == (0.0, 40.0)


def test_check_unit_allows_duty_without_a_calibration() -> None:
    """Raw duty control never needed a calibration and still does not."""
    assert check_unit(OutputUnit.duty, Channel("pwm1", "pwm")) is None


def test_check_unit_rejects_flow_without_a_calibration() -> None:
    """mL/min is meaningless on an uncalibrated pump."""
    reason = check_unit(OutputUnit.flow, Channel("pwm1", "pwm"))

    assert reason is not None
    assert "calibration" in reason


def test_check_unit_rejects_flow_on_an_unfitted_calibration() -> None:
    """A placeholder calibration is not a calibration."""
    channel = Channel("pwm0", "pwm", calibration=Calibration("R0_pwm0"))

    assert check_unit(OutputUnit.flow, channel) is not None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_dispenser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reactors_czlab.core.dispenser'`

- [ ] **Step 4: Write `core/dispenser.py`**

```python
"""Turn a controller's demand into a duty value for a pump.

A controller answers *what should I demand?*; this module answers *how do I
deliver that?*. Keeping the two apart is what lets the existing PID and
on-boundaries strategies command a pump in mL/min or mL without knowing that
pumps or calibrations exist.

Standard library only - this runs on the Pi.
"""

from __future__ import annotations

import logging
from time import perf_counter
from typing import TYPE_CHECKING, Callable

from reactors_czlab.core.data import MAX_OUTPUT, OutputUnit

if TYPE_CHECKING:
    from reactors_czlab.core.data import Channel

_logger = logging.getLogger("server.dispenser")

#: Fallback decision period for an actuator that no Reactor owns - in tests
#: or on the bench. Deliberately non-zero: a zero period would disable the
#: volume-mode re-trigger guard entirely.
DEFAULT_CONTROL_PERIOD = 10.0

#: Seconds in a minute. Flow is mL/min, every clock here is seconds.
_SECONDS_PER_MINUTE = 60.0


def check_unit(unit: OutputUnit, channel: Channel) -> str | None:
    """Check whether ``unit`` can be used on ``channel``.

    Parameters
    ----------
    unit:
        The output unit a new control config asks for.
    channel:
        The actuator channel the config would drive.

    Returns
    -------
    str or None
        ``None`` when the unit is usable, otherwise why it is not. Callers
        reject the config rather than silently treating mL/min as raw
        counts, which would peg a pump.

    """
    if unit is OutputUnit.duty:
        return None

    cal = channel.calibration
    if cal is None or not cal.is_fitted:
        return f"{unit} needs a fitted calibration on the channel"
    return None


class Dispenser:
    """Convert a demand into a duty value and account for what was pumped."""

    def __init__(
        self,
        unit: OutputUnit,
        channel: Channel,
        control_period: float = DEFAULT_CONTROL_PERIOD,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        """Build a dispenser for one actuator channel.

        Parameters
        ----------
        unit:
            Unit the controller's demand is expressed in.
        channel:
            The actuator channel. The calibration is read through it on every
            call, so a refit is picked up with no rewiring.
        control_period:
            Seconds between control decisions.
        clock:
            Monotonic clock, injectable so bolus timing is testable.

        """
        self.unit = unit
        self.channel = channel
        self.control_period = control_period
        self.total_volume = 0.0

        self._clock = clock
        self._current_duty = 0.0
        self._since = clock()

    def __repr__(self) -> str:
        """Print the unit and how much has been delivered."""
        return f"Dispenser({self.unit}, {self.total_volume:.3f} mL)"

    def duty(self, demand: float) -> float:
        """Duty counts realising ``demand``, and the new pump state."""
        now = self._clock()
        if self.unit is OutputUnit.duty:
            return self._apply(demand, now)
        if self.unit is OutputUnit.flow:
            return self._apply(self._duty_for_flow(demand), now)
        error_message = f"{self.unit} is not implemented yet"
        raise NotImplementedError(error_message)

    def tick(self) -> float | None:
        """Advance the delivery of a demand already accepted.

        Returns
        -------
        float or None
            A duty value to write, or ``None`` when nothing changes. Volume
            accrual happens either way.

        """
        self._accrue(self._clock())
        return None

    def demand_limits(self) -> tuple[float, float]:
        """Range a controller may demand, in this dispenser's unit."""
        if self.unit is OutputUnit.duty:
            return (0.0, MAX_OUTPUT)
        cal = self.channel.calibration
        return (0.0, cal.flow_at(cal.max_duty))

    def reset(self) -> None:
        """Forget any delivery in flight. Totals are kept."""
        self._accrue(self._clock())
        self._current_duty = 0.0

    def _duty_for_flow(self, demand: float) -> float:
        """Invert the calibration line, respecting the pump's usable band."""
        if demand <= 0:
            return 0.0
        cal = self.channel.calibration
        duty = cal.duty_for(demand)
        if duty < cal.min_duty:
            _logger.debug(
                "Demand %s mL/min is below the stall floor of %s counts",
                demand,
                cal.min_duty,
            )
            duty = cal.min_duty
        return min(duty, cal.max_duty)

    def _apply(self, value: float, now: float) -> float:
        """Account for the duty that was running, then take the new one."""
        self._accrue(now)
        self._current_duty = value
        return value

    def _accrue(self, now: float) -> None:
        """Add what the pump delivered since the last accounting point.

        Integrating the *actual* duty over the *actual* elapsed time, rather
        than summing demanded volumes, keeps the total right when a delivery
        is superseded, clipped or interrupted.
        """
        cal = self.channel.calibration
        if cal is not None and cal.is_fitted and self._current_duty > 0:
            elapsed = (now - self._since) / _SECONDS_PER_MINUTE
            self.total_volume += cal.flow_at(self._current_duty) * elapsed
        self._since = now
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_dispenser.py -v`
Expected: PASS (12 tests)

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add reactors_czlab/core/dispenser.py tests/test_dispenser.py tests/conftest.py
git commit -m "feat: add Dispenser with duty passthrough and flow inversion"
```

---

### Task 4: Dispenser — volume boluses and the re-trigger guard

**Files:**
- Modify: `reactors_czlab/core/dispenser.py`
- Modify: `tests/test_dispenser.py`

**Interfaces:**
- Consumes: everything from Task 3.
- Produces: `OutputUnit.volume` support in `Dispenser.duty()` and
  `Dispenser.tick()`; `check_unit` additionally rejects a dispense duty that
  produces no flow; `demand_limits()` returns the per-period deliverable volume.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dispenser.py`:

```python
def test_a_volume_demand_starts_a_bolus(channel: Channel, clock) -> None:
    """1 mL at the 2000 count dispense duty runs the pump at that duty."""
    disp = Dispenser(OutputUnit.volume, channel, clock=clock)

    assert disp.duty(1.0) == 2000.0


def test_the_bolus_ends_on_time(channel: Channel, clock) -> None:
    """2000 counts is 20 mL/min, so 1 mL is exactly 3 s of running."""
    disp = Dispenser(OutputUnit.volume, channel, clock=clock)
    disp.duty(1.0)

    clock.advance(2.9)
    assert disp.tick() is None

    clock.advance(0.2)
    assert disp.tick() == 0.0


def test_the_bolus_ends_only_once(channel: Channel, clock) -> None:
    """After it has stopped the pump, the fast loop has nothing to say."""
    disp = Dispenser(OutputUnit.volume, channel, clock=clock)
    disp.duty(1.0)
    clock.advance(3.1)
    disp.tick()

    clock.advance(1.0)

    assert disp.tick() is None


def test_a_repeated_demand_is_ignored_within_the_control_period(
    channel: Channel,
    clock,
) -> None:
    """The re-trigger guard.

    Regression: write_output() is called every 50 ms by the loop that drives
    unpaired actuators. Without this guard a standing 2 mL demand would be
    dispensed forty times a second.
    """
    disp = Dispenser(
        OutputUnit.volume,
        channel,
        control_period=10.0,
        clock=clock,
    )
    assert disp.duty(1.0) == 2000.0

    clock.advance(3.1)
    disp.tick()  # bolus finished, pump off

    for _ in range(20):
        clock.advance(0.05)
        assert disp.duty(1.0) == 0.0  # guard holds it off


def test_a_new_decision_is_accepted_after_the_control_period(
    channel: Channel,
    clock,
) -> None:
    """on_boundaries must be able to dose again on the next cycle."""
    disp = Dispenser(
        OutputUnit.volume,
        channel,
        control_period=10.0,
        clock=clock,
    )
    disp.duty(1.0)
    clock.advance(10.1)

    assert disp.duty(1.0) == 2000.0


def test_a_new_decision_supersedes_a_bolus_in_flight(
    channel: Channel,
    clock,
) -> None:
    """A longer dose re-arms the deadline rather than queueing."""
    disp = Dispenser(
        OutputUnit.volume,
        channel,
        control_period=1.0,
        clock=clock,
    )
    disp.duty(1.0)  # 3 s of running

    clock.advance(2.0)
    assert disp.duty(1.0) == 2000.0  # re-armed for another 3 s

    clock.advance(2.0)
    assert disp.tick() is None  # the original deadline no longer applies

    clock.advance(1.1)
    assert disp.tick() == 0.0


def test_a_zero_volume_demand_stops_the_pump(channel: Channel, clock) -> None:
    """Back inside the band, on_boundaries demands nothing."""
    disp = Dispenser(
        OutputUnit.volume,
        channel,
        control_period=1.0,
        clock=clock,
    )
    disp.duty(1.0)
    clock.advance(1.1)

    assert disp.duty(0.0) == 0.0


def test_volume_totals_survive_a_superseded_bolus(
    channel: Channel,
    clock,
) -> None:
    """Totals come from actual runtime, not from demanded volumes."""
    disp = Dispenser(
        OutputUnit.volume,
        channel,
        control_period=1.0,
        clock=clock,
    )
    disp.duty(1.0)
    clock.advance(1.5)
    disp.duty(1.0)  # superseded after only 1.5 s of the first 3 s
    clock.advance(3.0)
    disp.tick()

    # 4.5 s at 20 mL/min = 1.5 mL, not the 2 mL demanded.
    assert disp.total_volume == pytest.approx(1.5)


def test_volume_demand_limits_use_the_control_period(
    channel: Channel,
    clock,
) -> None:
    """A PID cannot usefully ask for more than one period can deliver."""
    disp = Dispenser(
        OutputUnit.volume,
        channel,
        control_period=10.0,
        clock=clock,
    )

    # 20 mL/min for 10 s = 3.3333 mL.
    assert disp.demand_limits()[1] == pytest.approx(20.0 * 10.0 / 60.0)


def test_reset_cancels_a_bolus(channel: Channel, clock) -> None:
    """Reactor.stop() must not leave a dose to resume."""
    disp = Dispenser(
        OutputUnit.volume,
        channel,
        control_period=1.0,
        clock=clock,
    )
    disp.duty(1.0)

    disp.reset()
    clock.advance(10.0)

    assert disp.tick() is None


def test_check_unit_rejects_a_dispense_duty_that_does_not_pump() -> None:
    """A bolus needs a positive flow at the dispense duty or it never ends."""
    dead = Calibration(
        "R0_pwm0",
        a=0.01,
        b=-50.0,
        dispense_duty=1000.0,
        fitted_at="2026-07-27T10:00:00+00:00",
    )
    channel = Channel("pwm0", "pwm", calibration=dead)

    reason = check_unit(OutputUnit.volume, channel)

    assert reason is not None
    assert "dispense" in reason
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dispenser.py -v`
Expected: FAIL — `NotImplementedError: OutputUnit.volume is not implemented yet`

- [ ] **Step 3: Add volume support to `core/dispenser.py`**

Extend `check_unit`, replacing its `return None` tail:

```python
    if unit is OutputUnit.volume and cal.flow_at(cal.dispense_duty) <= 0:
        return (
            f"dispense duty {cal.dispense_duty} produces no flow, so a "
            "bolus would never finish"
        )
    return None
```

In `__init__`, add the bolus state. `_last_decision` starts at negative
infinity so the very first demand is always accepted:

```python
        self._bolus_until: float | None = None
        self._last_decision = float("-inf")
```

Replace the `NotImplementedError` tail of `duty()`:

```python
        return self._start_bolus(demand, now)
```

Add the two new methods:

```python
    def _start_bolus(self, demand: float, now: float) -> float:
        """Accept a volume demand, unless the guard is still holding.

        A bolus is an event, but ``write_output()`` is called every
        ``UNPAIRED_PERIOD`` for an unpaired actuator. Rate-limiting decisions
        to ``control_period`` is what stops a standing manual demand from
        being dispensed twenty times a second, and it makes paired and
        unpaired actuators behave identically.
        """
        if now - self._last_decision < self.control_period:
            return self._current_duty
        self._last_decision = now

        if demand <= 0:
            self._bolus_until = None
            return self._apply(0.0, now)

        cal = self.channel.calibration
        seconds = _SECONDS_PER_MINUTE * demand / cal.flow_at(cal.dispense_duty)
        self._bolus_until = now + seconds
        _logger.debug("Dispensing %s mL over %.3fs", demand, seconds)
        return self._apply(cal.dispense_duty, now)
```

Replace `tick()`:

```python
    def tick(self) -> float | None:
        """Advance the delivery of a demand already accepted.

        Returns
        -------
        float or None
            A duty value to write, or ``None`` when nothing changes. Volume
            accrual happens either way.

        """
        now = self._clock()
        if self._bolus_until is None or now < self._bolus_until:
            self._accrue(now)
            return None
        self._bolus_until = None
        return self._apply(0.0, now)
```

Extend `demand_limits()`, replacing its `return` tail:

```python
        if self.unit is OutputUnit.flow:
            return (0.0, cal.flow_at(cal.max_duty))
        per_period = (
            cal.flow_at(cal.dispense_duty)
            * self.control_period
            / _SECONDS_PER_MINUTE
        )
        return (0.0, per_period)
```

Extend `reset()`:

```python
    def reset(self) -> None:
        """Forget any delivery in flight. Totals are kept."""
        self._accrue(self._clock())
        self._bolus_until = None
        self._current_duty = 0.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_dispenser.py -v`
Expected: PASS (23 tests)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add reactors_czlab/core/dispenser.py tests/test_dispenser.py
git commit -m "feat: dispense volume as a timed bolus with a re-trigger guard"
```

---

### Task 5: Controllers accept demand limits

**Files:**
- Modify: `reactors_czlab/core/control.py:311-348`
- Modify: `tests/test_control.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  `ControlFactory.create_control(config: ControlConfig, min_val: float = 0.0, max_val: float = MAX_OUTPUT) -> _Control`.
  The defaults reproduce today's behaviour, so every existing caller is
  unaffected.

Why this matters: with the limits in engineering units, `_PidControl` clamps its
output — and defaults its anti-windup band — in mL/min or mL automatically.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_control.py`:

```python
def test_limits_reach_the_controller(factory: ControlFactory) -> None:
    """A dispenser's demand range becomes the controller's clamp range."""
    control = factory.create_control(
        ControlConfig(ControlMethod.pid, setpoint=7.0),
        min_val=0.0,
        max_val=40.0,
    )

    assert control.min_val == 0.0
    assert control.max_val == 40.0
    assert control.clamp(100.0) == 40.0


def test_pid_anti_windup_defaults_to_the_demand_range(
    factory: ControlFactory,
) -> None:
    """The integral band follows the unit, not the raw PWM full scale."""
    control = factory.create_control(
        ControlConfig(ControlMethod.pid, setpoint=7.0),
        min_val=0.0,
        max_val=40.0,
    )

    assert control.max_integral == 40.0


def test_a_limit_change_replaces_the_controller(
    factory: ControlFactory,
) -> None:
    """Limits are configuration, so they take part in equality."""
    config = ControlConfig(ControlMethod.pid, setpoint=7.0)

    narrow = factory.create_control(config, max_val=40.0)
    wide = factory.create_control(config, max_val=4095.0)

    assert narrow != wide
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_control.py -v`
Expected: FAIL — `TypeError: create_control() got an unexpected keyword argument 'min_val'`

- [ ] **Step 3: Thread the limits through `ControlFactory`**

Replace `create_control` in `core/control.py`:

```python
    def create_control(
        self,
        config: ControlConfig,
        min_val: float = 0.0,
        max_val: float = MAX_OUTPUT,
    ) -> _Control:
        """Create a control class based on the control config.

        Parameters
        ----------
        config:
            A dataclass with the parameters of the new configuration
        min_val, max_val:
            Range the controller may demand, in the unit the config asks
            for. The defaults are the raw PLC output range, which is what
            duty-mode configs want.

        Raises
        ------
        TypeError
            If the method is unknown or a parameter is not a number.

        """
        limits = {"min_val": min_val, "max_val": max_val}
        match config.method:
            case ControlMethod.manual:
                return _ManualControl(value=config.value, **limits)

            case ControlMethod.timer:
                return _TimerControl(
                    time_on=config.time_on,
                    time_off=config.time_off,
                    value_on=config.value,
                    **limits,
                )

            case ControlMethod.on_boundaries:
                return _OnBoundariesControl(
                    lower_bound=config.lb,
                    upper_bound=config.ub,
                    value_on=config.value,
                    **limits,
                )

            case ControlMethod.pid:
                return _PidControl(setpoint=config.setpoint, **limits)

            case _:
                error_message = f"Unknown control method: {config.method!r}"
                raise TypeError(error_message)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_control.py -v && uv run pytest`
Expected: PASS — the existing control tests are unaffected by the defaults.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add reactors_czlab/core/control.py tests/test_control.py
git commit -m "feat: let ControlFactory take the demand range for a controller"
```

---

### Task 6: Actuator owns a dispenser

**Files:**
- Modify: `reactors_czlab/core/actuator.py:21-100`
- Modify: `tests/test_actuator.py`

**Interfaces:**
- Consumes: `Dispenser`, `check_unit`, `DEFAULT_CONTROL_PERIOD` (Tasks 3–4);
  `create_control(config, min_val, max_val)` (Task 5).
- Produces on `Actuator`:
  - `control_period: float` — property; the setter also updates the dispenser.
  - `calibrating: bool` — interlock, `False` by default.
  - `dispenser: Dispenser`
  - `tick() -> None`
  - `write_output(sens_value: float) -> None` — unchanged signature.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_actuator.py` (extend its imports with
`from reactors_czlab.core.data import ERROR_VALUE, OutputUnit`):

```python
def test_flow_config_is_rejected_without_a_calibration(
    actuator: RandomActuator,
) -> None:
    """mL/min against an uncalibrated pump must not reach the hardware."""
    actuator.set_control_config(
        ControlConfig(ControlMethod.manual, value=150),
    )
    good = actuator.controller

    actuator.set_control_config(
        ControlConfig(
            ControlMethod.manual,
            value=20,
            output_unit=OutputUnit.flow,
        ),
    )

    assert actuator.controller is good
    assert actuator.dispenser.unit is OutputUnit.duty


def test_flow_config_converts_the_demand(make_calibrated_actuator) -> None:
    """A manual 20 mL/min lands on the pin as 2000 counts."""
    actuator = make_calibrated_actuator()
    actuator.set_control_config(
        ControlConfig(
            ControlMethod.manual,
            value=20,
            output_unit=OutputUnit.flow,
        ),
    )

    actuator.write_output(0)

    assert actuator.channel.value == 2000


def test_total_volume_survives_a_config_change(
    make_calibrated_actuator,
) -> None:
    """The total records the physical pump, not the configuration."""
    actuator = make_calibrated_actuator()
    actuator.dispenser.total_volume = 12.5

    actuator.set_control_config(
        ControlConfig(
            ControlMethod.manual,
            value=20,
            output_unit=OutputUnit.flow,
        ),
    )

    assert actuator.dispenser.total_volume == 12.5


def test_calibrating_blocks_both_paths(make_calibrated_actuator) -> None:
    """A calibration run must not have a controller fighting it."""
    actuator = make_calibrated_actuator()
    actuator.set_control_config(
        ControlConfig(ControlMethod.manual, value=150),
    )
    actuator.calibrating = True

    actuator.write_output(0)
    actuator.tick()

    assert actuator.channel.value == ERROR_VALUE  # never written


def test_a_failed_sensor_read_holds_the_last_output(
    actuator: RandomActuator,
) -> None:
    """ERROR_VALUE is a sentinel, not a measurement.

    Regression: a failed pH probe reads -0.111, which would drive
    _OnBoundariesControl to dose base forever.
    """
    actuator.set_control_config(
        ControlConfig(ControlMethod.manual, value=150),
    )
    actuator.write_output(0)

    actuator.set_control_config(
        ControlConfig(ControlMethod.manual, value=900),
    )
    actuator.write_output(ERROR_VALUE)

    assert actuator.channel.value == 150


def test_control_period_reaches_the_dispenser(
    make_calibrated_actuator,
) -> None:
    """The Reactor stamps its period on; the guard has to see it."""
    actuator = make_calibrated_actuator()

    actuator.control_period = 42.0

    assert actuator.dispenser.control_period == 42.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_actuator.py -v`
Expected: FAIL — `AttributeError: 'RandomActuator' object has no attribute 'dispenser'`

- [ ] **Step 3: Rework `Actuator`**

Add the imports to `core/actuator.py`:

```python
from reactors_czlab.core.data import (
    ERROR_VALUE,
    ControlConfig,
    ControlMethod,
    OutputUnit,
    PlcOutput,
)
from reactors_czlab.core.dispenser import (
    DEFAULT_CONTROL_PERIOD,
    Dispenser,
    check_unit,
)
```

Extend `__init__` after `self.channel` is set:

```python
        #: Set while a calibration run owns the pump. Both the sampling loop
        #: and the fast loop leave the actuator alone while it is set.
        self.calibrating = False
        self._control_period = DEFAULT_CONTROL_PERIOD
        self.dispenser = Dispenser(
            OutputUnit.duty,
            self.channel,
            self._control_period,
        )
        self.controller = ControlFactory().create_control(
            ControlConfig(method=ControlMethod.manual, value=0),
        )
```

Add the property, next to the existing `controller` property:

```python
    @property
    def control_period(self) -> float:
        """Seconds between control decisions."""
        return self._control_period

    @control_period.setter
    def control_period(self, period: float) -> None:
        """Set the period, keeping the dispenser's guard in step."""
        self._control_period = period
        self.dispenser.control_period = period
```

Replace `write_output` and add its two companions:

```python
    def write_output(self, sens_value: float) -> None:
        """Write the actuator value derived from a sensor reading."""
        if self.calibrating:
            return
        if sens_value == ERROR_VALUE:
            # The sentinel is not a measurement. Acting on it would make a
            # boundaries controller dose forever on a dead probe.
            _logger.warning(
                "Holding %s: the reference sensor read failed",
                self.id,
            )
            return
        demand = self.controller.get_value(sens_value)
        self._write_if_changed(self.dispenser.duty(demand))

    def tick(self) -> None:
        """Let the dispenser finish a delivery it already started."""
        if self.calibrating:
            return
        value = self.dispenser.tick()
        if value is not None:
            self._write_if_changed(value)

    def _write_if_changed(self, value: float) -> None:
        """Push a duty value to the hardware only when it actually moved."""
        if value != self.channel.old_value:
            self.channel.old_value = value
            self.write(value)
            _logger.debug("Write %s to %s: %s", value, self.id, self.controller)
```

Replace `set_control_config`:

```python
    def set_control_config(self, config: ControlConfig) -> None:
        """Change the current configuration of the actuator outputs.

        Parameters
        ----------
        config:
            A dataclass with the parameters of the new controller

        """
        reason = check_unit(config.output_unit, self.channel)
        if reason is not None:
            _logger.warning("Rejected config for %s: %s", self.id, reason)
            return

        dispenser = self.dispenser
        if config.output_unit is not dispenser.unit:
            dispenser = Dispenser(
                config.output_unit,
                self.channel,
                self._control_period,
            )
            # The total records the physical pump, not the configuration.
            dispenser.total_volume = self.dispenser.total_volume

        min_val, max_val = dispenser.demand_limits()
        try:
            new_controller = ControlFactory().create_control(
                config,
                min_val=min_val,
                max_val=max_val,
            )
        except TypeError:
            # Each control class checks that the values
            # passed are of the correct type
            _logger.exception("Wrong attributes in %s: %s", self.id, config)
            return

        # Replace the controller only if the configuration actually changed,
        # so an unrelated OPC write does not reset a running timer or PID.
        if self.controller != new_controller or dispenser is not self.dispenser:
            self.dispenser = dispenser
            self.controller = new_controller
            _logger.info(
                "Control config update - %s: %s in %s",
                self.id,
                self.controller,
                self.dispenser.unit,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_actuator.py -v && uv run pytest`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add reactors_czlab/core/actuator.py tests/test_actuator.py
git commit -m "feat: give Actuator a dispenser, a calibration interlock and tick()"
```

---

### Task 7: Reactor drives the fast tick

**Files:**
- Modify: `reactors_czlab/core/reactor.py:86-98,175-186`
- Modify: `reactors_czlab/run_server.py:169`
- Modify: `tests/test_reactor.py:100-138`

**Interfaces:**
- Consumes: `Actuator.tick()`, `Actuator.control_period`, `Dispenser.reset()`.
- Produces: `Reactor.actuator_loop()` replacing `Reactor.unpaired_loop()`.

- [ ] **Step 1: Write the failing test**

In `tests/test_reactor.py`, rename `test_unpaired_loop_drives_unpaired_actuators`
to `test_actuator_loop_drives_unpaired_actuators` and change its
`reactor.unpaired_loop()` call to `reactor.actuator_loop()`. Then append:

```python
async def test_actuator_loop_ticks_paired_actuators(
    make_sensor,
    make_calibrated_actuator,
) -> None:
    """A bolus on a paired pump is ended by the fast loop, not the sampler.

    Regression: paired actuators are only refreshed once per sampling
    period. A dose timed at that granularity would overrun by seconds.
    """
    reactor = Reactor(
        "R0",
        volume=5,
        sensors=[make_sensor()],
        actuators=[make_calibrated_actuator("R0:pwm0")],
        period=10,
    )
    actuator = reactor.actuators["R0:pwm0"]
    actuator.set_control_config(
        ControlConfig(
            ControlMethod.manual,
            value=0.005,  # 0.005 mL at 20 mL/min = 15 ms
            output_unit=OutputUnit.volume,
        ),
    )
    actuator.write_output(0)
    assert actuator.channel.value == 2000

    task = asyncio.create_task(reactor.actuator_loop())
    try:
        await asyncio.sleep(0.2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert actuator.channel.value == 0


def test_the_reactor_stamps_its_period_on_its_actuators(
    make_calibrated_actuator,
    make_sensor,
) -> None:
    """The volume guard has to know how often decisions arrive."""
    reactor = Reactor(
        "R0",
        volume=5,
        sensors=[make_sensor()],
        actuators=[make_calibrated_actuator("R0:pwm0")],
        period=7.5,
    )

    assert reactor.actuators["R0:pwm0"].control_period == 7.5


def test_stop_cancels_a_bolus(make_calibrated_actuator, make_sensor) -> None:
    """A restart must not resume a dose that was in flight."""
    reactor = Reactor(
        "R0",
        volume=5,
        sensors=[make_sensor()],
        actuators=[make_calibrated_actuator("R0:pwm0")],
        period=10,
    )
    actuator = reactor.actuators["R0:pwm0"]
    actuator.set_control_config(
        ControlConfig(
            ControlMethod.manual,
            value=1.0,
            output_unit=OutputUnit.volume,
        ),
    )
    actuator.write_output(0)

    reactor.stop()

    assert actuator.channel.value == 0
    assert actuator.dispenser.tick() is None
```

Add `OutputUnit` to the `reactors_czlab.core.data` import at the top of the file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_reactor.py -v`
Expected: FAIL — `AttributeError: 'Reactor' object has no attribute 'actuator_loop'`

- [ ] **Step 3: Update `core/reactor.py`**

In `__init__`, after `self.unpaired.actuators` is filled:

```python
        # The volume-mode re-trigger guard needs to know how often a paired
        # actuator gets a decision.
        for actuator in self.actuators.values():
            actuator.control_period = period
```

Replace `unpaired_loop` with:

```python
    async def actuator_loop(self) -> None:
        """Refresh unpaired actuators and advance every delivery in flight.

        Two jobs, one loop. Unpaired actuators need their controller run
        often; paired ones are decided once per sampling period but their
        deliveries have to be ended on a far finer grain than that.

        No lock guards the tick: ``write_output()`` and ``tick()`` are both
        synchronous and never await, so a decision from the sampling loop
        cannot interleave with a delivery ending here.
        """
        while True:
            async with self.unpaired.lock:
                for aid in self.unpaired.actuators:
                    self.actuators[aid].write_output(UNPAIRED_INPUT)

            for actuator in self.actuators.values():
                actuator.tick()

            await asyncio.sleep(UNPAIRED_PERIOD)
```

Replace `stop`:

```python
    def stop(self) -> None:
        """Drive every actuator to zero and cancel deliveries in flight."""
        for actuator in self.actuators.values():
            actuator.dispenser.reset()
            actuator.write(0)
```

Update the `UNPAIRED_PERIOD` docstring comment to read
`#: How often unpaired actuators are refreshed and deliveries advanced, in seconds.`

- [ ] **Step 4: Update the call site**

In `reactors_czlab/run_server.py:169`, change
`asyncio.create_task(r_i.reactor.unpaired_loop())` to
`asyncio.create_task(r_i.reactor.actuator_loop())`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: PASS, and `grep -rn "unpaired_loop" reactors_czlab tests` returns nothing.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add reactors_czlab/core/reactor.py reactors_czlab/run_server.py tests/test_reactor.py
git commit -m "feat: rename unpaired_loop to actuator_loop and tick deliveries in it"
```

---

### Task 8: The calibration run

**Files:**
- Modify: `reactors_czlab/core/calibration.py`
- Modify: `tests/test_calibration.py`

**Interfaces:**
- Consumes: `fit_line`, `save_calibration`, `load_calibration` (Task 2);
  `Actuator.calibrating`, `Actuator.write()` (Task 6).
- Produces:
  - `MIN_RUN_SECONDS = 1.0`, `MAX_RUN_SECONDS = 600.0`
  - `CalibrationRun(actuator, clock=perf_counter, sleep=asyncio.sleep)` with
    `async calibrate_point(duty: float, seconds: float) -> str`,
    `record_point(volume_ml: float) -> str`, `fit() -> str`,
    `clear_points() -> str`, `reload() -> str`, `set_duties(min_duty: float, dispense_duty: float) -> str`.

Every method returns a human-readable status string: the operator reads them
straight out of a generic OPC client.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_calibration.py` (add `from reactors_czlab.core.calibration import CalibrationRun` and `import pytest` is already there):

```python
class _FakeSleep:
    """Records how long the run asked to sleep and advances a clock."""

    def __init__(self, clock) -> None:
        self.clock = clock
        self.slept: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)
        # Real sleeps overshoot; the run must use the measured time.
        self.clock.advance(seconds * 1.1)


async def test_a_point_runs_the_pump_and_then_stops_it(
    make_calibrated_actuator,
    clock,
) -> None:
    """The pump is driven, then zeroed, and the interlock is released."""
    actuator = make_calibrated_actuator()
    run = CalibrationRun(actuator, clock=clock, sleep=_FakeSleep(clock))

    await run.calibrate_point(2000.0, 30.0)

    assert actuator.channel.value == 0
    assert actuator.calibrating is False


async def test_a_point_uses_the_measured_elapsed_time(
    make_calibrated_actuator,
    clock,
) -> None:
    """asyncio.sleep drifts; that drift must not enter the flow estimate."""
    actuator = make_calibrated_actuator()
    run = CalibrationRun(actuator, clock=clock, sleep=_FakeSleep(clock))

    await run.calibrate_point(2000.0, 30.0)
    run.record_point(16.5)

    # 33 s actually elapsed, so 16.5 mL is 30 mL/min, not 33.
    assert run.points == [(2000.0, pytest.approx(30.0))]


async def test_recording_without_a_run_is_refused(
    make_calibrated_actuator,
    clock,
) -> None:
    """A volume with no pending point has nothing to attach to."""
    run = CalibrationRun(make_calibrated_actuator(), clock=clock)

    assert "no point" in run.record_point(5.0).lower()
    assert run.points == []


async def test_a_run_rejects_an_out_of_range_duration(
    make_calibrated_actuator,
    clock,
) -> None:
    """Bounded so a fat finger cannot run a pump dry for an hour."""
    actuator = make_calibrated_actuator()
    run = CalibrationRun(actuator, clock=clock, sleep=_FakeSleep(clock))

    assert "seconds" in await run.calibrate_point(2000.0, 6000.0)
    assert actuator.calibrating is False


async def test_a_run_releases_the_pump_when_it_raises(
    make_calibrated_actuator,
    clock,
) -> None:
    """A crashed run must never leave a pump running."""

    async def boom(_seconds: float) -> None:
        error_message = "bus fell over"
        raise OSError(error_message)

    actuator = make_calibrated_actuator()
    run = CalibrationRun(actuator, clock=clock, sleep=boom)

    with pytest.raises(OSError, match="bus fell over"):
        await run.calibrate_point(2000.0, 30.0)

    assert actuator.calibrating is False
    assert actuator.channel.value == 0


async def test_fit_installs_and_stores_the_line(
    make_calibrated_actuator,
    clock,
) -> None:
    """A fit lands on the channel and on disk."""
    actuator = make_calibrated_actuator(fitted=False)
    run = CalibrationRun(actuator, clock=clock, sleep=_FakeSleep(clock))

    for duty, volume in ((1000.0, 5.0), (3000.0, 15.0)):
        await run.calibrate_point(duty, 60.0)
        run.record_point(volume)

    run.fit()

    # 5 mL and 15 mL over 2000 counts would be a slope of 0.005, but the
    # fake sleep overshoots by 10%, so the measured flows are 1/1.1 of that.
    cal = actuator.channel.calibration
    assert cal.is_fitted is True
    assert cal.a == pytest.approx(0.005 / 1.1)
    assert load_calibration("R0_pwm0").a == pytest.approx(cal.a)


async def test_fit_is_refused_with_one_point(
    make_calibrated_actuator,
    clock,
) -> None:
    """A single measurement does not define a line; keep the old one."""
    actuator = make_calibrated_actuator(fitted=False)
    run = CalibrationRun(actuator, clock=clock, sleep=_FakeSleep(clock))
    await run.calibrate_point(2000.0, 60.0)
    run.record_point(20.0)

    result = run.fit()

    assert "distinct" in result
    assert actuator.channel.calibration.is_fitted is False


async def test_fit_needs_a_calibration_slot_on_the_channel(clock) -> None:
    """A channel with no Calibration has no file to store under."""
    from reactors_czlab.core.actuator import RandomActuator
    from reactors_czlab.core.data import PhysicalInfo, PlcOutput

    info = PhysicalInfo(
        model="pwm",
        address=0,
        type=PlcOutput.pwm,
        channels=[Channel("pwm1", "pwm", pin="Q1.5")],
    )
    run = CalibrationRun(RandomActuator("R0:pwm1", info), clock=clock)

    assert "no calibration" in run.fit().lower()


async def test_clear_points_keeps_the_installed_calibration(
    make_calibrated_actuator,
    clock,
) -> None:
    """Restarting a run must not disturb the pump that is already good."""
    actuator = make_calibrated_actuator()
    run = CalibrationRun(actuator, clock=clock, sleep=_FakeSleep(clock))
    await run.calibrate_point(2000.0, 60.0)
    run.record_point(20.0)

    run.clear_points()

    assert run.points == []
    assert actuator.channel.calibration.is_fitted is True


async def test_reload_reinstalls_the_stored_calibration(
    make_calibrated_actuator,
    clock,
) -> None:
    """The runtime reload path."""
    actuator = make_calibrated_actuator(fitted=False)
    save_calibration(
        Calibration("R0_pwm0", a=0.02, fitted_at="2026-07-27T10:00:00+00:00"),
    )
    run = CalibrationRun(actuator, clock=clock)

    run.reload()

    assert actuator.channel.calibration.a == 0.02


async def test_set_duties_stores_the_bench_knobs(
    make_calibrated_actuator,
    clock,
) -> None:
    """min_duty and dispense_duty are adjustable without a refit."""
    actuator = make_calibrated_actuator()
    run = CalibrationRun(actuator, clock=clock)

    run.set_duties(500.0, 1500.0)

    assert actuator.channel.calibration.min_duty == 500.0
    assert actuator.channel.calibration.dispense_duty == 1500.0
    assert load_calibration("R0_pwm0").dispense_duty == 1500.0


async def test_set_duties_rejects_a_dispense_duty_below_the_floor(
    make_calibrated_actuator,
    clock,
) -> None:
    """Dispensing below the stall floor would never finish a bolus."""
    actuator = make_calibrated_actuator()
    run = CalibrationRun(actuator, clock=clock)

    result = run.set_duties(1500.0, 500.0)

    assert "stall" in result
    assert actuator.channel.calibration.dispense_duty == 2000.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_calibration.py -v`
Expected: FAIL — `ImportError: cannot import name 'CalibrationRun'`

- [ ] **Step 3: Add `CalibrationRun` to `core/calibration.py`**

Add to the imports:

```python
import asyncio
from datetime import UTC, datetime
from time import perf_counter
from typing import TYPE_CHECKING, Callable

from reactors_czlab.core.data import MAX_OUTPUT, Calibration
```

and inside `if TYPE_CHECKING:` add `from reactors_czlab.core.actuator import Actuator`.
Importing `Actuator` only for typing keeps `core.actuator` free of an import
back into this module, so there is no cycle.

Add the bounds and the class:

```python
#: A calibration point shorter than this cannot be measured accurately.
MIN_RUN_SECONDS = 1.0

#: Upper bound, so a mistyped duration cannot run a pump dry.
MAX_RUN_SECONDS = 600.0


class CalibrationRun:
    """Collect calibration points from one actuator and fit them.

    Every method returns a status string: the operator drives this from a
    generic OPC client and reads the result straight off the method call.
    """

    def __init__(
        self,
        actuator: Actuator,
        clock: Callable[[], float] = perf_counter,
        sleep: Callable[[float], object] = asyncio.sleep,
    ) -> None:
        """Attach a run to an actuator.

        Parameters
        ----------
        actuator:
            The pump being calibrated.
        clock, sleep:
            Injectable so the tests neither wait nor guess at drift.

        """
        self.actuator = actuator
        self.points: list[tuple[float, float]] = []
        # Public so a test - or a bench script - can swap them after
        # construction; ActuatorOpc builds the run itself.
        self.clock = clock
        self.sleep = sleep

        self._pending: tuple[float, float] | None = None
        self._running = False

    def __repr__(self) -> str:
        """Print the actuator and how many points are collected."""
        return f"CalibrationRun({self.actuator.id}, {len(self.points)} points)"

    async def calibrate_point(self, duty: float, seconds: float) -> str:
        """Run the pump at ``duty`` for ``seconds``, then stop it.

        The elapsed time is measured rather than assumed: ``asyncio.sleep``
        overshoots, and that overshoot would go straight into the flow.
        """
        if self._running:
            return f"{self.actuator.id} is already calibrating"
        if not 0 <= duty <= MAX_OUTPUT:
            return f"duty must be within 0 - {MAX_OUTPUT}, got {duty}"
        if not MIN_RUN_SECONDS <= seconds <= MAX_RUN_SECONDS:
            return (
                f"seconds must be within {MIN_RUN_SECONDS} - "
                f"{MAX_RUN_SECONDS}, got {seconds}"
            )

        self._running = True
        self.actuator.calibrating = True
        start = self.clock()
        try:
            self.actuator.write(duty)
            await self.sleep(seconds)
        finally:
            elapsed = self.clock() - start
            self.actuator.write(0)
            # write() bypasses the change guard, so put old_value back in
            # step or the control loop will not rewrite the same value.
            self.actuator.channel.old_value = 0
            self.actuator.calibrating = False
            self._running = False

        self._pending = (duty, elapsed)
        _logger.info(
            "Calibration point on %s: duty %s for %.3fs",
            self.actuator.id,
            duty,
            elapsed,
        )
        return (
            f"ran duty {duty} for {elapsed:.3f}s - now record the measured "
            "volume in mL"
        )

    def record_point(self, volume_ml: float) -> str:
        """Attach the operator's measured volume to the last run."""
        if self._pending is None:
            return "no point is waiting for a measurement"

        duty, elapsed = self._pending
        self._pending = None
        flow = volume_ml / (elapsed / 60.0)
        self.points.append((duty, flow))
        return (
            f"duty {duty} -> {flow:.4f} mL/min "
            f"({len(self.points)} points collected)"
        )

    def fit(self) -> str:
        """Fit, store and install the collected points."""
        current = self.actuator.channel.calibration
        if current is None:
            return (
                f"{self.actuator.id} has no calibration slot on its channel; "
                "give it one in server_info.py"
            )

        try:
            a, b, r2 = fit_line(self.points)
        except ValueError as exc:
            _logger.warning("Fit refused for %s: %s", self.actuator.id, exc)
            return str(exc)

        cal = Calibration(
            file=current.file,
            a=a,
            b=b,
            min_duty=self._stall_floor(a, b),
            max_duty=current.max_duty,
            dispense_duty=current.dispense_duty,
            points=list(self.points),
            fitted_at=datetime.now(UTC).isoformat(),
            r2=r2,
        )
        save_calibration(cal)
        self.actuator.channel.calibration = cal
        return (
            f"fitted flow = {a:.6g} * duty + {b:.6g} (r2 {r2:.4f}), "
            f"stall floor {cal.min_duty:.0f}"
        )

    def clear_points(self) -> str:
        """Throw the collected points away, keeping the installed line."""
        self.points = []
        self._pending = None
        return f"cleared the collected points for {self.actuator.id}"

    def reload(self) -> str:
        """Re-read the stored calibration from disk."""
        current = self.actuator.channel.calibration
        if current is None:
            return f"{self.actuator.id} has no calibration slot on its channel"

        stored = load_calibration(current.file)
        if stored is None:
            return f"no usable stored calibration for {current.file}"

        self.actuator.channel.calibration = stored
        return f"reloaded {current.file}, fitted at {stored.fitted_at}"

    def set_duties(self, min_duty: float, dispense_duty: float) -> str:
        """Adjust the stall floor and the bolus duty without a refit."""
        cal = self.actuator.channel.calibration
        if cal is None:
            return f"{self.actuator.id} has no calibration slot on its channel"
        if dispense_duty < min_duty:
            return (
                f"dispense duty {dispense_duty} is below the stall floor "
                f"{min_duty}; a bolus at that duty would never finish"
            )
        if not 0 <= dispense_duty <= MAX_OUTPUT:
            return f"dispense duty must be within 0 - {MAX_OUTPUT}"

        cal.min_duty = min_duty
        cal.dispense_duty = dispense_duty
        save_calibration(cal)
        return f"min duty {min_duty}, dispense duty {dispense_duty}"

    def _stall_floor(self, a: float, b: float) -> float:
        """Lowest duty the pump is believed to actually turn at.

        The fitted x-intercept is the estimate; a point that measured no
        volume at all is direct evidence and overrides it.
        """
        floor = max(0.0, -b / a)
        measured = [duty for duty, flow in self.points if flow <= 0]
        if measured:
            floor = max(floor, max(measured))
        return floor
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_calibration.py -v && uv run pytest`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add reactors_czlab/core/calibration.py tests/test_calibration.py
git commit -m "feat: add the pump calibration run state machine"
```

---

### Task 9: OPC — output unit and published pump data

**Files:**
- Modify: `reactors_czlab/opcua/actuator.py:20-25,40-47,94-113,115-205`

**Interfaces:**
- Consumes: `OutputUnit` (Task 1), `Actuator.dispenser` (Task 6).
- Produces: `output_unit` map and variable, and the published `total_volume`,
  `cal_a`, `cal_b`, `cal_r2` variables on the actuator node.

Browse names follow the existing contract `<reactor>:<name>:<channel>`, so
`R0:pwm0:total_volume` lands in the `data` table with no client-side change.

- [ ] **Step 1: Write the failing test**

Create `tests/test_opcua_actuator.py`:

```python
"""Tests for the actuator node's control-config plumbing.

The method and variable bodies are exercised directly against stub nodes:
asyncua is a base dependency but a running server is not needed.
"""

from __future__ import annotations

from reactors_czlab.core.data import OutputUnit
from reactors_czlab.opcua.actuator import ActuatorOpc, output_unit_map


class _StubVariable:
    """An asyncua variable that just holds a value."""

    def __init__(self, value: object) -> None:
        self.value = value

    async def get_value(self) -> object:
        """Read the held value."""
        return self.value

    async def write_value(self, value: object) -> None:
        """Store a written value."""
        self.value = value


def test_output_unit_map_covers_every_unit() -> None:
    """The OPC enum and the Python enum must not drift apart."""
    assert set(output_unit_map.values()) == set(OutputUnit)


async def test_datachange_reads_the_output_unit(
    make_calibrated_actuator,
) -> None:
    """Writing unit 1 puts a flow config on the actuator."""
    actuator = make_calibrated_actuator()
    node = ActuatorOpc(actuator)
    node.method = _StubVariable(0)  # manual
    node.value = _StubVariable(20.0)
    node.output_unit = _StubVariable(1)  # flow

    await node.datachange_notification(None, 0.0, None)

    assert actuator.dispenser.unit is OutputUnit.flow
    assert actuator.controller.value == 20.0


async def test_datachange_defaults_to_duty_on_a_bad_unit(
    make_calibrated_actuator,
) -> None:
    """An out of range index is logged and ignored, not fatal."""
    actuator = make_calibrated_actuator()
    node = ActuatorOpc(actuator)
    node.method = _StubVariable(0)
    node.value = _StubVariable(150.0)
    node.output_unit = _StubVariable(99)

    await node.datachange_notification(None, 0.0, None)

    assert actuator.dispenser.unit is OutputUnit.duty


async def test_update_value_publishes_the_pump_totals(
    make_calibrated_actuator,
) -> None:
    """total_volume and the fitted line reach the server variables."""
    actuator = make_calibrated_actuator()
    actuator.dispenser.total_volume = 3.25
    node = ActuatorOpc(actuator)
    node.curr_value = _StubVariable(0.0)
    node.total_volume = _StubVariable(0.0)
    node.cal_a = _StubVariable(0.0)
    node.cal_b = _StubVariable(0.0)
    node.cal_r2 = _StubVariable(0.0)

    await node.update_value()

    assert node.total_volume.value == 3.25
    assert node.cal_a.value == 0.01
    assert node.cal_r2.value == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_opcua_actuator.py -v`
Expected: FAIL — `ImportError: cannot import name 'output_unit_map'`

- [ ] **Step 3: Update `opcua/actuator.py`**

Add the map next to `control_method`, and `OutputUnit` to the data import:

```python
output_unit_map = {
    0: OutputUnit.duty,
    1: OutputUnit.flow,
    2: OutputUnit.volume,
}
```

In `datachange_notification`, after the `control_method` lookup, read the unit:

```python
        unit_index = await self.output_unit.get_value()
        try:
            unit = output_unit_map[unit_index]
        except KeyError:
            _logger.exception(
                "%s is not a member of %s",
                unit_index,
                sorted(output_unit_map),
            )
            return
```

and build the config with it:

```python
        config = ControlConfig(
            method,
            value=await self.value.get_value(),
            output_unit=unit,
        )
```

In `init_control_node`, after the `method` variable and its `EnumStrings`, add
the unit variable with the same shape:

```python
        # Unit the demand is expressed in: raw counts, mL/min, or mL.
        self.output_unit = await self.control_method.add_variable(
            idx,
            f"{self.id}:output_unit",
            0,
            varianttype=ua.VariantType.UInt32,
        )
        await self.output_unit.set_writable()
        unit_strings_variant = ua.Variant(
            [ua.LocalizedText(output_unit_map[k]) for k in output_unit_map],
            ua.VariantType.LocalizedText,
        )
        await self.output_unit.add_property(
            ua.ObjectIds.MultiStateDiscreteType_EnumStrings,
            "EnumStrings",
            unit_strings_variant,
        )
```

Still in `init_control_node`, next to `curr_value`, add the published pump data.
These hang off `self.node`, so their browse names are
`R0:pwm0:total_volume` and friends:

```python
        # Published pump data. The browse names follow the
        # <reactor>:<name>:<channel> contract, so they reach the data table.
        self.total_volume = await self.node.add_variable(
            idx,
            f"{self.id}:total_volume",
            0.0,
        )
        self.cal_a = await self.node.add_variable(idx, f"{self.id}:cal_a", 0.0)
        self.cal_b = await self.node.add_variable(idx, f"{self.id}:cal_b", 0.0)
        self.cal_r2 = await self.node.add_variable(
            idx,
            f"{self.id}:cal_r2",
            0.0,
        )
```

Replace `update_value`:

```python
    async def update_value(self) -> None:
        """Publish the actuator output and pump data if they changed."""
        published = await self.curr_value.get_value()
        # old_value is what write_output() last pushed to the hardware.
        current = self.actuator.channel.old_value
        if current != published:
            await self.curr_value.write_value(float(current))
            _logger.debug("Updated %s with value %s", self.id, current)

        await self.total_volume.write_value(
            float(self.actuator.dispenser.total_volume),
        )
        cal = self.actuator.channel.calibration
        if cal is not None:
            await self.cal_a.write_value(float(cal.a))
            await self.cal_b.write_value(float(cal.b))
            await self.cal_r2.write_value(float(cal.r2))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_opcua_actuator.py -v && uv run pytest`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add reactors_czlab/opcua/actuator.py tests/test_opcua_actuator.py
git commit -m "feat: expose the output unit and pump totals over OPC"
```

---

### Task 10: OPC — the calibration methods

**Files:**
- Modify: `reactors_czlab/opcua/actuator.py`
- Create: `tests/test_opcua_calibration.py`

**Interfaces:**
- Consumes: `CalibrationRun` (Task 8).
- Produces: `ActuatorOpc.run` (a `CalibrationRun`) and
  `ActuatorOpc.init_calibration_methods(idx: int) -> None`, registering
  `calibrate_point`, `record_point`, `fit_calibration`, `clear_points`,
  `reload_calibration` and `set_duties` on the actuator node.

- [ ] **Step 1: Write the failing test**

Create `tests/test_opcua_calibration.py`:

```python
"""Tests for the calibration OPC methods.

init_calibration_methods() is handed a stub node that captures the callables
instead of registering them, so no server is needed - the same pattern as
tests/test_opcua_pairing.py.
"""

from __future__ import annotations

import pytest

from reactors_czlab.core.calibration import CALIBRATION_ENV
from reactors_czlab.opcua.actuator import ActuatorOpc


class _CapturingNode:
    """Stand-in for an asyncua node that records added methods."""

    def __init__(self) -> None:
        self.methods: dict[str, object] = {}

    async def add_method(self, idx, name, callback, *args, **kwargs) -> None:  # noqa: ANN001, ANN002, ANN003, ARG002
        """Capture the callback under its bare method name."""
        self.methods[name.split(":")[-1]] = callback


@pytest.fixture(autouse=True)
def _cal_dir(tmp_path, monkeypatch) -> None:
    """Keep every test out of the operator's real calibration directory."""
    monkeypatch.setenv(CALIBRATION_ENV, str(tmp_path))


@pytest.fixture
async def calibrating(make_calibrated_actuator, clock):
    """An ActuatorOpc with its calibration methods captured."""

    async def instant(seconds: float) -> None:
        clock.advance(seconds)

    actuator = make_calibrated_actuator(fitted=False)
    node_opc = ActuatorOpc(actuator)
    node_opc.node = _CapturingNode()
    node_opc.run.clock = clock
    node_opc.run.sleep = instant
    await node_opc.init_calibration_methods(2)
    return node_opc, node_opc.node.methods


async def test_the_six_methods_are_registered(calibrating) -> None:
    """The operator's whole workflow is reachable from an OPC client."""
    _, methods = calibrating

    assert set(methods) == {
        "calibrate_point",
        "record_point",
        "fit_calibration",
        "clear_points",
        "reload_calibration",
        "set_duties",
    }


async def test_a_full_calibration_over_the_methods(calibrating) -> None:
    """Run two points, record them, fit, and the channel is calibrated."""
    node_opc, methods = calibrating

    await methods["calibrate_point"](None, 1000.0, 60.0)
    methods["record_point"](None, 10.0)
    await methods["calibrate_point"](None, 3000.0, 60.0)
    methods["record_point"](None, 30.0)

    result = methods["fit_calibration"](None)

    assert "fitted" in result
    assert node_opc.actuator.channel.calibration.a == pytest.approx(0.01)


async def test_reload_reports_when_there_is_nothing_stored(
    calibrating,
) -> None:
    """A missing file is an operator-visible message, not an exception."""
    _, methods = calibrating

    assert "no usable stored calibration" in methods["reload_calibration"](None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_opcua_calibration.py -v`
Expected: FAIL — `AttributeError: 'ActuatorOpc' object has no attribute 'run'`

- [ ] **Step 3: Add the methods to `opcua/actuator.py`**

Import the run and `uamethod`:

```python
from asyncua import ua, uamethod

from reactors_czlab.core.calibration import CalibrationRun
```

In `ActuatorOpc.__init__`:

```python
        self.run = CalibrationRun(actuator)
```

Call the new initialiser from `init_node`, after `init_control_subscription`:

```python
        await self.init_calibration_methods(idx)
```

Add the method:

```python
    async def init_calibration_methods(self, idx: int) -> None:
        """Expose the pump calibration workflow on the actuator node.

        Every method answers with a status string; the operator drives the
        run from a generic OPC client and reads the result off the call.
        """
        run = self.run

        @uamethod
        async def calibrate_point(
            parent: Node,
            duty: float,
            seconds: float,
        ) -> str:
            """Run the pump at a duty for a time, then stop it."""
            return await run.calibrate_point(duty, seconds)

        @uamethod
        def record_point(parent: Node, volume_ml: float) -> str:
            """Record the volume measured for the last point."""
            return run.record_point(volume_ml)

        @uamethod
        def fit_calibration(parent: Node) -> str:
            """Fit, store and install the collected points."""
            return run.fit()

        @uamethod
        def clear_points(parent: Node) -> str:
            """Throw the collected points away."""
            return run.clear_points()

        @uamethod
        def reload_calibration(parent: Node) -> str:
            """Re-read the stored calibration from disk."""
            return run.reload()

        @uamethod
        def set_duties(
            parent: Node,
            min_duty: float,
            dispense_duty: float,
        ) -> str:
            """Adjust the stall floor and the bolus duty without a refit."""
            return run.set_duties(min_duty, dispense_duty)

        inarg_duty = ua.Argument()
        inarg_duty.Name = "Duty"
        inarg_duty.DataType = ua.NodeId(ua.ObjectIds.Float)
        inarg_duty.Description = ua.LocalizedText(
            Text="PLC counts to drive the pump at",
        )

        inarg_seconds = ua.Argument()
        inarg_seconds.Name = "Seconds"
        inarg_seconds.DataType = ua.NodeId(ua.ObjectIds.Float)
        inarg_seconds.Description = ua.LocalizedText(
            Text="How long to run the pump for",
        )

        inarg_volume = ua.Argument()
        inarg_volume.Name = "Volume_ml"
        inarg_volume.DataType = ua.NodeId(ua.ObjectIds.Float)
        inarg_volume.Description = ua.LocalizedText(
            Text="Measured volume delivered by the last point, in mL",
        )

        inarg_min_duty = ua.Argument()
        inarg_min_duty.Name = "Min_duty"
        inarg_min_duty.DataType = ua.NodeId(ua.ObjectIds.Float)
        inarg_min_duty.Description = ua.LocalizedText(
            Text="Stall floor: the lowest duty the pump turns at",
        )

        inarg_dispense = ua.Argument()
        inarg_dispense.Name = "Dispense_duty"
        inarg_dispense.DataType = ua.NodeId(ua.ObjectIds.Float)
        inarg_dispense.Description = ua.LocalizedText(
            Text="Duty used for volume boluses",
        )

        outarg = ua.Argument()
        outarg.Name = "Status"
        outarg.DataType = ua.NodeId(ua.ObjectIds.String)

        for name, callback, inargs in (
            ("calibrate_point", calibrate_point, [inarg_duty, inarg_seconds]),
            ("record_point", record_point, [inarg_volume]),
            ("fit_calibration", fit_calibration, []),
            ("clear_points", clear_points, []),
            ("reload_calibration", reload_calibration, []),
            ("set_duties", set_duties, [inarg_min_duty, inarg_dispense]),
        ):
            await self.node.add_method(
                idx,
                f"{self.id}:{name}",
                callback,
                inargs,
                [outarg],
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add reactors_czlab/opcua/actuator.py tests/test_opcua_calibration.py
git commit -m "feat: expose the pump calibration workflow as OPC methods"
```

---

### Task 11: Startup wiring, pump inventory and documentation

**Files:**
- Modify: `reactors_czlab/run_server.py:52-58,131-147`
- Modify: `reactors_czlab/server_info.py:136-288`
- Modify: `CLAUDE.md`
- Modify: `README.md:100-115`

**Interfaces:**
- Consumes: `load_into` (Task 2).
- Produces: calibrations installed on every pump channel at server start.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_calibration.py`:

```python
def test_load_into_is_idempotent() -> None:
    """Startup wiring may run more than once without drifting."""
    save_calibration(
        Calibration("R0_pwm0", a=0.01, fitted_at="2026-07-27T10:00:00+00:00"),
    )
    channel = Channel("pwm0", "pwm", calibration=Calibration("R0_pwm0"))

    assert load_into(channel) is True
    assert load_into(channel) is True
    assert channel.calibration.a == 0.01
```

- [ ] **Step 2: Run test to verify it passes already**

Run: `uv run pytest tests/test_calibration.py::test_load_into_is_idempotent -v`
Expected: PASS — this pins existing behaviour before the wiring depends on it.

- [ ] **Step 3: Give every pump a unique calibration slot**

In `reactors_czlab/server_info.py`, every channel inside `ANALOG_ACTUATORS`
gets `calibration=Calibration("<reactor>_<pwm>")`. Change the existing
`R0:pwm0` entry from `Calibration("pump_0")` to `Calibration("R0_pwm0")` and add
the same to the other eleven, e.g.:

```python
                Channel(
                    "pwm1",
                    "pwm",
                    pin="Q1.5",
                    calibration=Calibration("R1_pwm1"),
                ),
```

The names must be unique across reactors: `pump_0` on three reactors would have
them share one file. Nothing has ever written a calibration file, so renaming
the R0 entry orphans nothing.

- [ ] **Step 4: Load calibrations at server start**

In `reactors_czlab/run_server.py`, import the loader:

```python
from reactors_czlab.core.calibration import load_into
```

and call it in `build_reactors`, in **both** the simulated and the hardware
branch, after the actuator list is built. In the hardware branch, immediately
after `actuators.extend(...)`:

```python
        # Install any stored pump calibration. Explicit, like init_hardware():
        # nothing reads the filesystem at import time.
        for actuator in actuators:
            load_into(actuator.channel)
```

The simulated branch is a single comprehension today, so the loop has nowhere
to live. Replace the whole `if simulated:` block with:

```python
    if simulated:
        reactors = []
        for r in REACTORS:
            sensors = [
                *(RandomSensor(k, cfg) for k, cfg in HAMILTON_SENSORS[r].items()),
                *(RandomSensor(k, cfg) for k, cfg in BIOMASS_SENSORS[r].items()),
            ]
            actuators = [
                *(RandomActuator(k, cfg) for k, cfg in ANALOG_ACTUATORS[r].items()),
                *(RandomActuator(k, cfg) for k, cfg in MFC_ACTUATORS[r].items()),
            ]
            for actuator in actuators:
                load_into(actuator.channel)

            reactors.append(
                ReactorOpc(
                    r,
                    volume=REACTOR_VOLUME,
                    sensors=sensors,
                    actuators=actuators,
                    period=SAMPLE_PERIOD,
                ),
            )
        return reactors
```

The two `*(...)` lines run past 79 characters; let `ruff format` break them.

- [ ] **Step 5: Run the server with no hardware**

Run:

```bash
uv run reactors-server --simulated --endpoint opc.tcp://localhost:4840/
```

Expected: it starts, and the log shows one
`No stored calibration for R0_pwm0 at ...` warning per pump. Stop it with
Ctrl-C.

- [ ] **Step 6: Update CLAUDE.md**

Add `calibration.py` and `dispenser.py` to the `core/` block of the Layout
section:

```
    calibration.py Pump calibration: fit, store, reload, run state machine
    dispenser.py   Demand (mL/min or mL) -> duty, bolus timing, volume totals
```

Add to "Hard constraints":

```
- **`core/calibration.py` and `core/dispenser.py` are standard library only.**
  They run on the Pi, which has neither numpy nor psycopg. The calibration fit
  is an ordinary least squares written out by hand for that reason.
```

Add a new subsection under "Model you need to hold":

```markdown
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
"simplify" it away - `test_dispenser.py` pins it.

`Dispenser.total_volume` integrates the *actual* duty over the *actual* elapsed
time, never the sum of demanded volumes, so a superseded bolus still totals
correctly. It survives a control-config change: it records the pump, not the
config.

A flow or volume config against a channel with no fitted calibration is
rejected and logged; treating mL/min as raw counts would peg a pump.
`_PidControl.kp` defaults to 100.0, which is tuned for 0-4095 counts and is
wrong in mL/min - gains must be retuned per unit.
```

Under "Conventions", add:

```
- `Actuator.write_output()` skips the decision when the reference reading is
  `ERROR_VALUE` and holds the last output. A failed probe reads -0.111, which
  would otherwise make a boundaries controller dose forever.
```

Rename `unpaired_loop` to `actuator_loop` in the "Two loops, one pairing table"
section, and note that it now also ends deliveries in flight.

Remove "pump calibration" from the "Open items" list.

- [ ] **Step 7: Update README.md**

In the "To do" list, remove the "Pump actuators (pump calibration and volume
dispense)" entry and add a short operator note describing the calibration
workflow: call `calibrate_point(duty, seconds)`, measure what came out, call
`record_point(volume_ml)`, repeat for at least two duties, then
`fit_calibration()`. Mention that calibrations live in
`~/.reactors_czlab/calibrations/` and that `REACTORS_CALIBRATION_DIR` overrides
that.

- [ ] **Step 8: Run the whole suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add reactors_czlab/run_server.py reactors_czlab/server_info.py CLAUDE.md README.md tests/test_calibration.py
git commit -m "feat: load pump calibrations at startup and document the workflow"
```

---

## Bench verification (hardware, not automated)

These cannot be checked in CI. Do them on the Pi before trusting a run.

- [ ] Calibrate one pump end to end over OPC and confirm `cal_a` and `cal_r2`
      look sane, and that `~/.reactors_czlab/calibrations/R0_pwm0.json` exists.
- [ ] Set a manual flow demand and confirm the measured delivery matches within
      a few percent over a minute.
- [ ] Set a manual volume demand of 1 mL and weigh the result. Expect a small
      over-delivery: the bolus is ended by the 20 Hz loop, so it can overrun by
      up to 50 ms.
- [ ] Confirm `min_duty` is right by demanding a flow just above and just below
      the stall floor.
- [ ] The unrelated **Modbus byte order is still unverified** (see CLAUDE.md).
      If Hamilton readings look byte-swapped, flip `BYTE_ORDER` / `WORD_ORDER`
      and nothing else. Do not conflate that with a pump problem.
