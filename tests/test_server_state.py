"""Durable server checkpoint validation and fail-safe recovery."""

from __future__ import annotations

import copy
import json
from collections import defaultdict

import pytest

from reactors_czlab.core.control import _PidControl, _TimerControl
from reactors_czlab.core.data import (
    ERROR_VALUE,
    ControlConfig,
    ControlMethod,
    OutputUnit,
)
from reactors_czlab.core.reactor import Reactor
from reactors_czlab.core.server_state import (
    StateStore,
    StateValidationError,
    apply_recovery,
    snapshot,
    validate_snapshot,
)


def _reactor(make_sensor, make_calibrated_actuator) -> Reactor:
    """Build the exact topology shared by checkpoint and restart."""
    return Reactor(
        "R0",
        5.0,
        [make_sensor("R0:ph")],
        [
            make_calibrated_actuator("R0:manual"),
            make_calibrated_actuator("R0:timer"),
            make_calibrated_actuator("R0:boundary"),
            make_calibrated_actuator("R0:pid"),
        ],
        10.0,
    )


def _configured_reactor(make_sensor, make_calibrated_actuator) -> Reactor:
    """Build one server carrying every controller method and output unit."""
    reactor = _reactor(make_sensor, make_calibrated_actuator)
    configs = {
        "R0:manual": ControlConfig(
            ControlMethod.manual,
            value=1.25,
            output_unit=OutputUnit.volume,
        ),
        "R0:timer": ControlConfig(
            ControlMethod.timer,
            time_on=4.0,
            time_off=6.0,
            value=12.0,
            output_unit=OutputUnit.flow,
        ),
        "R0:boundary": ControlConfig(
            ControlMethod.on_boundaries,
            lb=6.5,
            ub=7.5,
            value=900.0,
            backwards=True,
            output_unit=OutputUnit.duty,
        ),
        "R0:pid": ControlConfig(
            ControlMethod.pid,
            setpoint=7.1,
            kp=2.0,
            ki=0.3,
            kd=0.04,
            backwards=True,
            min_integral=0.2,
            max_integral=1.8,
            auto_integral_band=False,
            output_unit=OutputUnit.volume,
        ),
    }
    for actuator_id, config in configs.items():
        assert reactor.actuators[actuator_id].set_control_config(config) is None
    reactor.sampling.pairings = defaultdict(
        list,
        {
            "R0:ph": [
                ("R0:boundary", 0),
                ("R0:pid", 0),
            ],
        },
    )
    reactor.unpaired.actuators = ["R0:manual", "R0:timer"]
    for index, actuator in enumerate(reactor.actuators.values(), start=1):
        actuator.dispenser.total_volume = index + 0.25
    reactor.update_period(12.5)
    return reactor


def test_round_trip_restores_configuration_pairings_and_period_zeroing_totals(
    tmp_path,
    make_sensor,
    make_calibrated_actuator,
) -> None:
    """Every controller field survives; accrued totals restart at zero."""
    before = _configured_reactor(make_sensor, make_calibrated_actuator)
    store = StateStore(tmp_path / "state.json")
    assert store.checkpoint([before], 12.5) is True

    after = _reactor(make_sensor, make_calibrated_actuator)
    assert store.restore([after]) == 12.5

    assert after.period == 12.5
    assert dict(after.sampling.pairings) == {
        "R0:ph": [("R0:boundary", 0), ("R0:pid", 0)],
    }
    assert after.unpaired.actuators == ["R0:manual", "R0:timer"]
    # Accrued volume is durable only in PostgreSQL, never in the snapshot,
    # so every restored counter starts at zero regardless of what ran before.
    assert {actuator_id: actuator.dispenser.total_volume for actuator_id, actuator in after.actuators.items()} == {
        "R0:manual": 0.0,
        "R0:timer": 0.0,
        "R0:boundary": 0.0,
        "R0:pid": 0.0,
    }

    manual = after.actuators["R0:manual"]
    assert manual.controller.method is ControlMethod.manual
    assert manual.controller.value == 0.0
    assert manual.dispenser.unit is OutputUnit.volume

    timer = after.actuators["R0:timer"]
    assert isinstance(timer.controller, _TimerControl)
    assert (timer.controller.time_on, timer.controller.time_off) == (4.0, 6.0)
    assert timer.controller.value_on == 12.0
    assert timer.dispenser.unit is OutputUnit.flow

    boundary = after.actuators["R0:boundary"].controller
    assert (boundary.lower_bound, boundary.upper_bound) == (6.5, 7.5)
    assert boundary.backwards is True

    pid = after.actuators["R0:pid"].controller
    assert isinstance(pid, _PidControl)
    assert (pid.setpoint, pid.kp, pid.ki, pid.kd) == (7.1, 2.0, 0.3, 0.04)
    assert (pid.min_integral, pid.max_integral) == (0.2, 1.8)
    assert pid._integral_band_is_default is False
    assert pid._integral_sum == 0.0
    assert pid._last_measurement is None
    assert all(actuator.channel.value == 0.0 for actuator in after.actuators.values())


