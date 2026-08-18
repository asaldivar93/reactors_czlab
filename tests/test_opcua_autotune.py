"""Stub-node tests for the reactor-owned pH autotune OPC methods."""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import replace

import pytest
from asyncua import ua

from reactors_czlab.core.autotune import (
    AutotunePhase,
    AutotuneResult,
    RelayIdentification,
    RelayTuneConfig,
)
from reactors_czlab.core.autotune_audit import AutotuneAudit
from reactors_czlab.core.data import ControlConfig, ControlMethod, OutputUnit
from reactors_czlab.opcua.reactor import ReactorOpc


class _Variable:
    """Writable readback stub."""

    def __init__(self, value: object = 0.0) -> None:
        self.value = value
        self.writes: list[object] = []

    async def get_value(self) -> object:
        return self.value

    async def write_value(self, value: object) -> None:
        self.value = value
        self.writes.append(value)


class _MethodNode:
    """Capture complete method declarations without a running server."""

    def __init__(self) -> None:
        self.methods: dict[str, tuple[object, list[ua.Argument], list[ua.Argument]]] = {}

    async def add_method(
        self,
        idx: int,
        name: str,
        callback: object,
        inargs: list[ua.Argument],
        outargs: list[ua.Argument],
    ) -> None:
        self.methods[name] = (callback, inargs, outargs)


async def _call(method: object, *args: object) -> str:
    result = method(ua.NodeId(), *(ua.Variant(value) for value in args))
    if inspect.isawaitable(result):
        result = await result
    return result[0].Value


def _pid(actuator, *, backwards: bool, setpoint: float = 7.0) -> None:
    reason = actuator.set_control_config(
        ControlConfig(
            ControlMethod.pid,
            output_unit=OutputUnit.volume,
            setpoint=setpoint,
            kp=2.0,
            ki=0.02,
            kd=0.1,
            backwards=backwards,
        ),
    )
    assert reason is None


def _attach_readbacks(node) -> None:
    controller = node.actuator.controller
    values = {
        "method": 3,
        "output_unit": 2,
        "setpoint": getattr(controller, "setpoint", 0.0),
        "kp": getattr(controller, "kp", 100.0),
        "ki": getattr(controller, "ki", 0.01),
        "kd": getattr(controller, "kd", 0.0),
        "min_integral": getattr(controller, "min_integral", 0.0),
        "max_integral": getattr(controller, "max_integral", 4095.0),
        "auto_integral_band": getattr(
            controller,
            "_integral_band_is_default",
            True,
        ),
        "backwards": getattr(controller, "backwards", False),
    }
    for name, value in values.items():
        setattr(node, name, _Variable(value))


@pytest.fixture
async def opc_autotune(
    tmp_path,
    make_sensor,
    make_calibrated_actuator,
):
    sensor = make_sensor("R0:ph", value=7.0)
    base = make_calibrated_actuator("R0:base")
    acid = make_calibrated_actuator("R0:acid")
    other = make_calibrated_actuator("R0:other")
    _pid(base, backwards=False)
    _pid(acid, backwards=True)
    audit = AutotuneAudit("R0", directory=lambda: tmp_path)
    reactor_opc = ReactorOpc(
        "R0",
        volume=5.0,
        sensors=[sensor],
        actuators=[base, acid, other],
        period=10.0,
        autotune_audit=audit,
    )
    reactor = reactor_opc.reactor
    reactor.sampling.pairings[sensor.id].extend(
        [(base.id, 0), (acid.id, 0)],
    )
    reactor.unpaired.actuators.remove(base.id)
    reactor.unpaired.actuators.remove(acid.id)
    captures: dict[str, _MethodNode] = {}
    for node in reactor_opc.actuator_nodes:
        capture = _MethodNode()
        node.node = capture
        captures[node.id] = capture
        _attach_readbacks(node)
    await reactor_opc.init_autotune_methods(2)
    return reactor_opc, captures, base, acid, other


def _config() -> RelayTuneConfig:
    return RelayTuneConfig(
        setpoint=7.0,
        base_dose_ml=0.2,
        acid_dose_ml=0.2,
        hysteresis=0.02,
        max_minutes=30.0,
        phosphate_molar=0.014,
        base_molar=0.5,
        acid_molar=0.5,
        acknowledge_other_loops=True,
    )


