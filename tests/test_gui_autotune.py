"""Pure controller and NiceGUI workflow tests for pH PID autotuning."""

from __future__ import annotations

import asyncio
import json
from importlib import import_module

import pytest
from asyncua.client.ua_client import UaClientState
from nicegui import ui
from nicegui.testing import User

from reactors_czlab.gui import state as state_module
from reactors_czlab.gui.address import AddressBook
from reactors_czlab.gui.controllers.autotune import (
    FormState,
    ViewMode,
    calibration_timestamp,
    decode_payload,
    preflight_from_payload,
    run_from_payload,
    validate_form,
)

pytest_plugins = ("nicegui.testing.user_plugin",)


@pytest.fixture
def slow_page_timers(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep recurring production timers outside short route tests."""
    shell = import_module("reactors_czlab.gui.components.shell")
    autotune_page = import_module("reactors_czlab.gui.pages.autotune")
    monkeypatch.setattr(shell, "STATUS_SECONDS", 3600.0)
    monkeypatch.setattr(autotune_page, "POLL_SECONDS", 3600.0)
    timer = ui.timer

    def slow_timer(
        interval: float,
        callback=None,
        *,
        active: bool = True,
        once: bool = False,
        immediate: bool = True,
    ):
        return timer(
            3600.0,
            callback,
            active=active,
            once=once,
            immediate=immediate,
        )

    monkeypatch.setattr(ui, "timer", slow_timer)


def _confirm_latest(user: User) -> None:
    """Click the newest confirmation when earlier closed dialogs remain."""
    interaction = user.find("Confirm")
    interaction.elements = {max(interaction.elements, key=lambda item: item.id)}
    interaction.click()


def _status(phase: str = "idle") -> dict:
    payload = {
        "version": 1,
        "ok": True,
        "message": "no autotune has been started",
        "phase": phase,
        "current_ph": None,
        "relay_direction": "none",
        "elapsed_seconds": 0.0,
        "trace": [],
        "cycles": [],
        "candidate_gains": {},
    }
    if phase == "idle":
        return payload
    payload.update(
        {
            "message": f"server says {phase}",
            "selection": {
                "sensor_id": "R0:ph",
                "base_id": "R0:base",
                "acid_id": "R0:acid",
            },
            "setpoint": 7.0,
            "hysteresis_ph": 0.02,
            "chemistry": {
                "phosphate_molar": 0.014,
                "base_molar": 0.5,
                "acid_molar": 0.5,
            },
            "max_minutes": 30.0,
            "current_ph": 7.03,
            "elapsed_seconds": 125.0,
            "adjusted_doses_ml": {"base": 0.2, "acid": 0.25},
            "dose": {"actual_ml": 1.2, "budget_ml": 8.0},
            "safety": {"safe_low": 6.0, "safe_high": 8.0},
            "noise_sigma": 0.003,
            "settling_cycles": 2,
            "clean_cycles": 3,
            "warnings": ["server warning"],
            "trace": [
                {
                    "timestamp": 10.0,
                    "ph": 7.03,
                    "requested_volume_ml": 0.2,
                    "actual_dose_ml": 1.2,
                },
            ],
        },
    )
    if phase == "identified":
        payload["result"] = {
            "identification": {
                "Ku": 18.6,
                "Pu": 293.0,
                "amplitude": 0.05,
                "mean_ph": 7.0,
                "cycles_used": 4,
            },
        }
        payload["candidate_gains"] = {
            "TL-PI": {"kp": 5.83, "ki": 0.009, "kd": 0.0},
            "ZN-PID": {"kp": 11.16, "ki": 0.076, "kd": 408.7},
            "TL-PID": {"kp": 8.4, "ki": 0.04, "kd": 120.0},
            "SIMC": {"kp": 4.1, "ki": 0.02, "kd": 30.0},
        }
    return payload


class TestAutotuneController:
    """JSON interpretation and local validation need no NiceGUI client."""

    @pytest.mark.parametrize(
        ("phase", "mode"),
        [
            ("idle", ViewMode.setup),
            ("baseline", ViewMode.running),
            ("adapting", ViewMode.running),
            ("settling", ViewMode.running),
            ("collecting", ViewMode.running),
            ("identified", ViewMode.identified),
            ("aborted", ViewMode.failed),
            ("failed", ViewMode.failed),
        ],
    )
    def test_maps_every_server_phase(self, phase: str, mode: ViewMode) -> None:
        assert run_from_payload(_status(phase)).mode is mode

    def test_reconstructs_active_run_and_candidates(self) -> None:
        view = run_from_payload(_status("identified"))

        assert view.form.sensor_id == "R0:ph"
        assert view.form.phosphate_mm == pytest.approx(14.0)
        assert view.ku == pytest.approx(18.6)
        assert [gain.rule for gain in view.gains] == [
            "TL-PI",
            "ZN-PID",
            "TL-PID",
            "SIMC",
        ]
        assert view.gains[0].has_derivative is False
        assert view.gains[1].has_derivative is True

    def test_rejects_bad_version_and_unknown_phase(self) -> None:
        with pytest.raises(ValueError, match="version 2"):
            decode_payload({**_status(), "version": 2})
        with pytest.raises(ValueError, match="version True"):
            decode_payload({**_status(), "version": True})
        with pytest.raises(ValueError, match="phase"):
            run_from_payload({**_status(), "phase": "mystery"})

    def test_preflight_keeps_refusal_verbatim_and_maps_budget(self) -> None:
        refusal = preflight_from_payload(
            {
                "version": 1,
                "ok": False,
                "message": "base pump needs a fitted calibration",
                "phase": "idle",
            },
        )
        accepted = preflight_from_payload(
            {
                "version": 1,
                "ok": True,
                "message": "autotune preflight passed",
                "phase": "idle",
                "safety": {
                    "safe_low": 6.0,
                    "safe_high": 8.0,
                    "default_dose_budget_ml": 9.5,
                    "dose_budget_ml": 7.0,
                },
                "warnings": ["control period is long"],
            },
        )

        assert refusal.message == "base pump needs a fitted calibration"
        assert accepted.default_budget_ml == pytest.approx(9.5)
        assert accepted.effective_budget_ml == pytest.approx(7.0)
        assert accepted.warnings == ("control period is long",)

    @pytest.mark.parametrize(
        ("safety", "message"),
        [
            ({}, "safe low"),
            (
                {
                    "safe_low": float("nan"),
                    "safe_high": 8.0,
                    "default_dose_budget_ml": 9.0,
                    "dose_budget_ml": 9.0,
                },
                "not finite",
            ),
            (
                {
                    "safe_low": 8.0,
                    "safe_high": 6.0,
                    "default_dose_budget_ml": 9.0,
                    "dose_budget_ml": 9.0,
                },
                "not ordered",
            ),
            (
                {
                    "safe_low": 6.0,
                    "safe_high": 8.0,
                    "default_dose_budget_ml": -1.0,
                    "dose_budget_ml": 9.0,
                },
                "must be positive",
            ),
            (
                {
                    "safe_low": 6.0,
                    "safe_high": 8.0,
                    "default_dose_budget_ml": 9.0,
                    "dose_budget_ml": 0.0,
                },
                "must be positive",
            ),
        ],
    )
    def test_rejects_malformed_accepted_preflight(
        self,
        safety: dict,
        message: str,
    ) -> None:
        payload = {
            "version": 1,
            "ok": True,
            "message": "accepted",
            "phase": "idle",
            "safety": safety,
        }

        with pytest.raises((TypeError, ValueError), match=message):
            preflight_from_payload(payload)

    def test_validates_acknowledgements_and_distinct_pumps(self) -> None:
        form = FormState(
            sensor_id="R0:ph",
            base_id="R0:base",
            acid_id="R0:base",
            dose_budget_override_ml=8.0,
        )
        errors = validate_form(form)

        assert "Base and acid pumps must be different" in errors
        assert "Acknowledge effects on other control loops" in errors
        assert "Acknowledge the dose budget override" in errors

    def test_builds_opc_units_and_zero_default_budget(self) -> None:
        form = FormState(
            sensor_id="R0:ph",
            base_id="R0:base",
            acid_id="R0:acid",
            acknowledge_other_loops=True,
        )

        args = form.opc_args()

        assert args[0:2] == ("R0:ph", "R0:acid")
        assert args[7] == pytest.approx(0.014)
        assert args[10:] == (0.0, True, False)

    def test_calibration_timestamp_is_operator_facing(self) -> None:
        assert calibration_timestamp(
            {
                "calibration": {
                    "is_fitted": True,
                    "fitted_at": "2026-08-02T12:00:00+00:00",
                },
            },
        ) == "2026-08-02T12:00:00+00:00"
        assert calibration_timestamp({"calibration": None}) == (
            "no fitted calibration"
        )


SENSOR_VARS = {
    "s-ph": {"reactor": "R0", "name": "ph", "channel": "pH"},
}
ACTUATOR_VARS = {
    "a-base": {"reactor": "R0", "name": "base", "channel": "curr_value"},
    "a-acid": {"reactor": "R0", "name": "acid", "channel": "curr_value"},
}


def _methods() -> dict[str, dict]:
    methods: dict[str, dict] = {}
    index = 0
    for pump in ("base", "acid"):
        for method in (
            "autotune_preflight",
            "autotune_start",
            "autotune_status",
            "autotune_abort",
            "autotune_apply",
            "autotune_scale_to_setpoint",
            "autotune_reapply_last",
            "get_calibration",
            "get_control_config",
        ):
            methods[f"m-{index}"] = {
                "reactor": "R0",
                "name": [pump, method],
            }
            index += 1
    return methods


class AutotuneClient:
    """Short OPC responses used by route and interaction tests."""

    def __init__(self, phase: str = "idle") -> None:
        self.sensor_vars = SENSOR_VARS
        self.actuator_vars = ACTUATOR_VARS
        self.methods = _methods()
        self.variables = {}
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.status = _status(phase)
        self.fail_status = False
        self.connection_state = UaClientState.CONNECTED
        self._method_by_node = {
            nodeid: info["name"][-1]
            for nodeid, info in self.methods.items()
        }

    @property
    def state(self) -> UaClientState:
        return self.connection_state

    @property
    def recording(self) -> bool:
        return False

    def is_recording(self, reactor: str) -> bool:
        return False

    async def call_method(self, nodeid: str, *args: object) -> str:
        method = self._method_by_node[nodeid]
        self.calls.append((method, args))
        if method == "autotune_status":
            if self.fail_status:
                raise OSError("status link failed")
            return json.dumps(self.status)
        if method == "get_calibration":
            return json.dumps(
                {
                    "calibration": {
                        "is_fitted": True,
                        "fitted_at": "2026-08-02T12:00:00+00:00",
                    },
                },
            )
        if method == "get_control_config":
            return json.dumps({"setpoint": 6.5})
        if method == "autotune_preflight":
            return json.dumps(
                {
                    "version": 1,
                    "ok": True,
                    "message": "autotune preflight passed",
                    "phase": self.status["phase"],
                    "safety": {
                        "safe_low": 6.0,
                        "safe_high": 8.0,
                        "default_dose_budget_ml": 9.5,
                        "dose_budget_ml": 9.5,
                    },
                    "warnings": [],
                },
            )
        action_phase = self.status["phase"]
        if method == "autotune_start":
            self.status = _status("baseline")
            action_phase = "baseline"
        elif method == "autotune_abort":
            self.status = _status("aborted")
            action_phase = "aborted"
        return json.dumps(
            {
                "version": 1,
                "ok": True,
                "message": f"server accepted {method}",
                "phase": action_phase,
            },
        )


@pytest.fixture
def autotune_state(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> AutotuneClient:
    phase = getattr(request, "param", "idle")
    client = AutotuneClient(phase)
    monkeypatch.setattr(state_module.STATE, "client", client)
    monkeypatch.setattr(
        state_module.STATE,
        "book",
        AddressBook.from_mappings(SENSOR_VARS, ACTUATOR_VARS, client.methods),
    )
    monkeypatch.setattr(state_module.STATE, "generation", 10)
    return client


@pytest.fixture
def disconnected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Expose the route's ordinary connection-failure branch."""
    monkeypatch.setattr(state_module.STATE, "client", None)
    monkeypatch.setattr(state_module.STATE, "book", None)
    monkeypatch.setattr(
        state_module.STATE,
        "connection_error",
        "OSError: connection refused",
    )


@pytest.mark.usefixtures("slow_page_timers")
class TestAutotuneRoutes:
    """Every server phase builds a useful real element tree."""

    async def test_disconnected(self, user: User, disconnected: None) -> None:
        await user.open("/reactor/R0/autotune")
        await user.should_see("Retry")

    async def test_idle(self, user: User, autotune_state: AutotuneClient) -> None:
        await user.open("/reactor/R0/autotune")
        await user.should_see("Run setup")
        await user.should_see("Base pump: backwards=False")
        await user.should_see("2026-08-02T12:00:00+00:00")

    async def test_default_pump_roles_are_deterministic(
        self,
        user: User,
        autotune_state: AutotuneClient,
    ) -> None:
        await user.open("/reactor/R0/autotune")
        selects = user.find(ui.select).elements
        base = next(item for item in selects if item.props["label"] == "Base pump")
        acid = next(item for item in selects if item.props["label"] == "Acid pump")

        assert base.value == "R0:acid"
        assert acid.value == "R0:base"

    async def test_initial_status_failure_is_visible(
        self,
        user: User,
        autotune_state: AutotuneClient,
    ) -> None:
        autotune_state.fail_status = True

        await user.open("/reactor/R0/autotune")

        await user.should_see(
            "Could not read autotune status: status link failed",
        )
        await user.should_see("Run setup")

    async def test_header_targets_are_pruned_when_a_new_header_builds(
        self,
        user: User,
        autotune_state: AutotuneClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from reactors_czlab.gui.components.shell import status_badges

        class DeletedTarget:
            deleted = True

        departed = DeletedTarget()
        monkeypatch.setattr(status_badges, "targets", [departed])
        await user.open("/reactor/R0/autotune")

        assert departed not in status_badges.targets
        assert len(status_badges.targets) == 1

    @pytest.mark.parametrize(
        "autotune_state",
        ["baseline"],
        indirect=True,
    )
    async def test_running(
        self,
        user: User,
        autotune_state: AutotuneClient,
    ) -> None:
        await user.open("/reactor/R0/autotune")
        await user.should_see("Phase: baseline")
        await user.should_see("Abort")
        await user.should_see("Combined dose")

    @pytest.mark.parametrize("autotune_state", ["failed"], indirect=True)
    async def test_failed(
        self,
        user: User,
        autotune_state: AutotuneClient,
    ) -> None:
        await user.open("/reactor/R0/autotune")
        await user.should_see("Autotune did not identify gains")
        await user.should_see("server says failed")

    @pytest.mark.parametrize("autotune_state", ["identified"], indirect=True)
    async def test_identified(
        self,
        user: User,
        autotune_state: AutotuneClient,
    ) -> None:
        await user.open("/reactor/R0/autotune")
        await user.should_see("Ku: 18.600000 mL/pH")
        await user.should_see("TL-PI: kp=5.83")
        await user.should_see("Apply gains")


@pytest.mark.usefixtures("slow_page_timers")
class TestAutotuneInteractions:
    """Validation, acknowledgements and every consequential action."""

    async def test_acknowledgements_gate_preflight(
        self,
        user: User,
        autotune_state: AutotuneClient,
    ) -> None:
        await user.open("/reactor/R0/autotune")
        user.find("Check preflight").click()
        await user.should_see("Acknowledge effects on other control loops")
        assert not any(
            method == "autotune_preflight"
            for method, _ in autotune_state.calls
        )

        user.find(
            "I acknowledge that the pH excursion may affect other loops",
        ).click()
        user.find("Check preflight").click()
        await user.should_see("Chemistry-computed budget: 9.500 mL")
        await user.should_see("Review and start")

    async def test_override_needs_its_own_acknowledgement(
        self,
        user: User,
        autotune_state: AutotuneClient,
    ) -> None:
        await user.open("/reactor/R0/autotune")
        user.find(
            "I acknowledge that the pH excursion may affect other loops",
        ).click()
        user.find("Dose budget override (mL)").type("7")
        user.find("Check preflight").click()
        await user.should_see("Acknowledge the dose budget override")
        assert not any(
            method == "autotune_preflight"
            for method, _ in autotune_state.calls
        )

        user.find(
            "I explicitly acknowledge this budget override",
        ).click()
        user.find("Check preflight").click()
        await user.should_see("autotune preflight passed")

    async def test_start_has_final_safety_confirmation_and_readback(
        self,
        user: User,
        autotune_state: AutotuneClient,
    ) -> None:
        await user.open("/reactor/R0/autotune")
        user.find(
            "I acknowledge that the pH excursion may affect other loops",
        ).click()
        user.find("Check preflight").click()
        await user.should_see("autotune preflight passed")

        user.find("Review and start").click()
        await user.should_see("Start pH PID autotuning?")
        await user.should_see("Safety 6.000–8.000 pH")
        assert not any(
            method == "autotune_start" for method, _ in autotune_state.calls
        )

        _confirm_latest(user)
        await user.should_see("Phase: baseline")
        methods = [method for method, _ in autotune_state.calls]
        assert "autotune_start" in methods
        assert methods.count("autotune_status") >= 2

    async def test_terminal_run_requires_a_fresh_preflight(
        self,
        user: User,
        autotune_state: AutotuneClient,
    ) -> None:
        await user.open("/reactor/R0/autotune")
        user.find(
            "I acknowledge that the pH excursion may affect other loops",
        ).click()
        user.find("Check preflight").click()
        await user.should_see("autotune preflight passed")
        start_button = next(iter(user.find("Review and start").elements))
        user.find("Review and start").click()
        await user.should_see("Start pH PID autotuning?")
        _confirm_latest(user)
        await user.should_see("Phase: baseline")
        assert start_button.enabled is False

        user.find("Abort").click()
        await user.should_see("Abort pH PID autotuning?")
        _confirm_latest(user)
        await user.should_see("Autotune did not identify gains")
        assert start_button.enabled is False
        starts = [
            method
            for method, _ in autotune_state.calls
            if method == "autotune_start"
        ]
        assert starts == ["autotune_start"]

        user.find("Check preflight").click()
        await user.should_see("Chemistry-computed budget: 9.500 mL")
        assert start_button.enabled is True

        autotune_state.connection_state = UaClientState.RECONNECTING
        await asyncio.sleep(0.2)
        assert start_button.enabled is False

    @pytest.mark.parametrize("autotune_state", ["baseline"], indirect=True)
    async def test_abort_reads_back_server_status(
        self,
        user: User,
        autotune_state: AutotuneClient,
    ) -> None:
        await user.open("/reactor/R0/autotune")
        user.find("Abort").click()
        await user.should_see("Abort pH PID autotuning?")
        _confirm_latest(user)
        await user.should_see("Autotune did not identify gains")
        assert [method for method, _ in autotune_state.calls].count(
            "autotune_status",
        ) >= 2

    @pytest.mark.parametrize("autotune_state", ["identified"], indirect=True)
    async def test_apply_scale_and_reapply_are_confirmed_and_read_back(
        self,
        user: User,
        autotune_state: AutotuneClient,
    ) -> None:
        await user.open("/reactor/R0/autotune")

        user.find("Apply gains").click()
        await user.should_see("Apply autotune gains?")
        _confirm_latest(user)
        await user.should_see("server accepted autotune_apply")

        user.find("Scale to current setpoint").click()
        await user.should_see("current shared setpoint of pH 6.500")
        _confirm_latest(user)
        await user.should_see("server accepted autotune_scale_to_setpoint")

        user.find("Reapply last tune").click()
        await user.should_see("Reapply the last tune?")
        _confirm_latest(user)
        await user.should_see("server accepted autotune_reapply_last")

        methods = [method for method, _ in autotune_state.calls]
        assert "autotune_apply" in methods
        assert "autotune_scale_to_setpoint" in methods
        assert "autotune_reapply_last" in methods
        assert methods.count("autotune_status") >= 4
