"""Tests for the sensor node's calibration methods and channel indices.

The method bodies are exercised directly against stub nodes: asyncua is a
base dependency but a running server is not needed, and ``core.sensor``
(which needs pymodbus) is never imported - the fake sensor below is the
same duck type ``conftest`` uses.
"""

from __future__ import annotations

import types

import pytest
from asyncua import ua

from reactors_czlab.core.hamilton import CalibrationStatus
from reactors_czlab.opcua.sensor import SensorOpc


class _CalibratableSensor:
    """A sensor duck type that answers both calibration calls."""

    def __init__(self, identifier, channels):
        self.id = identifier
        self.channels = channels
        self.status_calls = []

    async def read(self):
        """Never called by these tests."""

    async def write_calibration(self, cal_point, cal_value):
        """Report a successful write."""
        return ("Successful", 0.9, cal_value)

    async def read_calibration_status(self, cal_point):
        """Record the request and answer with a fixed status."""
        self.status_calls.append(cal_point)
        return CalibrationStatus(
            point="cp1",
            status="Successful",
            quality=0.93,
            value=4.01,
            process_value=7.02,
        )


class _CapturingVariable:
    """An asyncua variable that records the properties added to it."""

    def __init__(self):
        self.properties = {}

    async def set_writable(self):
        """No-op."""

    async def write_attribute(self, attribute, value):
        """No-op."""

    async def add_property(self, idx, name, value):
        """Capture the property under its name."""
        self.properties[name] = value


class _CapturingNode:
    """Stand-in for an asyncua node that records children."""

    def __init__(self, name="R0:ph"):
        self.name = name
        self.methods = {}
        self.variables = []
        self.objects = {}

    async def read_browse_name(self):
        """Return something with a ``.Name``, as asyncua does."""
        return types.SimpleNamespace(Name=self.name)

    async def add_object(self, idx, name):
        """Hand back a child node."""
        child = _CapturingNode(name)
        self.objects[name] = child
        return child

    async def add_variable(self, idx, name, value, **kwargs):
        """Hand back a capturing variable."""
        variable = _CapturingVariable()
        self.variables.append((name, variable))
        return variable

    async def add_method(self, idx, name, callback, *args, **kwargs):
        """Capture the callback under its bare method name."""
        self.methods[name.split(":")[-1]] = callback


async def _call(method, *args):
    """Invoke a captured @uamethod callback the way the server would."""
    result = await method(ua.NodeId(), *(ua.Variant(a) for a in args))
    return [item.Value for item in result]


@pytest.fixture
async def sensor_node():
    """A SensorOpc initialised against capturing nodes."""
    from reactors_czlab.core.data import Channel

    sensor = _CalibratableSensor(
        "R0:ph",
        [
            Channel("pH", "pH", register="pmc1"),
            Channel("oC", "degree_celsius", register="pmc6"),
        ],
    )
    parent = _CapturingNode("R0:sensors")
    node = SensorOpc(sensor)
    await node.init_node(parent, 2, "R0")
    return node, sensor


async def test_read_calibration_status_returns_four_values(
    sensor_node,
) -> None:
    """The GUI needs status, quality, stored value and the live reading."""
    node, sensor = sensor_node

    result = await _call(
        node.node.methods["read_calibration_status"],
        1.0,
    )

    assert result == ["Successful", 0.93, 4.01, 7.02]
    assert sensor.status_calls == [1.0]


async def test_every_channel_carries_its_index(sensor_node) -> None:
    """set_pairing takes a channel index; OPC only gives channel names.

    Regression: deriving the index from browse order would rely on
    get_children() preserving insertion order, which asyncua does not
    guarantee.
    """
    node, _ = sensor_node

    indices = [
        variable.properties["ChannelIndex"]
        for _, variable in node.node.variables
    ]

    assert indices == [0, 1]


async def test_the_calibration_write_method_still_exists(
    sensor_node,
) -> None:
    """The existing method keeps its name and its three return values."""
    node, _ = sensor_node

    result = await _call(node.node.methods["calibration"], 1.0, 4.0)

    assert result == ["Successful", 0.9, 4.0]