def _identified(reactor_opc: ReactorOpc) -> None:
    run = reactor_opc.autotune_api.coordinator.start(
        "R0:ph",
        "R0:base",
        "R0:acid",
        _config(),
    )
    for actuator in (run.base, run.acid):
        actuator.release_autotune(run)
    run._claimed.clear()
    run.phase = AutotunePhase.identified
    run.message = "relay identification complete"
    run.result = AutotuneResult(
        RelayIdentification(18.6, 293.0, 0.05, 7.0, 4),
        noise_sigma=0.001,
        base_dose_ml=0.2,
        acid_dose_ml=0.2,
        actual_dose_ml=1.0,
        cycles=(),
    )


def test_method_declarations_match_the_public_contract(opc_autotune) -> None:
    reactor_opc, captures, *_ = opc_autotune
    methods = captures["R0:base"].methods
    expected = {
        "autotune_preflight",
        "autotune_start",
        "autotune_status",
        "autotune_abort",
        "autotune_apply",
        "autotune_scale_to_setpoint",
        "autotune_reapply_last",
    }
    assert {name.rsplit(":", 1)[-1] for name in methods} == expected
    _, start_args, start_out = methods["R0:base:autotune_start"]
    assert [item.Name for item in start_args] == [
        "Sensor_id",
        "Acid_id",
        "Setpoint",
        "Base_dose_ml",
        "Acid_dose_ml",
        "Hysteresis_ph",
        "Max_minutes",
        "Phosphate_molar",
        "Base_molar",
        "Acid_molar",
        "Dose_budget_ml",
        "Acknowledge_other_loops",
        "Acknowledge_budget_override",
    ]
    assert start_args[0].DataType.Identifier == ua.ObjectIds.String
    assert start_args[2].DataType.Identifier == ua.ObjectIds.Double
    assert start_args[-1].DataType.Identifier == ua.ObjectIds.Boolean
    assert start_out[0].DataType.Identifier == ua.ObjectIds.String
    assert reactor_opc.autotune_api.actuator_nodes["R0:base"] is reactor_opc.actuator_nodes[0]


def test_preflight_selection_validation_and_zero_default_budget(opc_autotune) -> None:
    reactor_opc, _, base, *_ = opc_autotune
    api = reactor_opc.autotune_api
    accepted = json.loads(api.preflight(base.id, "R0:ph", "R0:acid", _config()))
    rejected = json.loads(api.preflight(base.id, "R9:ph", "R0:acid", _config()))

    assert accepted["ok"] is True
    assert accepted["phase"] == "idle"
    assert accepted["safety"]["default_dose_budget_ml"] > 0
    assert rejected["ok"] is False
    assert "belong" in rejected["message"] or "unknown" in rejected["message"]
    assert api._config(7, 0.2, 0.2, 0.02, 30, 0.014, 0.5, 0.5, 0, True, False).dose_budget_ml is None


async def test_start_status_and_abort_are_callable_through_stub_node(opc_autotune) -> None:
    _, captures, base, acid, _ = opc_autotune
    methods = captures[base.id].methods
    common = (
        "R0:ph",
        acid.id,
        7.0,
        0.2,
        0.2,
        0.02,
        30.0,
        0.014,
        0.5,
        0.5,
        0.0,
        True,
        False,
    )

    started = json.loads(await _call(methods[f"{base.id}:autotune_start"][0], *common))
    status = json.loads(await _call(methods[f"{base.id}:autotune_status"][0]))
    aborted = json.loads(await _call(methods[f"{base.id}:autotune_abort"][0]))

    assert started["phase"] == status["phase"] == "baseline"
    assert aborted["phase"] == "aborted"


def test_status_json_is_versioned_bounded_and_contains_candidates(opc_autotune) -> None:
    reactor_opc, *_ = opc_autotune
    _identified(reactor_opc)

    payload = json.loads(reactor_opc.autotune_api.status())

    assert payload["version"] == 1
    assert payload["ok"] is True
    assert payload["phase"] == "identified"
    assert payload["selection"]["base_id"] == "R0:base"
    assert payload["setpoint"] == 7.0
    assert payload["hysteresis_ph"] == 0.02
    assert payload["chemistry"] == {
        "phosphate_molar": 0.014,
        "base_molar": 0.5,
        "acid_molar": 0.5,
    }
    assert payload["max_minutes"] == 30.0
    assert payload["relay_direction"] == "none"
    assert payload["adjusted_boluses_ml"] == payload["adjusted_doses_ml"]
    assert set(payload["candidate_gains"]) == {"ZN-PID", "TL-PI", "TL-PID", "SIMC"}
    assert len(payload["trace"]) <= 240
    assert len(payload["cycles"]) <= 12


