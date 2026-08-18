"""End-to-end numerical and lifecycle validation for pH PID autotuning."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
import pytest

from reactors_czlab.core.actuator import RandomActuator
from reactors_czlab.core.autotune import (
    AutotuneContext,
    AutotuneCoordinator,
    AutotunePhase,
    Pump,
    RelayTuneConfig,
    SplitRangeConfig,
    SplitRangeController,
    run_relay_experiment,
    scale_gains_to_setpoint,
    simulate,
    simulation_metrics,
    to_code_gains,
    tuning_rules,
)
from reactors_czlab.core.autotune_audit import AutotuneAudit
from reactors_czlab.core.calibration import CalibrationRun
from reactors_czlab.core.data import (
    Calibration,
    Channel,
    ControlConfig,
    ControlMethod,
    OutputUnit,
    PhysicalInfo,
    PlcOutput,
)
from reactors_czlab.core.dispenser import Dispenser
from reactors_czlab.core.ph_model import PhPlant, PlantParams
from reactors_czlab.core.reactor import Reactor
from reactors_czlab.gui.address import AddressBook
from reactors_czlab.gui.controllers.autotune import ViewMode, run_from_payload
from reactors_czlab.gui.state import AppState
from reactors_czlab.opcua.actuator import ActuatorOpc
from reactors_czlab.opcua.autotune import ReactorAutotuneOpc
from reactors_czlab.opcua.client import OpcClient
from reactors_czlab.opcua.reactor import ReactorOpc

REFERENCE_DT = 10.0
REFERENCE_DEAD_TIME = 10.0
REFERENCE_NOISE = 0.005
REFERENCE_METABOLIC_LOAD = 2e-7
FAST_TICK = 0.05


class _Clock:
    """A deterministic monotonic clock shared by runs and dispensers."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = round(self.now + seconds, 12)


class _Sensor:
    """Hardware-free single-channel pH sensor."""

    def __init__(self, identifier: str = "R0:ph", value: float = 7.0) -> None:
        self.id = identifier
        self.channels = [Channel("pH", "pH", register="pmc1")]
        self.channels[0].value = value
        self.reads = 0

    async def read(self) -> None:
        self.reads += 1


def _pump(identifier: str, clock: _Clock) -> RandomActuator:
    """Build a fitted 20 mL/min pump without importing sensor hardware."""
    calibration = Calibration(
        identifier.replace(":", "_"),
        a=0.01,
        b=0.0,
        min_duty=400.0,
        max_duty=4000.0,
        dispense_duty=2000.0,
        points=[(500.0, 5.0), (2500.0, 25.0)],
        fitted_at="2026-08-17T00:00:00+00:00",
        r2=1.0,
    )
    info = PhysicalInfo(
        model="pwm",
        address=0,
        type=PlcOutput.pwm,
        channels=[
            Channel("pwm0", "pwm", pin="Q2.7", calibration=calibration),
        ],
    )
    return RandomActuator(identifier, info)


def _configure_pair(
    clock: _Clock,
    *,
    period: float,
) -> tuple[RandomActuator, RandomActuator]:
    base = _pump("R0:base", clock)
    acid = _pump("R0:acid", clock)
    for actuator, backwards in ((base, False), (acid, True)):
        actuator.control_period = period
        reason = actuator.set_control_config(
            ControlConfig(
                ControlMethod.pid,
                output_unit=OutputUnit.volume,
                setpoint=7.0,
                backwards=backwards,
            ),
        )
        assert reason is None
        actuator.dispenser = Dispenser(
            OutputUnit.volume,
            actuator.channel,
            period,
            clock=clock,
        )
    return base, acid


@dataclass
class _PlantHarness:
    """A real core run connected to the packaged plant and fake hardware."""

    clock: _Clock
    plant: PhPlant
    run: object
    base: RandomActuator
    acid: RandomActuator


def _plant_harness(
    *,
    period: float = REFERENCE_DT,
    dose_ml: float = 0.30,
    max_minutes: float = 60.0,
) -> _PlantHarness:
    clock = _Clock()
    base, acid = _configure_pair(clock, period=period)
    sensor = _Sensor()
    pairings = {sensor.id: [(base.id, 0), (acid.id, 0)]}
    context = AutotuneContext(
        "R0",
        5.0,
        {sensor.id: sensor},
        {base.id: base, acid.id: acid},
        lambda: pairings,
    )
    config = RelayTuneConfig(
        setpoint=7.0,
        base_dose_ml=dose_ml,
        acid_dose_ml=dose_ml,
        hysteresis=0.02,
        dt=period,
        dead_time=REFERENCE_DEAD_TIME,
        max_minutes=max_minutes,
        phosphate_molar=0.014,
        base_molar=0.5,
        acid_molar=0.5,
        acknowledge_other_loops=True,
    )
    run = AutotuneCoordinator(context, clock=clock).start(
        sensor.id,
        base.id,
        acid.id,
        config,
    )
    plant = PhPlant(
        PlantParams(
            V0=5.0,
            C_P0=0.014,
            pH0=7.0,
            c_base=0.5,
            c_acid=0.5,
        ),
    )
    return _PlantHarness(clock, plant, run, base, acid)


