"""Tests for the calibration OPC methods.

init_calibration_methods() is handed a stub node that captures the callables
instead of registering them, so no server is needed - the same pattern as
tests/test_opcua_pairing.py.

Deviation from the task-10 brief: the brief's Step 1 test code invokes the
captured callbacks with bare Python values, e.g.
``methods["calibrate_point"](None, 1000.0, 60.0)``. @uamethod's wrapper
branches on ``isinstance(parent, ua.NodeId)``; a bare ``None`` fails that
check and sends the call down the "bound method" branch instead, which then
tries ``.Value`` on the raw float arguments and raises AttributeError. This
is exactly the trap Task 0 of this plan was created to fix in six other
tests. tests/test_opcua_pairing.py is the corrected reference: a captured
callback is invoked with ``ua.NodeId()`` for the parent and
``ua.Variant(value)`` for each argument. _call() below follows that pattern,
and also awaits the result only when it is awaitable, since
calibrate_point's wrapper is async (the underlying method is a coroutine)
while the other five methods' wrappers are synchronous.
"""

from __future__ import annotations

import inspect
import json

import pytest
from asyncua import ua

from reactors_czlab.core.calibration import CALIBRATION_ENV
from reactors_czlab.core.data import MAX_OUTPUT
from reactors_czlab.opcua.actuator import ActuatorOpc


async def _call(method, *args):
    """Invoke a captured @uamethod callback the way the server would.

    @uamethod unwraps ua.Variant arguments and re-wraps the return value, so
    a direct call has to supply that shape and unpack the result. Only
    calibrate_point's wrapper is a coroutine function; the rest return
    their result directly, so the await is conditional.
    """
    result = method(ua.NodeId(), *(ua.Variant(a) for a in args))
    if inspect.isawaitable(result):
        result = await result
    return result[0].Value


class _CapturingNode:
    """Stand-in for an asyncua node that records added methods."""

    def __init__(self) -> None:
        self.methods: dict[str, object] = {}

    async def add_method(self, idx, name, callback, *args, **kwargs) -> None:
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


async def test_the_workflow_methods_are_registered(calibrating) -> None:
    """The operator's whole workflow is reachable from an OPC client.

    Six methods drive a run; get_calibration reads back what the run
    produced, which is otherwise Python-side state no client can see.
    """
    _, methods = calibrating

    assert set(methods) == {
        "calibrate_point",
        "record_point",
        "fit_calibration",
        "clear_points",
        "reload_calibration",
        "set_duties",
        "get_calibration",
    }


async def test_a_full_calibration_over_the_methods(calibrating) -> None:
    """Run two points, record them, fit, and the channel is calibrated."""
    node_opc, methods = calibrating

    status = await _call(methods["calibrate_point"], 1000.0, 60.0)
    assert "now record the measured" in status
    await _call(methods["record_point"], 10.0)
    await _call(methods["calibrate_point"], 3000.0, 60.0)
    await _call(methods["record_point"], 30.0)

    result = await _call(methods["fit_calibration"])

    assert "fitted" in result
    assert node_opc.actuator.channel.calibration.a == pytest.approx(0.01)


async def test_reload_reports_when_there_is_nothing_stored(
    calibrating,
) -> None:
    """A missing file is an operator-visible message, not an exception."""
    _, methods = calibrating

    result = await _call(methods["reload_calibration"])
    assert "no usable stored calibration" in result


async def test_clear_points_and_set_duties_status_strings_come_back(
    calibrating,
) -> None:
    """The remaining two methods also round-trip their status intact."""
    node_opc, methods = calibrating

    cleared = await _call(methods["clear_points"])
    assert node_opc.actuator.id in cleared

    # The channel carries an unfitted placeholder calibration, so
    # set_duties can still adjust it without a fit.
    status = await _call(methods["set_duties"], 500.0, 2000.0)
    assert status == "min duty 500.0, dispense duty 2000.0"


async def test_calibrate_point_holds_the_interlock_while_running(
    calibrating,
) -> None:
    """The interlock is set for the run's duration, not just at the end.

    A generic OPC client cannot see CalibrationRun directly, only the
    actuator node, so this exercises actuator.calibrating - the flag the
    rest of the reactor's loops check before touching a pump.
    """
    node_opc, methods = calibrating
    seen: dict[str, bool] = {}

    async def observe(seconds: float) -> None:
        seen["calibrating"] = node_opc.actuator.calibrating

    node_opc.run.sleep = observe

    assert node_opc.actuator.calibrating is False
    await _call(methods["calibrate_point"], 1000.0, 60.0)

    assert seen["calibrating"] is True
    assert node_opc.actuator.calibrating is False


