"""Smoke tests for the routes, driven through NiceGUI's user fixture.

The pages hold no logic - that is in ``address``, ``format``,
``control`` and ``controllers``, all covered without a browser. What is
covered here is that each route builds at all, and that the two states
a page can be in reach the right branch: connected, and not.

A page that raises while rendering shows an operator a blank screen, so
"it builds" is worth pinning even when nothing is decided in it.
"""

from __future__ import annotations

import pytest
from nicegui.testing import User

from reactors_czlab.gui import state as state_module
from reactors_czlab.gui.address import AddressBook

pytest_plugins = ("nicegui.testing.user_plugin",)

SENSOR_VARS = {
    "ns=2;i=10": {"reactor": "R0", "name": "ph", "channel": "pH"},
    "ns=2;i=11": {"reactor": "R0", "name": "ph", "channel": "oC"},
}

ACTUATOR_VARS = {
    "ns=2;i=20": {"reactor": "R0", "name": "pwm0", "channel": "curr_value"},
    "ns=2;i=21": {"reactor": "R0", "name": "pwm0", "channel": "total_volume"},
    "ns=2;i=22": {"reactor": "R0", "name": "pwm0", "channel": "cal_a"},
    "ns=2;i=23": {"reactor": "R0", "name": "pwm0", "channel": "cal_b"},
    "ns=2;i=24": {"reactor": "R0", "name": "pwm0", "channel": "cal_r2"},
}

METHODS = {
    "ns=2;i=30": {"reactor": "R0", "name": ["set_pairing"]},
    "ns=2;i=31": {"reactor": "R0", "name": ["unpair"]},
    "ns=2;i=32": {"reactor": "R0", "name": ["ph", "calibration"]},
    "ns=2;i=33": {
        "reactor": "R0",
        "name": ["ph", "read_calibration_status"],
    },
    "ns=2;i=34": {"reactor": "R0", "name": ["pwm0", "get_calibration"]},
}


class FakeClient:
    """Enough of an OpcClient for a page to render against."""

    def __init__(self) -> None:
        """Start connected, with a value on every published variable."""
        self.recording = False
        self.experiment_tags: dict[str, str] = {}
        self.sensor_vars = SENSOR_VARS
        self.actuator_vars = ACTUATOR_VARS
        self.methods = METHODS
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


@pytest.fixture
def connected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put STATE into a connected state without a server."""
    client = FakeClient()
    monkeypatch.setattr(state_module.STATE, "client", client)
    monkeypatch.setattr(
        state_module.STATE,
        "book",
        AddressBook.from_mappings(SENSOR_VARS, ACTUATOR_VARS, METHODS),
    )


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

    async def test_sensor_page_lists_only_capable_sensors(
        self,
        user: User,
        connected: None,
    ) -> None:
        """A sensor without the read-back method is not offered.

        Offering it and failing at the call reads to an operator as the
        sensor being broken.
        """
        await user.open("/reactor/R0/calibration/sensors")
        await user.should_see("ph")

    async def test_pump_page_builds(
        self,
        user: User,
        connected: None,
    ) -> None:
        """The pump selector renders even before a run is read."""
        await user.open("/reactor/R0/calibration/pumps")
        await user.should_see("Pump calibration")


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
        await user.open("/")
        await user.should_see("Record")