def test_abort_is_base_owned_and_releases_both_pumps(opc_autotune) -> None:
    reactor_opc, _, base, acid, _ = opc_autotune
    api = reactor_opc.autotune_api
    api.coordinator.start("R0:ph", base.id, acid.id, _config())

    wrong = json.loads(api.abort(acid.id))
    stopped = json.loads(api.abort(base.id))

    assert wrong["ok"] is False
    assert stopped["phase"] == "aborted"
    assert base.autotune_owner is acid.autotune_owner is None


def test_required_pairing_loss_aborts_on_the_next_fresh_sample(opc_autotune) -> None:
    reactor_opc, _, base, acid, _ = opc_autotune
    api = reactor_opc.autotune_api
    run = api.coordinator.start("R0:ph", base.id, acid.id, _config())
    reactor_opc.reactor.sampling.pairings["R0:ph"].remove((acid.id, 0))

    reactor_opc.reactor.update_autotune()

    assert run.phase is AutotunePhase.aborted
    assert "pairing loss" in run.message
    assert base.autotune_owner is acid.autotune_owner is None


async def test_selected_control_change_aborts_before_readback_await(opc_autotune) -> None:
    reactor_opc, _, base, acid, _ = opc_autotune
    api = reactor_opc.autotune_api
    run = api.coordinator.start("R0:ph", base.id, acid.id, _config())
    base_node = next(node for node in reactor_opc.actuator_nodes if node.id == base.id)

    accepted, _ = await base_node.apply_control_config(
        3,
        2,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        7.0,
        3.0,
        0.02,
        0.1,
        0.0,
        4095.0,
        True,
        False,
    )

    assert accepted is True
    assert run.phase is AutotunePhase.aborted
    assert "configuration changed" in run.message
    assert base.autotune_owner is acid.autotune_owner is None


async def test_identical_selected_control_write_does_not_abort(opc_autotune) -> None:
    """An unrelated client readback round-trip remains an inert no-op."""
    reactor_opc, _, base, acid, _ = opc_autotune
    api = reactor_opc.autotune_api
    run = api.coordinator.start("R0:ph", base.id, acid.id, _config())
    base_node = next(node for node in reactor_opc.actuator_nodes if node.id == base.id)

    accepted, _ = await base_node.apply_control_config(
        3,
        2,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        7.0,
        2.0,
        0.02,
        0.1,
        0.0,
        4095.0,
        True,
        False,
    )

    assert accepted is True
    assert run.is_active
    assert base.autotune_owner is acid.autotune_owner is run
    run.abort()


async def test_atomic_dual_apply_preserves_runtime_and_publishes_both(opc_autotune) -> None:
    reactor_opc, _, base, acid, _ = opc_autotune
    _identified(reactor_opc)
    base_controller = base.controller
    acid_controller = acid.controller
    base_controller._integral_sum = 3.0
    acid_controller._integral_sum = 2.0

    result = json.loads(await reactor_opc.autotune_api.apply(base.id, "TL-PI"))

    assert result["ok"] is True
    assert base.controller is base_controller
    assert acid.controller is acid_controller
    assert base_controller._integral_sum == 3.0
    assert acid_controller._integral_sum == 2.0
    assert (base.controller.kp, base.controller.ki, base.controller.kd) == pytest.approx(
        (acid.controller.kp, acid.controller.ki, acid.controller.kd),
    )
    for node in reactor_opc.actuator_nodes[:2]:
        assert node.kp.writes and node.ki.writes and node.kd.writes


async def test_apply_rejects_without_identification(opc_autotune) -> None:
    reactor_opc, _, base, *_ = opc_autotune

    result = json.loads(await reactor_opc.autotune_api.apply(base.id, "TL-PI"))

    assert result["ok"] is False
    assert "identified" in result["message"]


async def test_unexpected_partial_apply_rolls_back_first_pump(
    opc_autotune,
    monkeypatch,
) -> None:
    reactor_opc, _, base, acid, _ = opc_autotune
    _identified(reactor_opc)
    original = (base.controller.kp, base.controller.ki, base.controller.kd)
    base_set = base.set_control_config

    def reject_new(config: ControlConfig) -> str | None:
        if (config.kp, config.ki, config.kd) != original:
            return "injected second-pump refusal"
        return base_set(config)

    # Stable id order is acid then base, so acid really is mutated before
    # the injected base refusal exercises rollback of a partial apply.
    monkeypatch.setattr(base, "set_control_config", reject_new)

    result = json.loads(await reactor_opc.autotune_api.apply(base.id, "TL-PI"))

    assert result["ok"] is False
    assert "atomic" in result["message"]
    assert (base.controller.kp, base.controller.ki, base.controller.kd) == original
    assert (acid.controller.kp, acid.controller.ki, acid.controller.kd) == original