def _advance_owned_deliveries(harness: _PlantHarness, period: float) -> None:
    """Mirror the 20 Hz reactor actuator loop for one sample period."""
    remaining = period
    while remaining > 1e-12:
        step = min(FAST_TICK, remaining)
        harness.clock.advance(step)
        harness.run.tick()
        remaining -= step


def _drive_plant(
    harness: _PlantHarness,
    *,
    period: float = REFERENCE_DT,
    noise: float = REFERENCE_NOISE,
    seed: int = 0,
    metabolic_load: float = REFERENCE_METABOLIC_LOAD,
    max_steps: int = 4000,
) -> None:
    """Drive a live sample/tick workflow from the packaged plant."""
    rng = np.random.default_rng(seed)
    delay = max(0, round(REFERENCE_DEAD_TIME / period))
    measurements = [harness.plant.pH] * (delay + 1)

    for _ in range(max_steps):
        true_ph = harness.plant.pH
        measurements.append(true_ph + rng.normal(0.0, noise))
        measured_ph = measurements.pop(0)
        base_before = harness.base.dispenser.total_volume
        acid_before = harness.acid.dispenser.total_volume

        harness.run.sample(measured_ph, harness.clock())
        _advance_owned_deliveries(harness, period)

        delivered_base = harness.base.dispenser.total_volume - base_before
        delivered_acid = harness.acid.dispenser.total_volume - acid_before
        harness.plant.step(
            q_base=delivered_base / 1000.0 / period,
            q_acid=delivered_acid / 1000.0 / period,
            dt=period,
            r_metabolic=metabolic_load,
        )
        if not harness.run.is_active:
            return
    pytest.fail(f"autotune did not terminate after {max_steps} plant samples")


@pytest.fixture(scope="module")
def reference_run():
    """Identify once through real ownership, delivery, and plant dynamics."""
    harness = _plant_harness()
    _drive_plant(harness)
    assert harness.run.phase is AutotunePhase.identified, harness.run.message
    return harness.run


@pytest.fixture(scope="module")
def reference_gains(reference_run) -> tuple[float, float, float]:
    identification = reference_run.result.identification
    return to_code_gains(
        *tuning_rules(identification.Ku, identification.Pu)["TL-PI"],
    )


def test_live_reference_run_identifies_without_spurious_adaptation(
    reference_run,
) -> None:
    """The accepted 0.30 mL PhPlant fixture crosses every core layer."""
    identification = reference_run.result.identification
    gains = to_code_gains(
        *tuning_rules(identification.Ku, identification.Pu)["TL-PI"],
    )

    assert reference_run.base_dose_ml == pytest.approx(0.30)
    assert reference_run.acid_dose_ml == pytest.approx(0.30)
    assert identification.Ku == pytest.approx(18.6, rel=0.15)
    assert identification.Pu == pytest.approx(293.0, rel=0.10)
    assert gains == pytest.approx((5.83, 0.0090, 0.0), rel=0.15, abs=1e-12)
    assert reference_run.result.actual_dose_ml > 0
    assert reference_run.base.channel.value == 0.0
    assert reference_run.acid.channel.value == 0.0


def _disturbance_run(
    gains: tuple[float, float, float],
    period: float,
):
    controller = SplitRangeController(
        SplitRangeConfig(
            setpoint=7.0,
            kp=gains[0],
            ki=gains[1],
            kd=gains[2],
            dead_band=0.02,
            dt=period,
            base_pump=Pump(),
            acid_pump=Pump(),
        ),
    )

    def metabolic_load(timestamp: float) -> float:
        return 3e-7 if timestamp > 900.0 else 0.0

    return simulate(
        controller,
        PhPlant(PlantParams()),
        t_end=3600.0,
        r_metabolic_fn=metabolic_load,
        noise_pH=REFERENCE_NOISE,
        dead_time=REFERENCE_DEAD_TIME,
        seed=1,
    )