def test_unpaired_boundary_and_pid_restore_as_manual_zero(
    make_sensor,
    make_calibrated_actuator,
) -> None:
    """Measurement-dependent control cannot resume without a pairing."""
    reactor = _configured_reactor(make_sensor, make_calibrated_actuator)
    reactor.sampling.pairings.clear()
    reactor.unpaired.actuators = list(reactor.actuators)
    payload = snapshot([reactor], 12.5)

    restarted = _reactor(make_sensor, make_calibrated_actuator)
    apply_recovery(validate_snapshot(payload, [restarted]))

    for actuator_id in ("R0:boundary", "R0:pid"):
        controller = restarted.actuators[actuator_id].controller
        assert controller.method is ControlMethod.manual
        assert controller.value == 0.0


def test_paired_controller_holds_on_failed_read_then_arms_on_fresh_read(
    make_sensor,
    make_calibrated_actuator,
) -> None:
    """The checkpoint never turns the previous reading into a new decision."""
    reactor = _configured_reactor(make_sensor, make_calibrated_actuator)
    restarted = _reactor(make_sensor, make_calibrated_actuator)
    apply_recovery(validate_snapshot(snapshot([reactor], 12.5), [restarted]))
    actuator = restarted.actuators["R0:boundary"]
    sensor = restarted.sensors["R0:ph"]

    sensor.channels[0].value = ERROR_VALUE
    restarted.update_paired_actuators()
    assert actuator.channel.value == 0.0

    sensor.channels[0].value = 8.0
    restarted.update_paired_actuators()
    assert actuator.channel.value == 900.0


def test_paired_timer_starts_new_phase_only_after_successful_read(
    make_sensor,
    make_calibrated_actuator,
) -> None:
    """Even a timer paired to a probe is held until that probe reads once."""
    reactor = _configured_reactor(make_sensor, make_calibrated_actuator)
    reactor.sampling.pairings = defaultdict(list, {"R0:ph": [("R0:timer", 0)]})
    reactor.unpaired.actuators = [actuator_id for actuator_id in reactor.actuators if actuator_id != "R0:timer"]
    restarted = _reactor(make_sensor, make_calibrated_actuator)
    apply_recovery(validate_snapshot(snapshot([reactor], 12.5), [restarted]))
    actuator = restarted.actuators["R0:timer"]
    sensor = restarted.sensors["R0:ph"]

    sensor.channels[0].value = ERROR_VALUE
    restarted.update_paired_actuators()
    assert actuator.channel.value == 0.0
    assert "R0:timer" in restarted._recovery_paired_timers

    sensor.channels[0].value = 7.0
    restarted.update_paired_actuators()
    assert actuator.channel.value > 0.0
    assert "R0:timer" not in restarted._recovery_paired_timers


def test_unpaired_timer_waits_for_first_sampling_cycle(
    make_sensor,
    make_calibrated_actuator,
) -> None:
    """A restored timer begins a new ON phase only after sampling starts."""
    reactor = _configured_reactor(make_sensor, make_calibrated_actuator)
    restarted = _reactor(make_sensor, make_calibrated_actuator)
    apply_recovery(validate_snapshot(snapshot([reactor], 12.5), [restarted]))
    actuator = restarted.actuators["R0:timer"]

    assert "R0:timer" in restarted._recovery_unpaired_timers
    assert actuator.channel.value == 0.0
    restarted._arm_recovered_unpaired_timers()
    actuator.write_output(0.0)
    assert actuator.channel.value > 0.0


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (
            lambda payload: payload.update(schema_version=99),
            "unsupported schema version",
        ),
        (
            lambda payload: payload.update(sampling_period=float("nan")),
            "must be finite",
        ),
        (
            lambda payload: payload["reactors"][0]["sensors"][0].update(id="R0:missing"),
            "unknown id",
        ),
        (
            lambda payload: payload["reactors"][0]["pairings"].append({"sensor": "R0:ph", "actuator": "R0:boundary", "channel": 0}),
            "duplicate pairing",
        ),
        (
            lambda payload: payload["reactors"][0]["pairings"][0].update(channel=20),
            "outside sensor",
        ),
        (
            lambda payload: payload["reactors"][0]["actuators"][0]["control"].update(lb=9.0, ub=8.0),
            "lower boundary",
        ),
        (
            lambda payload: payload["reactors"][0]["actuators"][0].update(total_volume=1.0),
            "fields differ",
        ),
    ],
)
def test_invalid_snapshot_is_rejected_before_any_mutation(
    mutate,
    match,
    make_sensor,
    make_calibrated_actuator,
) -> None:
    """A failure anywhere leaves every live object at its safe defaults."""
    source = _configured_reactor(make_sensor, make_calibrated_actuator)
    payload = snapshot([source], 12.5)
    mutate(payload)
    restarted = _reactor(make_sensor, make_calibrated_actuator)

    with pytest.raises(StateValidationError, match=match):
        validate_snapshot(payload, [restarted])

    assert restarted.period == 10.0
    assert restarted.sampling.pairings == {}
    assert all(
        actuator.controller.method is ControlMethod.manual and actuator.controller.value == 0.0 and actuator.dispenser.total_volume == 0.0
        for actuator in restarted.actuators.values()
    )


