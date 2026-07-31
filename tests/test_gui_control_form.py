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


async def test_applying_pid_writes_parameters_before_unit_and_method(
    user: User,
    writes: list,
    fitted: None,
) -> None:
    """The manual plan pinned above is only 3 writes (value, output_unit,
    method) - too short to catch an ordering bug among a method's own
    parameters. pid contributes eight (setpoint, kp, ki, kd, backwards,
    min_integral, max_integral, auto_integral_band); all of them must
    still land before output_unit, which must still land before method.
    """
    await user.open("/reactor/R0")
    user.find("Configure").click()
    await user.should_see("pwm0 control")

    user.find("Method").click()
    user.find("pid").click()
    await user.should_see("kp")

    user.find("Apply").click()
    await user.should_see("configured")

    channels = [channel for channel, _ in writes]
    pid_fields = (
        "setpoint",
        "kp",
        "ki",
        "kd",
        "backwards",
        "min_integral",
        "max_integral",
        "auto_integral_band",
    )
    assert set(pid_fields) <= set(channels)
    assert channels[-1] == "method"
    assert channels[-2] == "output_unit"
    unit_index = channels.index("output_unit")
    for field in pid_fields:
        assert channels.index(field) < unit_index


async def test_dialog_prefills_the_running_config(
    user: User,
    writes: list,
    fitted: None,
    gui_state,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the selects must reflect what is actually running.

    Opening the dialog hardcoded at manual/duty while the numeric
    fields were prefilled meant an operator who opened it only to
    nudge a gain on a pump running pid/flow would, on Apply, also
    write method=manual and output_unit=duty - the same demand number
    then means raw counts instead of mL/min on a live dosing pump.
    """
    running = {
        "method": 3.0,  # pid
        "output_unit": 1.0,  # flow
        "setpoint": 7.0,
        "kp": 2.0,
        "ki": 0.1,
        "kd": 0.0,
        "min_integral": -10.0,
        "max_integral": 10.0,
        "backwards": 0.0,
        "auto_integral_band": 0.0,
    }

    def reading(reactor, name, channel):
        return (running.get(channel), None)

    monkeypatch.setattr(gui_state, "reading", reading)

    await user.open("/reactor/R0")
    user.find("Configure").click()
    await user.should_see("pwm0 control")

    user.find("Apply").click()
    await user.should_see("configured")

    written = dict(writes)
    assert written["method"] == 3
    assert written["output_unit"] == 1


async def test_a_cleared_field_writes_nothing(
    user: User,
    writes: list,
    fitted: None,
) -> None:
    """Regression: a cleared ui.number yields None, which the server's
    _as_float raises on inside datachange_notification - the whole
    configuration is silently dropped while the dialog still says
    'configured'. The dialog must refuse before writing anything.
    """
    await user.open("/reactor/R0")
    user.find("Configure").click()
    await user.should_see("pwm0 control")

    user.find("Demand").clear()
    user.find("Apply").click()
    await user.should_see("is empty")

    assert writes == []


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