async def test_calibrate_point_interlock_clears_even_if_the_run_raises(
    calibrating,
) -> None:
    """A pump left mid-run must still drop the interlock on failure."""
    node_opc, methods = calibrating

    async def boom(seconds: float) -> None:
        error_message = "simulated hardware fault"
        raise RuntimeError(error_message)

    node_opc.run.sleep = boom

    with pytest.raises(RuntimeError, match="simulated hardware fault"):
        await _call(methods["calibrate_point"], 1000.0, 60.0)

    assert node_opc.actuator.calibrating is False


class TestGetCalibration:
    """Reading back what a run produced.

    The screen needs the duty limits, the fit timestamp and the measured
    points behind the line. None of those are published variables - only
    cal_a, cal_b and cal_r2 are - so without this method an operator
    driving a calibration is working blind.
    """

    async def test_reports_run_state_before_any_points(
        self,
        calibrating,
    ) -> None:
        """An idle run with an unfitted placeholder is legible."""
        _, methods = calibrating

        state = json.loads(await _call(methods["get_calibration"]))

        assert state["actuator"] == "R0:pwm0"
        assert state["running"] is False
        assert state["pending"] is None
        assert state["run_points"] == []
        assert state["calibration"]["is_fitted"] is False

    async def test_a_pending_point_is_visible(self, calibrating) -> None:
        """The UI must know a measurement is owed before it offers Fit."""
        _, methods = calibrating

        await _call(methods["calibrate_point"], 1000.0, 60.0)
        state = json.loads(await _call(methods["get_calibration"]))

        assert state["pending"] == [1000.0, 60.0]
        assert state["run_points"] == []

    async def test_recorded_points_are_visible_before_the_fit(
        self,
        calibrating,
    ) -> None:
        """Collected points survive a page reload: they live server-side."""
        _, methods = calibrating

        await _call(methods["calibrate_point"], 1000.0, 60.0)
        await _call(methods["record_point"], 10.0)

        state = json.loads(await _call(methods["get_calibration"]))

        assert state["pending"] is None
        assert state["run_points"] == [[1000.0, 10.0]]

    async def test_reports_the_fitted_line_and_its_duties(
        self,
        calibrating,
    ) -> None:
        """After a fit, the screen can show the whole installed line."""
        _, methods = calibrating

        await _call(methods["calibrate_point"], 1000.0, 60.0)
        await _call(methods["record_point"], 10.0)
        await _call(methods["calibrate_point"], 3000.0, 60.0)
        await _call(methods["record_point"], 30.0)
        await _call(methods["fit_calibration"])

        cal = json.loads(await _call(methods["get_calibration"]))[
            "calibration"
        ]

        assert cal["is_fitted"] is True
        assert cal["fitted_at"]
        assert cal["a"] == pytest.approx(0.01)
        assert cal["points"] == [[1000.0, 10.0], [3000.0, 30.0]]
        assert cal["max_duty"] == pytest.approx(MAX_OUTPUT)
        assert cal["min_duty"] <= cal["dispense_duty"] <= cal["max_duty"]

    async def test_carries_the_installable_reason_verbatim(
        self,
        calibrating,
    ) -> None:
        """installable_reason() is the authority and is already worded.

        The screen shows it rather than deriving its own explanation of
        why a calibration cannot be used.
        """
        node_opc, methods = calibrating
        node_opc.actuator.channel.calibration.a = -1.0

        cal = json.loads(await _call(methods["get_calibration"]))[
            "calibration"
        ]

        assert cal["installable_reason"] is not None
        assert "slope" in cal["installable_reason"]

    async def test_an_actuator_with_no_slot_reports_null(
        self,
        make_actuator,
    ) -> None:
        """The MFCs have calibration=None; the screen must hide, not crash."""
        node_opc = ActuatorOpc(make_actuator("R0:mfc"))
        node_opc.node = _CapturingNode()
        await node_opc.init_calibration_methods(2)

        state = json.loads(
            await _call(node_opc.node.methods["get_calibration"]),
        )

        assert state["calibration"] is None
