"""Tests for actuator OPC control configuration and read-back values."""

from __future__ import annotations

import json

from reactors_czlab.core.control import (
    _PidControl,
    _TimerControl,
)
from reactors_czlab.core.data import ControlConfig, ControlMethod, OutputUnit
from reactors_czlab.opcua.actuator import ActuatorOpc, output_unit_map


class _StubVariable:
    """An asyncua variable that records writes."""

    def __init__(self, value: object) -> None:
        self.value = value
        self.writes: list[object] = []

    async def get_value(self) -> object:
        """Read the held value."""
        return self.value

    async def write_value(self, value: object) -> None:
        """Store and record a written value."""
        self.value = value
        self.writes.append(value)


def _attach_readback(node: ActuatorOpc) -> None:
    """Install read-back variables without a running OPC server."""
    for name, value in {
        "method": 0,
        "output_unit": 0,
        "value": 0.0,
        "time_on": 0.0,
        "time_off": 0.0,
        "lb": 0.0,
        "ub": 0.0,
        "setpoint": 0.0,
        "kp": 100.0,
        "ki": 0.01,
        "kd": 0.0,
        "min_integral": 0.0,
        "max_integral": 4095.0,
        "auto_integral_band": True,
        "backwards": False,
    }.items():
        setattr(node, name, _StubVariable(value))


async def _apply(
    node: ActuatorOpc,
    *,
    method: int = 0,
    unit: int = 0,
    value: object = 0.0,
    time_on: object = 0.0,
    time_off: object = 0.0,
    lb: object = 0.0,
    ub: object = 0.0,
    setpoint: object = 0.0,
    kp: object = 100.0,
    ki: object = 0.01,
    kd: object = 0.0,
    min_integral: object = 0.0,
    max_integral: object = 4095.0,
    auto_integral_band: object = True,
    backwards: object = False,
) -> tuple[bool, str]:
    """Call the atomic method with named test inputs."""
    return await node.apply_control_config(
        method,
        unit,
        value,
        time_on,
        time_off,
        lb,
        ub,
        setpoint,
        kp,
        ki,
        kd,
        min_integral,
        max_integral,
        auto_integral_band,
        backwards,
    )


def test_output_unit_map_covers_every_unit() -> None:
    """The OPC enum and the Python enum must not drift apart."""
    assert set(output_unit_map.values()) == set(OutputUnit)


async def test_same_method_retune_applies_exactly_one_config(
    make_calibrated_actuator,
    monkeypatch,
) -> None:
    """One Apply cannot expose a half-retuned controller.

    Regression: sequential variable writes applied a new PID setpoint with
    the old gain before a second notification installed the intended gain.
    """
    actuator = make_calibrated_actuator()
    actuator.set_control_config(
        ControlConfig(ControlMethod.pid, setpoint=7.0, kp=100.0),
    )
    node = ActuatorOpc(actuator)
    _attach_readback(node)
    calls: list[ControlConfig] = []
    original = actuator.set_control_config

    def record(config: ControlConfig) -> str | None:
        calls.append(config)
        return original(config)

    monkeypatch.setattr(actuator, "set_control_config", record)

    accepted, _ = await _apply(node, method=3, setpoint=8.0, kp=50.0)

    assert accepted is True
    assert len(calls) == 1
    assert (calls[0].setpoint, calls[0].kp) == (8.0, 50.0)


async def test_boundaries_swap_never_exposes_an_inverted_band(
    make_calibrated_actuator,
    monkeypatch,
) -> None:
    """Both boundary values reach the actuator in the same object."""
    actuator = make_calibrated_actuator()
    actuator.set_control_config(
        ControlConfig(ControlMethod.on_boundaries, lb=6.0, ub=8.0),
    )
    node = ActuatorOpc(actuator)
    _attach_readback(node)
    calls: list[ControlConfig] = []
    original = actuator.set_control_config

    def record(config: ControlConfig) -> str | None:
        calls.append(config)
        return original(config)

    monkeypatch.setattr(actuator, "set_control_config", record)
    accepted, _ = await _apply(node, method=2, lb=9.0, ub=11.0)

    assert accepted is True
    assert [(config.lb, config.ub) for config in calls] == [(9.0, 11.0)]
    assert all(config.lb <= config.ub for config in calls)


async def test_invalid_enum_leaves_the_controller_unchanged(
    make_calibrated_actuator,
) -> None:
    """An unknown enum is rejected before the actuator is touched."""
    actuator = make_calibrated_actuator()
    node = ActuatorOpc(actuator)
    _attach_readback(node)
    before = actuator.controller

    accepted, message = await _apply(node, method=99)

    assert accepted is False
    assert "99" in message
    assert actuator.controller is before
    assert node.method.writes == []


async def test_invalid_output_unit_leaves_the_controller_unchanged(
    make_calibrated_actuator,
) -> None:
    """Unit indices are validated independently of method indices."""
    actuator = make_calibrated_actuator()
    node = ActuatorOpc(actuator)
    _attach_readback(node)
    before = actuator.controller

    accepted, message = await _apply(node, unit=99)

    assert accepted is False
    assert "99" in message
    assert actuator.controller is before
    assert node.output_unit.writes == []


