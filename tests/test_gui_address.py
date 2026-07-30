"""Tests for the AddressBook.

It is a pure function of the three dicts OpcClient builds when it
browses, so no server and no nicegui are needed.
"""

from __future__ import annotations

import pytest

from reactors_czlab.gui.address import AddressBook

SENSOR_VARS = {
    "ns=2;i=10": {
        "reactor": "R0",
        "name": "ph",
        "channel": "pH",
        "value": 7.0,
    },
    "ns=2;i=11": {
        "reactor": "R0",
        "name": "ph",
        "channel": "oC",
        "value": 30.0,
    },
    "ns=2;i=12": {
        "reactor": "R1",
        "name": "do",
        "channel": "ppm",
        "value": 6.0,
    },
}

ACTUATOR_VARS = {
    "ns=2;i=20": {
        "reactor": "R0",
        "name": "pwm0",
        "channel": "curr_value",
        "value": 0.0,
    },
    "ns=2;i=21": {
        "reactor": "R0",
        "name": "pwm0",
        "channel": "setpoint",
        "value": 7.0,
    },
}

METHODS = {
    "ns=2;i=30": {"reactor": "R0", "name": ["set_pairing"]},
    "ns=2;i=31": {"reactor": "R0", "name": ["unpair"]},
    "ns=2;i=32": {"reactor": "R0", "name": ["pwm0", "get_calibration"]},
    "ns=2;i=33": {"reactor": "R0", "name": ["ph", "calibration"]},
}


@pytest.fixture
def book() -> AddressBook:
    """An AddressBook over the fixture dicts."""
    return AddressBook.build(SENSOR_VARS, ACTUATOR_VARS, METHODS)


def test_reactors_are_sorted_and_unique(book) -> None:
    """The dashboard lists reactors in a stable order."""
    assert book.reactors == ("R0", "R1")


def test_sensor_channels_are_grouped_by_sensor(book) -> None:
    """One row per sensor, one value per channel."""
    sensors = book.sensors("R0")

    assert set(sensors) == {"ph"}
    assert [ref.channel for ref in sensors["ph"]] == ["oC", "pH"]


def test_actuator_channels_are_keyed_by_channel(book) -> None:
    """The config form looks up one named variable at a time."""
    actuators = book.actuators("R0")

    assert set(actuators["pwm0"]) == {"curr_value", "setpoint"}
    assert actuators["pwm0"]["setpoint"].nodeid == "ns=2;i=21"


def test_variable_resolves_a_nodeid(book) -> None:
    """Writing a config means turning a name into a node id."""
    assert book.variable("R0", "pwm0", "setpoint") == "ns=2;i=21"


def test_variable_returns_none_when_absent(book) -> None:
    """A missing variable is a refusal, not a KeyError in a page."""
    assert book.variable("R0", "pwm0", "nonesuch") is None


def test_reactor_level_methods_have_no_owner(book) -> None:
    """set_pairing hangs off the reactor node itself."""
    assert book.method("R0", None, "set_pairing") == "ns=2;i=30"


def test_owned_methods_need_their_owner(book) -> None:
    """Two actuators expose the same method name on one reactor."""
    assert book.method("R0", "pwm0", "get_calibration") == "ns=2;i=32"
    assert book.method("R0", None, "get_calibration") is None


def test_sensor_methods_resolve_too(book) -> None:
    """The calibration screens call methods owned by a sensor."""
    assert book.method("R0", "ph", "calibration") == "ns=2;i=33"