def test_calibration_incompatible_snapshot_is_rejected(
    make_sensor,
    make_calibrated_actuator,
) -> None:
    """Flow/volume configs require the current calibration, not stale trust."""
    source = _configured_reactor(make_sensor, make_calibrated_actuator)
    payload = snapshot([source], 12.5)
    restarted = _reactor(make_sensor, make_calibrated_actuator)
    restarted.actuators["R0:pid"].channel.calibration.fitted_at = ""

    with pytest.raises(StateValidationError, match="current calibration"):
        validate_snapshot(payload, [restarted])


def test_truncated_snapshot_is_quarantined(tmp_path, make_sensor, make_actuator) -> None:
    """A torn JSON write cannot partially restore and remains inspectable."""
    path = tmp_path / "state.json"
    path.write_text('{"schema_version": 1,', encoding="utf-8")
    reactor = Reactor(
        "R0",
        5.0,
        [make_sensor("R0:ph")],
        [make_actuator("R0:pwm0")],
        10.0,
    )

    assert StateStore(path).restore([reactor]) is None

    assert not path.exists()
    assert len(list(tmp_path.glob("state.json.rejected-*"))) == 1
    assert reactor.actuators["R0:pwm0"].controller.value == 0.0


def test_interrupted_replacement_preserves_previous_checkpoint(
    tmp_path,
    make_sensor,
    make_calibrated_actuator,
    monkeypatch,
) -> None:
    """Failure before atomic replace leaves the prior complete JSON intact."""
    reactor = _configured_reactor(make_sensor, make_calibrated_actuator)
    path = tmp_path / "state.json"
    store = StateStore(path)
    assert store.checkpoint([reactor], 12.5) is True
    previous = json.loads(path.read_text(encoding="utf-8"))
    reactor.actuators["R0:pid"].dispenser.total_volume = 99.0

    def interrupted_replace(self, target):
        raise OSError("simulated outage")

    monkeypatch.setattr(type(path), "replace", interrupted_replace)
    assert store.checkpoint([reactor], 12.5) is False

    assert json.loads(path.read_text(encoding="utf-8")) == previous


def test_schema_v2_snapshot_omits_actuator_totals(
    make_sensor,
    make_calibrated_actuator,
) -> None:
    """A current snapshot is schema v2 and carries no per-pump total."""
    reactor = _configured_reactor(make_sensor, make_calibrated_actuator)
    payload = snapshot([reactor], 12.5)

    assert payload["schema_version"] == 2
    for actuator_row in payload["reactors"][0]["actuators"]:
        assert "total_volume" not in actuator_row


def test_schema_v1_configuration_restores_ignoring_its_totals(
    make_sensor,
    make_calibrated_actuator,
) -> None:
    """A legacy file still migrates its configuration; its totals are dropped."""
    source = _configured_reactor(make_sensor, make_calibrated_actuator)
    payload = snapshot([source], 12.5)
    # Rewrite the current snapshot back into the schema-v1 shape: bump the
    # version down and re-attach the per-actuator totals v1 carried.
    payload["schema_version"] = 1
    for actuator_row in payload["reactors"][0]["actuators"]:
        actuator_row["total_volume"] = 7.5

    restarted = _reactor(make_sensor, make_calibrated_actuator)
    apply_recovery(validate_snapshot(payload, [restarted]))

    boundary = restarted.actuators["R0:boundary"].controller
    assert (boundary.lower_bound, boundary.upper_bound) == (6.5, 7.5)
    assert all(actuator.dispenser.total_volume == 0.0 for actuator in restarted.actuators.values())


def test_validation_does_not_modify_its_payload(
    make_sensor,
    make_calibrated_actuator,
) -> None:
    """Validation normalises into a separate plan rather than editing JSON."""
    reactor = _configured_reactor(make_sensor, make_calibrated_actuator)
    payload = snapshot([reactor], 12.5)
    before = copy.deepcopy(payload)

    validate_snapshot(payload, [_reactor(make_sensor, make_calibrated_actuator)])

    assert payload == before
