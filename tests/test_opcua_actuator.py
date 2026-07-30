"""Tests for the actuator node's control-config plumbing.

The method and variable bodies are exercised directly against stub nodes:
asyncua is a base dependency but a running server is not needed.
"""

from __future__ import annotations

from reactors_czlab.core.control import _OnBoundariesControl, _PidControl
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


async def test_redundant_write_preserves_identity_in_flow_mode(
    make_calibrated_actuator,
) -> None:
    """A no-op OPC write in flow mode must not revert to duty.

    Regression: ``datachange_notification`` used to build
    ``ControlConfig`` with no ``output_unit``, which defaults to
    ``OutputUnit.duty``. Any unrelated write - even one that changes
    nothing meaningful - silently reverted a pump dosing in mL/min
    back to raw duty and rebuilt the controller, discarding a running
    PID's integral.
    """
    actuator = make_calibrated_actuator()
    node = ActuatorOpc(actuator)
    node.method = _StubVariable(0)  # manual
    node.value = _StubVariable(20.0)
    node.output_unit = _StubVariable(1)  # flow

    await node.datachange_notification(None, 0.0, None)
    assert actuator.dispenser.unit is OutputUnit.flow

    controller_before = actuator.controller
    dispenser_before = actuator.dispenser

    # Redundant write: identical method, value, and unit.
    await node.datachange_notification(None, 0.0, None)

    assert actuator.controller is controller_before
    assert actuator.dispenser is dispenser_before
    assert actuator.dispenser.unit is OutputUnit.flow


async def test_datachange_reads_pid_gains_backwards_and_band(
    make_calibrated_actuator,
) -> None:
    """A PID write carries gains, the backwards flag and the band."""
    actuator = make_calibrated_actuator()
    node = ActuatorOpc(actuator)
    node.method = _StubVariable(3)  # pid
    node.value = _StubVariable(0.0)
    node.output_unit = _StubVariable(0)  # duty
    node.setpoint = _StubVariable(7.0)
    node.kp = _StubVariable(5.0)
    node.ki = _StubVariable(2.0)
    node.kd = _StubVariable(1.0)
    node.backwards = _StubVariable(True)
    node.min_integral = _StubVariable(1.0)
    node.max_integral = _StubVariable(5.0)
    node.auto_integral_band = _StubVariable(False)

    await node.datachange_notification(None, 0.0, None)

    control = actuator.controller
    assert isinstance(control, _PidControl)
    assert (control.kp, control.ki, control.kd) == (5.0, 2.0, 1.0)
    assert control.backwards is True
    assert control._integral_band_is_default is False
    assert (control.min_integral, control.max_integral) == (1.0, 5.0)


async def test_datachange_reads_boundaries_backwards(
    make_calibrated_actuator,
) -> None:
    """An on_boundaries write carries the backwards flag."""
    actuator = make_calibrated_actuator()
    node = ActuatorOpc(actuator)
    node.method = _StubVariable(2)  # on_boundaries
    node.value = _StubVariable(150.0)
    node.output_unit = _StubVariable(0)  # duty
    node.lb = _StubVariable(1.0)
    node.ub = _StubVariable(2.0)
    node.backwards = _StubVariable(True)

    await node.datachange_notification(None, 0.0, None)

    control = actuator.controller
    assert isinstance(control, _OnBoundariesControl)
    assert control.backwards is True


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
