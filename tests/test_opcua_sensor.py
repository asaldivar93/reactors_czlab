"""Tests for the sensor node's calibration surface and channel indices.

Driven through a stub node that captures what init_node registers, the
way test_opcua_pairing.py does - only asyncua is needed, not a running
server. This matters here because ``tests/`` deliberately does not
import ``core.sensor``, so the sensor itself is a duck type.
"""

from __future__ import annotations

import pytest
from asyncua import ua

from reactors_czlab.core.hamilton import CalibrationStatus
from reactors_czlab.opcua.sensor import UNSUPPORTED_STATUS, SensorOpc


async def _call(method, *args):
    """Invoke a captured @uamethod callback the way the server would."""
    result = await method(ua.NodeId(), *(ua.Variant(a) for a in args))
    return [item.Value for item in result]


class _CapturingVariable:
    """Stand-in for a variable node that records its properties."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.properties: dict[str, object] = {}
        self.writable = False

    async def set_writable(self) -> None:
        """Record that the server made this channel writable."""
        self.writable = True

    async def write_attribute(self, *args, **kwargs) -> None:
        """Descriptions are not what these tests are about."""

    async def add_property(self, idx, name, value) -> None:
        """Capture a property so the test can read it back."""
        self.properties[name] = value


class _CapturingNode:
    """Stand-in for an asyncua node that records what was added."""

    def __init__(self, name: str = "R0:sensors") -> None:
        self.name = name
        self.methods: dict[str, object] = {}
        self.variables: list[_CapturingVariable] = []
        self.children: list[_CapturingNode] = []

    async def add_object(self, idx, name) -> _CapturingNode:
        """Capture the sensor object node."""
        child = _CapturingNode(name)
        self.children.append(child)
        return child

    async def add_variable(self, idx, name, value) -> _CapturingVariable:
        """Capture a channel variable."""
        var = _CapturingVariable(name)
        self.variables.append(var)
        return var

    async def add_method(self, idx, name, callback, *args, **kwargs) -> None:
        """Capture the callback under its bare method name."""
        self.methods[name.split(":")[-1]] = callback

    async def read_browse_name(self) -> ua.QualifiedName:
        """Enough of a browse name for the logging in init_node."""
        return ua.QualifiedName(self.name, 2)


class FakeHamilton:
    """A sensor duck type that answers the calibration calls."""

    def __init__(self, sensor_id: str, channels: list) -> None:
        """Store the identity and channels init_node walks."""
        self.id = sensor_id
        self.channels = channels
        self.status: CalibrationStatus | None = CalibrationStatus(
            point="cp1",
            code=0,
            value=7.0,
            quality=0.98,
            process_value=7.01,
        )
        self.reads: list[float] = []
        self.writes: list[tuple[float, float]] = []

    async def read_calibration_status(
        self,
        cal_point: float,
    ) -> CalibrationStatus | None:
        """Record the call and answer with the canned status."""
        self.reads.append(cal_point)
        return self.status

    async def write_calibration(
        self,
        cal_point: float,
        cal_value: float,
    ) -> tuple[str, float, float]:
        """Record the call and report success."""
        self.writes.append((cal_point, cal_value))
        return ("ok", 0.98, cal_value)


class FakeChannel:
    """A channel duck type with the two fields init_node reads."""

    def __init__(self, units: str, description: str) -> None:
        """Store the browse-name suffix and the OPC description."""
        self.units = units
        self.description = description


@pytest.fixture
async def sensor_node():
    """A SensorOpc built against a stub parent node."""
    sensor = FakeHamilton(
        "R0:ph",
        [FakeChannel("pH", "pH"), FakeChannel("oC", "degree_celsius")],
    )
    opc = SensorOpc(sensor)
    parent = _CapturingNode()
    await opc.init_node(parent, 2, "R0")
    return opc, sensor, parent.children[0]


class TestChannelIndex:
    """The index set_pairing actually wants."""

    async def test_every_channel_publishes_its_index(
        self,
        sensor_node,
    ) -> None:
        """set_pairing takes an index; browsing only yields a name.

        Without this a client had to guess the channel index from the
        order sensors happen to be declared in server_info.py.
        """
        _, _, node = sensor_node

        indices = [var.properties["ChannelIndex"] for var in node.variables]
        assert indices == [0, 1]

    async def test_the_index_matches_the_channel_order(
        self,
        sensor_node,
    ) -> None:
        """The published index addresses the right channel."""
        _, sensor, node = sensor_node

        for var, channel in zip(node.variables, sensor.channels, strict=True):
            index = var.properties["ChannelIndex"]
            assert sensor.channels[index] is channel
            assert var.name.endswith(f":{channel.units}")


class TestReadCalibrationStatus:
    """The on-demand status read the calibration screen uses."""

    async def test_reports_status_quality_value_and_process_value(
        self,
        sensor_node,
    ) -> None:
        """All four out-arguments carry the sensor's answer."""
        _, sensor, node = sensor_node

        result = await _call(node.methods["read_calibration_status"], 1.0)

        assert sensor.reads == [1.0]
        assert result == [
            "ok",
            pytest.approx(0.98),
            pytest.approx(7.0),
            pytest.approx(7.01),
        ]

    async def test_a_sensor_with_no_calibration_says_unsupported(
        self,
        sensor_node,
    ) -> None:
        """Biomass and simulated sensors answer rather than raise.

        SensorOpc registers this method on every sensor, so it has to
        have an answer for the ones that cannot be calibrated.
        """
        _, sensor, node = sensor_node
        sensor.status = None

        result = await _call(node.methods["read_calibration_status"], 1.0)

        assert result == [UNSUPPORTED_STATUS, 0.0, 0.0, 0.0]

    async def test_reading_a_point_never_writes_one(
        self,
        sensor_node,
    ) -> None:
        """The read path is separate from the write path end to end."""
        _, sensor, node = sensor_node

        await _call(node.methods["read_calibration_status"], 2.0)

        assert sensor.writes == []


class TestWriteCalibration:
    """The existing calibration method still behaves."""

    async def test_writes_the_point_and_reports_the_outcome(
        self,
        sensor_node,
    ) -> None:
        """Out-argument order is Status, Quality, Value."""
        _, sensor, node = sensor_node

        result = await _call(node.methods["calibration"], 1.0, 7.0)

        assert sensor.writes == [(1.0, 7.0)]
        assert result == ["ok", pytest.approx(0.98), pytest.approx(7.0)]