async def test_scaling_and_reapply_use_the_persisted_selection(opc_autotune) -> None:
    reactor_opc, _, base, acid, _ = opc_autotune
    api = reactor_opc.autotune_api
    _identified(reactor_opc)
    assert json.loads(await api.apply(base.id, "TL-PI"))["ok"] is True
    applied = (base.controller.kp, base.controller.ki, base.controller.kd)
    for actuator in (base, acid):
        current = actuator.controller
        actuator.set_control_config(
            ControlConfig(
                ControlMethod.pid,
                output_unit=OutputUnit.volume,
                setpoint=6.8,
                kp=current.kp,
                ki=current.ki,
                kd=current.kd,
                backwards=current.backwards,
            ),
        )

    scaled = json.loads(await api.scale(base.id, 6.8))
    scaled_gains = (base.controller.kp, base.controller.ki, base.controller.kd)
    assert scaled["ok"] is True
    assert scaled_gains != pytest.approx(applied)

    for actuator in (base, acid):
        config = api._pid_config(
            next(node for node in reactor_opc.actuator_nodes if node.id == actuator.id),
        )
        actuator.set_control_config(replace(config, kp=1.0, ki=1.0, kd=1.0))
    reapplied = json.loads(await api.reapply(base.id))

    assert reapplied["ok"] is True
    assert (base.controller.kp, base.controller.ki, base.controller.kd) == pytest.approx(scaled_gains)
    assert (acid.controller.kp, acid.controller.ki, acid.controller.kd) == pytest.approx(scaled_gains)


async def test_reapply_refuses_to_mutate_controllers_during_a_new_run(
    opc_autotune,
    monkeypatch,
) -> None:
    """Persisted gains cannot be pushed into controllers owned by a live run."""
    reactor_opc, _, base, acid, _ = opc_autotune
    api = reactor_opc.autotune_api
    _identified(reactor_opc)
    assert json.loads(await api.apply(base.id, "TL-PI"))["ok"] is True
    active = api.coordinator.start("R0:ph", base.id, acid.id, _config())
    calls: list[str] = []
    for actuator in (base, acid):
        original = actuator.set_control_config

        def record(config, *, identifier=actuator.id, apply_config=original):
            calls.append(identifier)
            return apply_config(config)

        monkeypatch.setattr(actuator, "set_control_config", record)

    result = json.loads(await api.reapply(base.id))

    assert result["ok"] is False
    assert "while a run is active" in result["message"]
    assert calls == []
    assert active.is_active
    assert base.autotune_owner is acid.autotune_owner is active


async def test_pending_gain_transaction_synchronously_refuses_start(
    opc_autotune,
) -> None:
    """Start cannot claim pumps while reapply waits for a config lock."""
    reactor_opc, _, base, acid, _ = opc_autotune
    api = reactor_opc.autotune_api
    _identified(reactor_opc)
    assert json.loads(await api.apply(base.id, "TL-PI"))["ok"] is True
    base_node = next(
        node for node in reactor_opc.actuator_nodes if node.id == base.id
    )
    await base_node._config_lock.acquire()
    transaction = asyncio.create_task(api.reapply(base.id))
    try:
        await asyncio.sleep(0)
        assert api._gain_change_in_progress is True
        assert transaction.done() is False

        concurrent = json.loads(await api.reapply(base.id))
        assert concurrent["ok"] is False
        assert "already in progress" in concurrent["message"]

        refused = json.loads(
            api.start(base.id, "R0:ph", acid.id, _config()),
        )

        assert refused["ok"] is False
        assert "gain change is in progress" in refused["message"]
        assert base.autotune_owner is acid.autotune_owner is None
    finally:
        base_node._config_lock.release()

    completed = json.loads(await asyncio.wait_for(transaction, timeout=1.0))
    assert completed["ok"] is True
    assert api._gain_change_in_progress is False
    assert base.autotune_owner is acid.autotune_owner is None


def test_autotune_method_names_do_not_change_archived_browse_names(opc_autotune) -> None:
    reactor_opc, captures, *_ = opc_autotune
    existing = {
        f"{node.id}:{channel}"
        for node in reactor_opc.actuator_nodes
        for channel in ("curr_value", "total_volume", "cal_a", "cal_b", "cal_r2")
    }
    assert all(name.count(":") == 2 for name in existing)
    assert all(
        full_name.startswith(f"{node.id}:autotune_")
        for node in reactor_opc.actuator_nodes
        for full_name in captures[node.id].methods
    )