def test_identified_tl_pi_rejects_the_reference_disturbance(
    reference_gains,
) -> None:
    result = _disturbance_run(reference_gains, REFERENCE_DT)
    metrics = simulation_metrics(result, REFERENCE_DT)
    tail = result.pH[result.t >= 3000.0]

    assert metrics["max_abs_error"] <= 0.05
    assert np.ptp(tail) <= 0.05


def _setpoint_step(gains: tuple[float, float, float]):
    controller = SplitRangeController(
        SplitRangeConfig(
            setpoint=7.0,
            kp=gains[0],
            ki=gains[1],
            kd=gains[2],
            dead_band=0.02,
            dt=REFERENCE_DT,
        ),
    )

    def setpoint(timestamp: float) -> float:
        return 7.0 if timestamp < 300.0 else 5.8

    return simulate(
        controller,
        PhPlant(PlantParams()),
        t_end=3600.0,
        setpoint_fn=setpoint,
        noise_pH=REFERENCE_NOISE,
        dead_time=REFERENCE_DEAD_TIME,
        seed=2,
    )


def _post_step_settling(result, band: float = 0.05) -> float:
    post_step = result.t >= 300.0
    outside = np.flatnonzero(
        np.abs(result.pH[post_step] - result.setpoint[post_step]) > band,
    )
    return 0.0 if outside.size == 0 else float((outside[-1] + 1) * REFERENCE_DT)


def test_p_h_5_8_scaling_removes_the_unscaled_limit_cycle(
    reference_gains,
) -> None:
    scaled_gains = scale_gains_to_setpoint(
        *reference_gains,
        7.0,
        5.8,
        0.014,
    )
    unscaled = _setpoint_step(reference_gains)
    scaled = _setpoint_step(scaled_gains)

    assert _post_step_settling(scaled) <= 900.0
    assert _post_step_settling(unscaled) > 900.0
    assert np.ptp(scaled.pH[scaled.t >= 3000.0]) < 0.05
    assert np.ptp(unscaled.pH[unscaled.t >= 3000.0]) > 0.10


@pytest.mark.parametrize("period", [5.0, 20.0, 40.0])
def test_tl_pi_gains_are_sample_period_portable(
    reference_gains,
    period: float,
) -> None:
    baseline = _disturbance_run(reference_gains, REFERENCE_DT)
    candidate = _disturbance_run(reference_gains, period)
    baseline_iae = simulation_metrics(baseline, REFERENCE_DT)["IAE"]
    candidate_iae = simulation_metrics(candidate, period)["IAE"]
    tail = candidate.pH[candidate.t >= 3000.0]

    assert candidate_iae <= 1.6 * baseline_iae
    assert np.ptp(tail) <= 0.05


def test_two_second_fixed_flow_run_refuses_an_undersized_relay() -> None:
    # Preserve the sampling study's fixed 1.2 mL/min physical relay flow:
    # at two seconds this is only 0.04 mL per decision.
    config = RelayTuneConfig(
        setpoint=7.0,
        base_dose_ml=1.2 * 2.0 / 60.0,
        acid_dose_ml=1.2 * 2.0 / 60.0,
        hysteresis=0.02,
        dt=2.0,
        dead_time=REFERENCE_DEAD_TIME,
        max_cycles=10,
    )

    with pytest.raises(ValueError, match="clear hysteresis"):
        run_relay_experiment(
            PhPlant(PlantParams()),
            config,
            r_metabolic=REFERENCE_METABOLIC_LOAD,
            noise_pH=REFERENCE_NOISE,
            seed=0,
        )


class _DisconnectingClient:
    def __init__(self) -> None:
        self.disconnected = False

    async def disconnect(self) -> None:
        self.disconnected = True


@pytest.mark.asyncio
async def test_gui_disconnect_does_not_abort_the_server_owned_run(
    tmp_path,
) -> None:
    clock = _Clock()
    sensor = _Sensor()
    base, acid = _configure_pair(clock, period=REFERENCE_DT)
    reactor = Reactor("R0", 5.0, [sensor], [base, acid], REFERENCE_DT)
    reactor.sampling.pairings[sensor.id].extend(
        [(base.id, 0), (acid.id, 0)],
    )
    reactor.unpaired.actuators.clear()
    nodes = {actuator.id: ActuatorOpc(actuator) for actuator in (base, acid)}
    api = ReactorAutotuneOpc(
        reactor,
        nodes,
        audit=AutotuneAudit("R0", directory=lambda: tmp_path),
    )
    started = json.loads(
        api.start(
            base.id,
            sensor.id,
            acid.id,
            RelayTuneConfig(
                base_dose_ml=0.30,
                acid_dose_ml=0.30,
                max_minutes=60.0,
                acknowledge_other_loops=True,
            ),
        ),
    )
    assert run_from_payload(started).mode is ViewMode.running
    server_run = api.coordinator.run

    gui = AppState("opc.tcp://localhost:4840/")
    gui_client = _DisconnectingClient()
    gui.client = gui_client
    gui.book = SimpleNamespace()
    await gui.disconnect()

    sensor.channels[0].value = 7.0
    reactor.update_autotune()
    reopened = run_from_payload(api.status())

    assert gui_client.disconnected
    assert api.coordinator.run is server_run
    assert server_run.is_active
    assert reopened.mode is ViewMode.running
    assert len(reopened.trace) == 1
    server_run.abort()


