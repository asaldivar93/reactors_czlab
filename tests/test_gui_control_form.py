"""Tests for the actuator configuration dialog.

The write ordering is pinned twice on purpose: test_gui_control.py pins
the plan build_write_plan produces, and this pins that the dialog
actually applies it in that order through the UI. The first can pass
while the second fails - a dialog that sorts, batches or parallelises
the writes would break the safety property without touching the plan.
"""

from __future__ import annotations

import json

import pytest
from nicegui.testing import User

from reactors_czlab.gui.components.values import actuator_panel, sensor_panel


@pytest.fixture(autouse=True)
def _isolated_refresh_targets():
    """Give each test in this file its own view of the refresh targets.

    ``actuator_panel``/``sensor_panel`` (``gui/components/values.py``)
    are ``@ui.refreshable`` functions defined at module level, so the
    same wrapper object - and its internal ``targets`` list - is
    reused for every test in the whole pytest session, not just this
    file. NiceGUI only drops a stale entry once that target's
    ``Client.delete()`` has actually run; for a real disconnect that
    happens ``reconnect_timeout`` seconds later
    (``nicegui/client.py:handle_disconnect`` -> ``delete_content``),
    which is harmless in production - nothing else is racing it, so it
    always completes and the next refresh prunes the entry cleanly.

    The test harness is different: its own teardown
    (``background_tasks.teardown``, a hard 2 second cap - see
    ``nicegui/background_tasks.py``) is shorter than the default 3
    second ``reconnect_timeout``, so it cancels every test client's
    pending ``delete_content`` before ``Client.delete()`` ever runs -
    for every test, not just slow ones. The cancelled client can still
    end up garbage collected without ``is_deleted`` ever having been
    set, so a *later* test's own timer tick can find that stale entry
    in the shared list and crash trying to clear it - not because
    anything in that later test did something wrong. Clearing the list
    before (and after, for whatever runs next) each test means a test
    can only ever see the target it made itself, matching what
    production looks like once a disconnect has had its real grace
    period.
    """
    actuator_panel.targets.clear()
    sensor_panel.targets.clear()
    yield
    actuator_panel.targets.clear()
    sensor_panel.targets.clear()

FITTED = json.dumps(
    {
        "file": "R0_pwm0",
        "a": 0.01,
        "b": 0.0,
        "r2": 1.0,
        "min_duty": 400.0,
        "max_duty": 4000.0,
        "dispense_duty": 2000.0,
        "fitted_at": "2026-07-27T10:00:00+00:00",
        "is_fitted": True,
        "points": [[500.0, 5.0]],
        "run_points": [],
    },
)

UNFITTED = json.dumps(
    {
        "file": "R0_pwm0",
        "a": 1.0,
        "b": 0.0,
        "r2": 0.0,
        "min_duty": 0.0,
        "max_duty": 4095.0,
        "dispense_duty": 4095.0,
        "fitted_at": "",
        "is_fitted": False,
        "points": [],
        "run_points": [],
    },
)


@pytest.fixture
def writes(gui_state, monkeypatch: pytest.MonkeyPatch) -> list:
    """Record every variable write, in order, instead of sending it."""
    recorded: list[tuple[str, object]] = []

    async def write_variable(reactor, name, channel, value):
        recorded.append((channel, value))
        return True

    def reading(reactor, name, channel):
        return (0.0, None)

    monkeypatch.setattr(gui_state, "write_variable", write_variable)
    monkeypatch.setattr(gui_state, "reading", reading)
    return recorded


@pytest.fixture
def fitted(gui_state, monkeypatch: pytest.MonkeyPatch) -> None:
    """get_calibration answers with a fitted line."""

    async def call(reactor, owner, method, *args):
        return FITTED

    monkeypatch.setattr(gui_state, "call", call)


@pytest.fixture
def unfitted(gui_state, monkeypatch: pytest.MonkeyPatch) -> None:
    """get_calibration answers with an unfitted placeholder."""

    async def call(reactor, owner, method, *args):
        return UNFITTED

    monkeypatch.setattr(gui_state, "call", call)


async def test_applying_writes_method_last(
    user: User,
    writes: list,
    fitted: None,
) -> None:
    """Regression: the server rebuilds the whole ControlConfig on every
    notification, so writing method first applies the new controller
    against stale parameters - a manual -> pid switch could drive hard
    for one notification.
    """
    await user.open("/reactor/R0")
    user.find("Configure").click()
    await user.should_see("pwm0 control")

    user.find("Apply").click()
    await user.should_see("configured")

    channels = [channel for channel, _ in writes]
    assert channels[-1] == "method"
    assert channels[-2] == "output_unit"


async def test_an_unfitted_pump_refuses_a_flow_unit(
    user: User,
    writes: list,
    unfitted: None,
) -> None:
    """check_unit() rejects this server-side and only logs the reason.

    The form must refuse before writing, or the operator sees a write
    succeed and nothing happen.
    """
    await user.open("/reactor/R0")
    user.find("Configure").click()
    await user.should_see("pwm0 control")

    user.find("Output unit").click()
    user.find("flow").click()
    await user.should_see("fitted calibration")


async def test_a_refused_unit_writes_nothing(
    user: User,
    writes: list,
    unfitted: None,
) -> None:
    """A refusal must not leave the actuator part configured."""
    await user.open("/reactor/R0")
    user.find("Configure").click()
    await user.should_see("pwm0 control")

    user.find("Output unit").click()
    user.find("flow").click()
    user.find("Apply").click()
    await user.should_see("fitted calibration")

    assert writes == []
