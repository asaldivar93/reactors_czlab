"""Tests for the OPC address index the pages read through.

Pure dict-in, string-out. Node id bookkeeping is exactly the kind of
thing that fails by returning None and rendering an empty page, so it
is covered here rather than discovered in a browser.
"""

from __future__ import annotations

import pytest

from reactors_czlab.gui.address import AddressBook

SENSOR_VARS = {
    "ns=2;i=10": {"reactor": "R0", "name": "ph", "channel": "pH"},
    "ns=2;i=11": {"reactor": "R0", "name": "ph", "channel": "oC"},
    "ns=2;i=12": {"reactor": "R0", "name": "do", "channel": "ppm"},
    "ns=2;i=13": {"reactor": "R1", "name": "ph", "channel": "pH"},
}

ACTUATOR_VARS = {
    "ns=2;i=20": {"reactor": "R0", "name": "pwm0", "channel": "curr_value"},
    "ns=2;i=21": {"reactor": "R0", "name": "pwm0", "channel": "total_volume"},
    "ns=2;i=22": {"reactor": "R0", "name": "pwm0", "channel": "cal_a"},
    "ns=2;i=23": {"reactor": "R0", "name": "pwm0", "channel": "kp"},
    "ns=2;i=24": {"reactor": "R0", "name": "pwm0", "channel": "setpoint"},
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


@pytest.fixture
def book() -> AddressBook:
    """An address book over a small but representative browse."""
    return AddressBook.from_mappings(SENSOR_VARS, ACTUATOR_VARS, METHODS)


class TestVariables:
    """Finding a variable's node id."""

    def test_finds_a_sensor_channel(self, book: AddressBook) -> None:
        """The three-part key is how every page asks."""
        assert book.variable("R0", "ph", "pH") == "ns=2;i=10"

    def test_finds_an_actuator_channel(self, book: AddressBook) -> None:
        """Actuator variables are indexed the same way."""
        assert book.variable("R0", "pwm0", "kp") == "ns=2;i=23"

    def test_an_unpublished_variable_is_none_not_a_raise(
        self,
        book: AddressBook,
    ) -> None:
        """A server without a variable is a page that hides a control."""
        assert book.variable("R0", "ph", "nonsense") is None
        assert book.variable("R9", "ph", "pH") is None

    def test_channels_with_the_same_name_stay_distinct(
        self,
        book: AddressBook,
    ) -> None:
        """Both ph and do publish oC; the key includes the device.

        Keying on the channel alone would make one probe's temperature
        overwrite the other's.
        """
        extended = AddressBook.from_mappings(
            {
                **SENSOR_VARS,
                "ns=2;i=14": {
                    "reactor": "R0",
                    "name": "do",
                    "channel": "oC",
                },
            },
            {},
            {},
        )
        assert extended.variable("R0", "ph", "oC") == "ns=2;i=11"
        assert extended.variable("R0", "do", "oC") == "ns=2;i=14"


class TestMethods:
    """Finding a method's node id."""

    def test_a_reactor_method_has_no_owner(self, book: AddressBook) -> None:
        """set_pairing sits on the reactor node itself."""
        assert book.method("R0", None, "set_pairing") == "ns=2;i=30"

    def test_a_device_method_is_keyed_by_its_owner(
        self,
        book: AddressBook,
    ) -> None:
        """Every sensor has a `calibration`; the owner disambiguates."""
        assert book.method("R0", "ph", "calibration") == "ns=2;i=32"
        assert book.method("R0", "pwm0", "get_calibration") == "ns=2;i=34"

    def test_has_method_lets_a_page_hide_what_is_missing(
        self,
        book: AddressBook,
    ) -> None:
        """An older server without the read-back must not break a page.

        The alternative is offering the control and failing at the call,
        which an operator reads as the sensor being broken.
        """
        assert book.has_method("R0", "ph", "read_calibration_status")
        assert not book.has_method("R0", "do", "read_calibration_status")


class TestInventory:
    """Listing what exists."""

    def test_reactors_are_sorted_and_deduplicated(
        self,
        book: AddressBook,
    ) -> None:
        """A reactor appears once however many devices it has."""
        assert book.reactors == ["R0", "R1"]

    def test_sensors_group_their_channels(self, book: AddressBook) -> None:
        """One entry per sensor, carrying its channels."""
        sensors = book.sensors("R0")
        assert sorted(sensors) == ["do", "ph"]
        assert [ref.channel for ref in sensors["ph"]] == ["oC", "pH"]

    def test_an_unknown_reactor_lists_nothing(
        self,
        book: AddressBook,
    ) -> None:
        """An empty mapping, not a KeyError, so a page renders empty."""
        assert book.sensors("R9") == {}
        assert book.actuators("R9") == {}

    def test_actuators_are_separate_from_sensors(
        self,
        book: AddressBook,
    ) -> None:
        """A pump must not turn up in the sensor panel."""
        assert "pwm0" in book.actuators("R0")
        assert "pwm0" not in book.sensors("R0")


class TestControlChannels:
    """Telling control config apart from actuator state."""

    def test_state_channels_are_excluded(self, book: AddressBook) -> None:
        """curr_value and the fitted line are not tunable settings."""
        channels = book.control_channels("R0", "pwm0")
        assert "curr_value" not in channels
        assert "total_volume" not in channels
        assert "cal_a" not in channels

    def test_config_channels_are_included(self, book: AddressBook) -> None:
        """Whatever the server publishes under ControlMethod shows up.

        Derived rather than hardcoded, so a field added to the server's
        control config appears without this module changing.
        """
        assert set(book.control_channels("R0", "pwm0")) == {"kp", "setpoint"}


class TestFromClient:
    """Indexing a connected client."""

    def test_reads_the_three_browse_dicts(self) -> None:
        """from_client is the only place that knows OpcClient's shape."""

        class FakeClient:
            sensor_vars = SENSOR_VARS
            actuator_vars = ACTUATOR_VARS
            methods = METHODS

        book = AddressBook.from_client(FakeClient())

        assert book.variable("R0", "ph", "pH") == "ns=2;i=10"
        assert book.method("R0", None, "unpair") == "ns=2;i=31"