@pytest.mark.asyncio
async def test_active_tune_preserves_paired_and_unpaired_loop_jobs(
    monkeypatch,
) -> None:
    clock = _Clock()
    sensor = _Sensor(value=5.5)
    base, acid = _configure_pair(clock, period=1.0)
    paired = _pump("R0:paired", clock)
    unpaired = _pump("R0:unpaired", clock)
    paired.set_control_config(
        ControlConfig(
            ControlMethod.on_boundaries,
            lb=6.0,
            ub=8.0,
            value=321.0,
        ),
    )
    unpaired.set_control_config(
        ControlConfig(ControlMethod.manual, value=123.0),
    )
    reactor = Reactor(
        "R0",
        5.0,
        [sensor],
        [base, acid, paired, unpaired],
        1.0,
    )
    reactor.sampling.pairings[sensor.id].extend(
        [(base.id, 0), (acid.id, 0), (paired.id, 0)],
    )
    for identifier in (base.id, acid.id, paired.id):
        reactor.unpaired.actuators.remove(identifier)
    context = AutotuneContext(
        reactor.id,
        reactor.volume,
        reactor.sensors,
        reactor.actuators,
        lambda: reactor.sampling.pairings,
    )
    coordinator = AutotuneCoordinator(context, clock=clock)
    reactor.autotune = coordinator
    run = coordinator.start(
        sensor.id,
        base.id,
        acid.id,
        RelayTuneConfig(
            base_dose_ml=0.20,
            acid_dose_ml=0.20,
            max_minutes=5.0,
            acknowledge_other_loops=True,
        ),
    )
    counts = {"run": 0, "base": 0, "paired": 0, "unpaired": 0}
    original_run_tick = run.tick

    def run_tick() -> object:
        counts["run"] += 1
        return original_run_tick()

    monkeypatch.setattr(run, "tick", run_tick)
    for name, actuator in (
        ("base", base),
        ("paired", paired),
        ("unpaired", unpaired),
    ):
        original = actuator.tick

        def tick(*, label=name, callback=original) -> None:
            counts[label] += 1
            callback()

        monkeypatch.setattr(actuator, "tick", tick)

    reactor.update_paired_actuators()
    task = asyncio.create_task(reactor.actuator_loop())
    try:
        await asyncio.sleep(0.12)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        run.abort()

    assert paired.channel.value == 321.0
    assert unpaired.channel.value == 123.0
    assert counts["run"] >= 2
    assert counts["base"] == 0
    assert counts["paired"] >= 2
    assert counts["unpaired"] >= 2


@pytest.mark.asyncio
async def test_pump_calibration_and_autotune_ownership_are_mutually_exclusive() -> None:
    harness = _plant_harness()
    calibration = CalibrationRun(harness.base, clock=harness.clock)

    refused = await calibration.calibrate_point(500.0, 1.0)
    assert "active autotune" in refused
    assert harness.base.autotune_owner is harness.run

    harness.run.abort()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_calibration(_seconds: float) -> None:
        entered.set()
        await release.wait()

    calibration.sleep = hold_calibration
    task = asyncio.create_task(calibration.calibrate_point(500.0, 1.0))
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    try:
        with pytest.raises(RuntimeError, match="already calibrating"):
            AutotuneCoordinator(
                harness.run.context,
                clock=harness.clock,
            ).start(
                harness.run.sensor_id,
                harness.run.base_id,
                harness.run.acid_id,
                harness.run.config,
            )
    finally:
        release.set()
        await task

    assert not harness.base.calibrating
    assert harness.base.autotune_owner is None