async def test_rejected_unit_leaves_the_controller_unchanged(
    make_actuator,
) -> None:
    """The server's calibration reason comes back to the caller."""
    actuator = make_actuator()
    node = ActuatorOpc(actuator)
    _attach_readback(node)
    before = actuator.controller

    accepted, message = await _apply(node, unit=1, value=10.0)

    assert accepted is False
    assert "calibration" in message
    assert actuator.controller is before
    assert node.output_unit.writes == []


async def test_bad_field_type_leaves_the_controller_unchanged(
    make_calibrated_actuator,
) -> None:
    """A field validation failure is an ordinary method rejection."""
    actuator = make_calibrated_actuator()
    node = ActuatorOpc(actuator)
    _attach_readback(node)
    before = actuator.controller

    accepted, message = await _apply(node, value=[])

    assert accepted is False
    assert "must be a number" in message
    assert actuator.controller is before


async def test_same_method_pid_tuning_preserves_runtime_state(
    make_calibrated_actuator,
) -> None:
    """Atomic transport still uses the actuator's in-place adoption."""
    actuator = make_calibrated_actuator()
    actuator.set_control_config(
        ControlConfig(ControlMethod.pid, setpoint=7.0, kp=100.0),
    )
    controller = actuator.controller
    assert isinstance(controller, _PidControl)
    controller._integral_sum = 12.0
    node = ActuatorOpc(actuator)
    _attach_readback(node)

    accepted, _ = await _apply(node, method=3, setpoint=8.0, kp=50.0)

    assert accepted is True
    assert actuator.controller is controller
    assert controller._integral_sum == 12.0


async def test_accepted_config_updates_only_its_readback_fields(
    make_calibrated_actuator,
) -> None:
    """Read-back changes only after the whole config was accepted."""
    actuator = make_calibrated_actuator()
    node = ActuatorOpc(actuator)
    _attach_readback(node)

    accepted, _ = await _apply(
        node,
        method=2,
        value=500.0,
        lb=6.0,
        ub=8.0,
        backwards=True,
        kp=999.0,
    )

    assert accepted is True
    assert node.method.writes == [2]
    assert node.lb.writes == [6.0]
    assert node.ub.writes == [8.0]
    assert node.backwards.writes == [True]
    assert node.kp.writes == []


async def test_same_method_timer_tuning_preserves_phase(
    make_calibrated_actuator,
) -> None:
    """A timer retune does not restart its current phase."""
    actuator = make_calibrated_actuator()
    actuator.set_control_config(
        ControlConfig(
            ControlMethod.timer,
            time_on=2.0,
            time_off=3.0,
            value=100.0,
        ),
    )
    controller = actuator.controller
    assert isinstance(controller, _TimerControl)
    controller._is_on = False
    last_time = controller._last_time
    node = ActuatorOpc(actuator)
    _attach_readback(node)

    accepted, _ = await _apply(
        node,
        method=1,
        value=200.0,
        time_on=4.0,
        time_off=5.0,
    )

    assert accepted is True
    assert actuator.controller is controller
    assert controller._is_on is False
    assert controller._last_time == last_time


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


async def test_update_value_skips_unchanged_pump_values(
    make_calibrated_actuator,
) -> None:
    """Unchanged totals and fit values cost no server writes."""
    actuator = make_calibrated_actuator()
    node = ActuatorOpc(actuator)
    node.curr_value = _StubVariable(0.0)
    node.total_volume = _StubVariable(0.0)
    node.cal_a = _StubVariable(0.01)
    node.cal_b = _StubVariable(0.0)
    node.cal_r2 = _StubVariable(1.0)

    await node.update_value()

    assert node.total_volume.writes == []
    assert node.cal_a.writes == []
    assert node.cal_b.writes == []
    assert node.cal_r2.writes == []


def test_get_control_config_round_trips_every_pid_field(
    make_calibrated_actuator,
) -> None:
    """The on-demand payload mirrors the running object, not stale nodes."""
    actuator = make_calibrated_actuator()
    actuator.set_control_config(
        ControlConfig(
            ControlMethod.pid,
            output_unit=OutputUnit.flow,
            setpoint=7.1,
            kp=2.0,
            ki=3.0,
            kd=4.0,
            backwards=True,
            auto_integral_band=False,
            min_integral=1.0,
            max_integral=5.0,
        ),
    )
    state = json.loads(ActuatorOpc(actuator).control_config_json())

    assert state["method"] == "pid"
    assert state["output_unit"] == "flow"
    assert state["setpoint"] == 7.1
    assert state["demand"] == 0.0
    assert (state["kp"], state["ki"], state["kd"]) == (2.0, 3.0, 4.0)
    assert state["backwards"] is True
    assert state["auto_integral_band"] is False
    assert (state["min_integral"], state["max_integral"]) == (1.0, 5.0)


def test_get_control_config_lists_server_enum_options(
    make_calibrated_actuator,
) -> None:
    """The GUI derives index-to-name mappings from the server itself."""
    state = json.loads(
        ActuatorOpc(make_calibrated_actuator()).control_config_json(),
    )

    assert state["methods"] == ["manual", "timer", "on_boundaries", "pid"]
    assert state["output_units"] == ["duty", "flow", "volume"]
