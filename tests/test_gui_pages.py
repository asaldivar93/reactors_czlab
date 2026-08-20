"""Smoke tests for the routes, driven through NiceGUI's user fixture.

The pages hold no logic - that is in ``address``, ``format``,
``control`` and ``controllers``, all covered without a browser. What is
covered here is that each route builds at all, and that the two states
a page can be in reach the right branch: connected, and not.

A page that raises while rendering shows an operator a blank screen, so
"it builds" is worth pinning even when nothing is decided in it.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from nicegui.testing import User

from reactors_czlab.gui import state as state_module
from reactors_czlab.gui.address import AddressBook

pytest_plugins = ("nicegui.testing.user_plugin",)

SENSOR_VARS = {
    "ns=2;i=10": {
        "reactor": "R0",
        "name": "ph",
        "channel": "pH",
        "description": "acidity",
    },
    "ns=2;i=11": {"reactor": "R0", "name": "ph", "channel": "oC"},
    "ns=2;i=12": {"reactor": "R0", "name": "do", "channel": "ppm"},
}

ACTUATOR_VARS = {
    "ns=2;i=20": {"reactor": "R0", "name": "pwm0", "channel": "curr_value"},
    "ns=2;i=21": {"reactor": "R0", "name": "pwm0", "channel": "total_volume"},
    "ns=2;i=22": {"reactor": "R0", "name": "pwm0", "channel": "cal_a"},
    "ns=2;i=23": {"reactor": "R0", "name": "pwm0", "channel": "cal_b"},
    "ns=2;i=24": {"reactor": "R0", "name": "pwm0", "channel": "cal_r2"},
}

PH_STATUS_METHOD = "ns=2;i=33"
DO_STATUS_METHOD = "ns=2;i=35"
PWM0_CALIBRATION_METHOD = "ns=2;i=34"
PWM0_CONTROL_METHOD = "ns=2;i=36"
PWM0_RESET_METHOD = "ns=2;i=37"
SERVER_PERIOD_NODE = "ns=2;i=40"
SET_PERIOD_METHOD = "ns=2;i=41"

METHODS = {
    "ns=2;i=30": {"reactor": "R0", "name": ["set_pairing"]},
    "ns=2;i=31": {"reactor": "R0", "name": ["unpair"]},
    "ns=2;i=32": {"reactor": "R0", "name": ["ph", "calibration"]},
    PH_STATUS_METHOD: {
        "reactor": "R0",
        "name": ["ph", "read_calibration_status"],
    },
    PWM0_CALIBRATION_METHOD: {
        "reactor": "R0",
        "name": ["pwm0", "get_calibration"],
    },
    PWM0_CONTROL_METHOD: {
        "reactor": "R0",
        "name": ["pwm0", "get_control_config"],
    },
    PWM0_RESET_METHOD: {
        "reactor": "R0",
        "name": ["pwm0", "reset_total_volume"],
    },
    DO_STATUS_METHOD: {
        "reactor": "R0",
        "name": ["do", "read_calibration_status"],
    },
}

SERVER_CONFIG_VARS = {
    SERVER_PERIOD_NODE: {"name": "sampling_period"},
}

SERVER_CONFIG_METHODS = {
    SET_PERIOD_METHOD: {"name": "set_sampling_period"},
}


@pytest.fixture(autouse=True)
def _inline_to_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep route tests independent of executor shutdown timing."""
    async def immediately(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", immediately)


class FakeClient:
    """Enough of an OpcClient for a page to render against."""

    def __init__(self) -> None:
        """Start connected, with a value on every published variable."""
        self.recording_reactors: set[str] = set()
        self.experiment_tags: dict[str, str] = {}
        self.sensor_vars = SENSOR_VARS
        self.actuator_vars = ACTUATOR_VARS
        self.methods = METHODS
        self.server_config_vars = SERVER_CONFIG_VARS
        self.server_config_methods = SERVER_CONFIG_METHODS
        self.server_period = 10.0
        self.sampling_result = (True, "sampling period set")
        self.variables = {
            nodeid: {**info, "value": 1.0, "timestamp": None}
            for nodeid, info in {**SENSOR_VARS, **ACTUATOR_VARS}.items()
        }
        # The real client object asyncua would hold. Pages that browse
        # for the pairings variable check this and give up cleanly.
        self.client = None

    @property
    def state(self) -> object:
        """Reported as connected."""
        from asyncua.client.ua_client import UaClientState

        return UaClientState.CONNECTED

    @property
    def recording(self) -> bool:
        """Whether any reactor is recording."""
        return bool(self.recording_reactors)

    def is_recording(self, reactor: str) -> bool:
        """Whether one reactor is recording."""
        return reactor in self.recording_reactors

    async def call_method(self, nodeid: str, *args: object) -> object:
        """Answer the calls a page makes while it renders.

        ``ph`` answers as a calibratable probe and ``do`` as one that is
        not, so the sensor page's two branches are both exercised.
        """
        if nodeid == PH_STATUS_METHOD:
            return ["ok", 0.98, 7.0, 7.01]
        if nodeid == DO_STATUS_METHOD:
            return ["unsupported", 0.0, 0.0, 0.0]
        if nodeid == PWM0_CALIBRATION_METHOD:
            return json.dumps(
                {
                    "actuator": "R0:pwm0",
                    "running": False,
                    "pending": None,
                    "run_points": [],
                    "calibration": {
                        "file": "R0_pwm0",
                        "a": 0.001,
                        "b": 0.0,
                        "numeric_equation": "flow = 0.001 * duty + 0",
                        "r2": 1.0,
                        "min_duty": 0.0,
                        "max_duty": 4095.0,
                        "dispense_duty": 4095.0,
                        "fitted_at": "2026-08-02T12:00:00+00:00",
                        "is_fitted": True,
                        "points": [[1000.0, 1.0], [3000.0, 3.0]],
                        "installable_reason": None,
                    },
                },
            )
        if nodeid == PWM0_CONTROL_METHOD:
            return json.dumps(
                {
                    "method": "manual",
                    "output_unit": "duty",
                    "demand": 0.0,
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
                    "methods": ["manual", "timer", "on_boundaries", "pid"],
                    "output_units": ["duty", "flow", "volume"],
                },
            )
        if nodeid == SET_PERIOD_METHOD:
            accepted, message = self.sampling_result
            if accepted:
                self.server_period = float(args[0])
            return [accepted, message]
        return None

    async def read_many(self, nodeids: list[str]) -> list[object]:
        """Return the global configuration read-back."""
        return [self.server_period for nodeid in nodeids]


@pytest.fixture
def connected(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    """Put STATE into a connected state without a server."""
    client = FakeClient()
    monkeypatch.setattr(state_module.STATE, "client", client)
    monkeypatch.setattr(
        state_module.STATE,
        "book",
        AddressBook.from_mappings(
            SENSOR_VARS,
            ACTUATOR_VARS,
            METHODS,
            SERVER_CONFIG_VARS,
            SERVER_CONFIG_METHODS,
        ),
    )
    monkeypatch.setattr(state_module.STATE, "period", 10.0)
    return client


@pytest.fixture
def disconnected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put STATE into a disconnected state."""
    monkeypatch.setattr(state_module.STATE, "client", None)
    monkeypatch.setattr(state_module.STATE, "book", None)
    monkeypatch.setattr(
        state_module.STATE,
        "connection_error",
        "OSError: connection refused",
    )


class TestIndex:
    """The reactor list."""

    async def test_lists_the_reactors(
        self,
        user: User,
        connected: None,
    ) -> None:
        """Every reactor the server published gets a card."""
        await user.open("/")
        await user.should_see("R0")
        await user.should_see("Reactors")

    async def test_offers_a_retry_when_disconnected(
        self,
        user: User,
        disconnected: None,
    ) -> None:
        """A server not up yet is the normal state on boot."""
        await user.open("/")
        await user.should_see("Retry")
        await user.should_see("connection refused")


class TestReactorDashboard:
    """Live values, configuration and pairing."""

    async def test_shows_sensors_and_actuators(
        self,
        user: User,
        connected: None,
    ) -> None:
        """The dashboard the requirements ask for, in one page."""
        await user.open("/reactor/R0")
        await user.should_see("Sensors")
        await user.should_see("Actuators")
        await user.should_see("Pairings")

    async def test_offers_the_configure_control(
        self,
        user: User,
        connected: None,
    ) -> None:
        """Modifying actuator configuration starts here."""
        await user.open("/reactor/R0")
        await user.should_see("Configure")

    async def test_offers_reset_delivered_for_a_calibrated_pump(
        self,
        user: User,
        connected: None,
    ) -> None:
        """A pump publishing the reset method gets the confirmed action."""
        await user.open("/reactor/R0")
        await user.should_see("Reset delivered")

    async def test_shows_running_control_and_experiment_state(
        self,
        user: User,
        connected: None,
    ) -> None:
        """The card states what drives the actuator without a dialog."""
        await user.open("/reactor/R0")
        await user.should_see("manual · duty · demand")
        await user.should_see("No active experiment")
        await user.should_see("Unpaired")

    async def test_says_so_when_disconnected(
        self,
        user: User,
        disconnected: None,
    ) -> None:
        """Not a blank page and not a traceback."""
        await user.open("/reactor/R0")
        await user.should_see("Retry")


class TestCalibrationPages:
    """Both calibration screens."""

    async def test_sensor_page_shows_both_points(
        self,
        user: User,
        connected: None,
    ) -> None:
        """CP1 and CP2, settable independently."""
        await user.open("/reactor/R0/calibration/sensors")
        await user.should_see("CP1")
        await user.should_see("CP2")

    async def test_the_state_is_shown_without_pressing_read(
        self,
        user: User,
        connected: None,
    ) -> None:
        """The requirement is that the screen *shows* the state.

        It used to say "not read yet" until an operator pressed Read,
        which is not the same thing.
        """
        await user.open("/reactor/R0/calibration/sensors")
        await user.should_see("stored value")
        await user.should_see("quality")
        await user.should_not_see("not read yet")

    async def test_a_sensor_that_cannot_be_calibrated_says_so(
        self,
        user: User,
        connected: None,
    ) -> None:
        """Every sensor node carries the calibration methods.

        The address book therefore cannot tell a Hamilton probe from a
        spectral one, and the page used to offer an Apply button on the
        biomass sensor that silently did nothing. Asking the sensor is
        what distinguishes them.
        """
        await user.open("/reactor/R0/calibration/sensors")
        await user.should_see("does not support calibration")

    async def test_pump_page_builds(
        self,
        user: User,
        connected: None,
    ) -> None:
        """The pump selector renders even before a run is read."""
        await user.open("/reactor/R0/calibration/pumps")
        await user.should_see("Pump calibration")

    async def test_pump_page_shows_the_installed_line_and_points(
        self,
        user: User,
        connected: None,
    ) -> None:
        """The fitted line and the points behind it.

        Neither is a published variable - only cal_a/cal_b/cal_r2 are -
        so this is what get_calibration was added for.
        """
        await user.open("/reactor/R0/calibration/pumps")
        await user.should_see("Collected points")
        await user.should_see("Duty limits")
        await user.should_see("flow = 0.001 * duty + 0")


class TestExperimentsPage:
    """The experiment interface."""

    async def test_explains_itself_without_a_database(
        self,
        user: User,
        connected: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Disabled with a reason rather than broken.

        This is the requirement that the database-dependent features be
        disabled when psycopg is not there.
        """
        monkeypatch.setattr(
            state_module.operations,
            "PSYCOPG_AVAILABLE",
            False,
        )
        await user.open("/experiments")
        await user.should_see("Experiments need a database")

    async def test_offers_creation_with_a_database(
        self,
        user: User,
        connected: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Create, and the list of what exists."""
        monkeypatch.setattr(
            state_module.operations,
            "PSYCOPG_AVAILABLE",
            True,
        )
        monkeypatch.setattr(
            state_module.operations,
            "list_experiments",
            list,
        )
        await user.open("/experiments")
        await user.should_see("New experiment")
        await user.should_see("Create")


class TestPlotsPage:
    """The live plots."""

    async def test_shows_every_panel(
        self,
        user: User,
        connected: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pH, dissolved oxygen, temperature and biomass."""
        monkeypatch.setattr(
            state_module.operations,
            "PSYCOPG_AVAILABLE",
            False,
        )
        await user.open("/reactor/R0/plots")
        await user.should_see("pH")
        await user.should_see("Dissolved oxygen")
        await user.should_see("Temperature")
        await user.should_see("Biomass")

    async def test_offers_the_window_and_channel_selectors(
        self,
        user: User,
        connected: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Both selectors the requirements name."""
        monkeypatch.setattr(
            state_module.operations,
            "PSYCOPG_AVAILABLE",
            False,
        )
        await user.open("/reactor/R0/plots")
        await user.should_see("Window")
        await user.should_see("Biomass channels")

    async def test_says_when_there_is_no_history(
        self,
        user: User,
        connected: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Live-only is a usable mode, but the operator should know."""
        monkeypatch.setattr(
            state_module.operations,
            "PSYCOPG_AVAILABLE",
            False,
        )
        await user.open("/reactor/R0/plots")
        await user.should_see("No database")


class TestHeader:
    """The bar every page carries."""

    async def test_shows_the_connection_state(
        self,
        user: User,
        connected: None,
    ) -> None:
        """Connected, reconnecting and disconnected are distinct."""
        await user.open("/")
        await user.should_see("connected")

    async def test_recording_is_separate_from_experiments(
        self,
        user: User,
        connected: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Recording is startable outside the experiment interface.

        The requirements ask for that explicitly.
        """
        monkeypatch.setattr(
            state_module.operations,
            "PSYCOPG_AVAILABLE",
            True,
        )
        await user.open("/reactor/R0")
        await user.should_see("Record")

    async def test_settings_dialog_applies_and_reads_back(
        self,
        user: User,
        connected: FakeClient,
    ) -> None:
        """The stable header dialog updates the shared sampling period."""
        await user.open("/")
        user.find("Settings").click()
        await user.should_see("Sampling settings")
        period_input = next(
            iter(user.find("Sampling period (seconds)").elements),
        )
        period_input.set_value(12.0)
        user.find("Apply").click()

        await user.should_see("sampling period set")
        assert connected.server_period == 12.0
        assert state_module.STATE.period == 12.0

    async def test_settings_button_has_explicit_contrast(
        self,
        user: User,
        connected: FakeClient,
    ) -> None:
        """The white header button uses readable primary-colored content."""
        await user.open("/")
        settings = next(iter(user.find("Settings").elements))

        assert settings.background_color == "white"
        assert settings.props["text-color"] == "primary"
        assert settings.props["unelevated"] is True

    async def test_settings_rejection_keeps_the_readback(
        self,
        user: User,
        connected: FakeClient,
    ) -> None:
        """A rejected candidate is replaced by the server-owned value."""
        connected.sampling_result = (False, "PID autotuning is active")
        await user.open("/")
        user.find("Settings").click()
        period_input = next(
            iter(user.find("Sampling period (seconds)").elements),
        )
        period_input.set_value(20.0)
        user.find("Apply").click()

        await user.should_see("PID autotuning is active")
        assert connected.server_period == 10.0
        assert state_module.STATE.period == 10.0
        assert period_input.value == 10.0

    async def test_settings_controls_disable_without_a_writable_connection(
        self,
        user: User,
        disconnected: None,
    ) -> None:
        """No global command is offered over a dead OPC session."""
        await user.open("/")

        settings = next(iter(user.find("Settings").elements))
        apply = next(iter(user.find("Apply").elements))
        assert settings.enabled is False
        assert apply.enabled is False