@pytest.mark.asyncio
async def test_sample_ready_preserves_an_event_during_publish_without_free_run(
    monkeypatch,
) -> None:
    reactor_opc = ReactorOpc(
        "R0",
        volume=5.0,
        sensors=[_Sensor()],
        actuators=[_pump("R0:pwm0", _Clock())],
        period=10.0,
    )
    counts = {"sensor": 0, "actuator": 0}
    first_publish = asyncio.Event()
    release_first = asyncio.Event()
    twice = asyncio.Event()

    async def sensor_update() -> None:
        counts["sensor"] += 1
        if counts["sensor"] == 1:
            first_publish.set()
            await release_first.wait()
        if counts["sensor"] == 2:
            twice.set()

    async def actuator_update() -> None:
        counts["actuator"] += 1

    monkeypatch.setattr(reactor_opc.sensor_nodes[0], "update_value", sensor_update)
    monkeypatch.setattr(
        reactor_opc.actuator_nodes[0],
        "update_value",
        actuator_update,
    )
    task = asyncio.create_task(reactor_opc.update())
    try:
        reactor_opc.sample_ready.set()
        await asyncio.wait_for(first_publish.wait(), timeout=1.0)
        reactor_opc.sample_ready.set()
        release_first.set()
        await asyncio.wait_for(twice.wait(), timeout=1.0)
        await asyncio.sleep(0.03)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert counts == {"sensor": 2, "actuator": 2}
    assert not reactor_opc.sample_ready.is_set()


class _Node:
    def __init__(self, nodeid: str) -> None:
        self.nodeid = nodeid


class _Subscription:
    def __init__(self) -> None:
        self.nodes: list[_Node] = []

    async def subscribe_data_change(self, nodes: list[_Node]) -> None:
        self.nodes.extend(nodes)


class _ArchiveClient:
    def __init__(self) -> None:
        self.subscription_count = 0
        self.subscription = _Subscription()

    async def create_subscription(self, _params, _handler) -> _Subscription:
        self.subscription_count += 1
        return self.subscription

    def get_node(self, nodeid: str) -> _Node:
        return _Node(nodeid)

    async def read_values(self, nodes: list[_Node]) -> list[float]:
        return [10.0 for _node in nodes]


@pytest.mark.asyncio
async def test_autotune_and_reconnect_do_not_expand_or_duplicate_subscriptions(
    monkeypatch,
) -> None:
    opc = OpcClient("opc.tcp://localhost:4840/")
    transport = _ArchiveClient()
    opc.client = transport
    opc.sensor_vars = {
        "old-ph": {"reactor": "R0", "name": "ph", "channel": "pH"},
    }
    opc.actuator_vars = {
        f"old-{channel}": {
            "reactor": "R0",
            "name": "base",
            "channel": channel,
        }
        for channel in ("curr_value", "total_volume", "cal_a", "kp")
    }
    opc.variables = {**opc.sensor_vars, **opc.actuator_vars}
    opc.methods = {
        "autotune-status": {
            "reactor": "R0",
            "name": ["base", "autotune_status"],
        },
    }
    opc.server_config_vars = {
        "old-period": {"name": "sampling_period"},
    }
    opc.server_config_methods = {
        "old-set-period": {"name": "set_sampling_period"},
    }
    await opc.init_subscriptions()

    async def refresh_browse() -> None:
        opc.sensor_vars = {
            "new-ph": {"reactor": "R0", "name": "ph", "channel": "pH"},
        }
        opc.actuator_vars = {
            f"new-{channel}": {
                "reactor": "R0",
                "name": "base",
                "channel": channel,
            }
            for channel in (
                "curr_value",
                "total_volume",
                "cal_a",
                "kp",
            )
        }
        opc.variables = {**opc.sensor_vars, **opc.actuator_vars}
        opc.methods = {
            "new-autotune-status": {
                "reactor": "R0",
                "name": ["base", "autotune_status"],
            },
        }
        opc.server_config_vars = {
            "new-period": {"name": "sampling_period"},
        }
        opc.server_config_methods = {
            "new-set-period": {"name": "set_sampling_period"},
        }

    monkeypatch.setattr(opc, "refresh_browse", refresh_browse)
    app = AppState("opc.tcp://localhost:4840/")
    app.client = opc
    app.book = AddressBook.from_client(opc)

    async def skip_database_adoption() -> None:
        return None

    monkeypatch.setattr(app, "adopt_running_experiments", skip_database_adoption)
    await app._rebrowse(opc)

    assert transport.subscription_count == 1
    assert {node.nodeid for node in transport.subscription.nodes} == {
        "old-ph",
        "old-curr_value",
        "old-total_volume",
    }
    assert {
        nodeid
        for nodeid, info in opc.variables.items()
        if opc.archives(nodeid, info)
    } == {"new-ph", "new-curr_value", "new-total_volume"}
    assert app.book.server_variable("sampling_period") == "new-period"
