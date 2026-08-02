"""Bus orchestration around a Hamilton calibration point.

``core.hamilton`` covers what the registers mean; this covers the order
of operations around them - raising the operator level, reading the
three blocks, and always dropping back down.

Skipped where pymodbus is absent. ``tests/`` deliberately does not
import ``core.sensor`` in the ordinary case (CLAUDE.md), and the client
extra carries no pymodbus, so this runs on the Pi and anywhere the
server extra is installed and stays out of the way elsewhere.
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "pymodbus",
    reason="core.sensor imports pymodbus; install the server extra",
)

from reactors_czlab.core.data import PhysicalInfo, PlcOutput
from reactors_czlab.core.modbus import ModbusError
from reactors_czlab.core.sensor import HamiltonSensor

#: CP status blocks: status words, unit words, value words. The fake
#: decode below is hundredths, so 700 reads back as 7.00.
GOOD_STATUS = [0, 0, 0, 0, 700, 0]
REFUSED_STATUS = [0, 7, 0, 0, 401, 0]


class FakeModbusHandler:
    """Records what was asked of the bus and replays canned answers.

    ``decode`` is keyed on the words handed to it rather than on call
    order, so a change in the order of the three reads inside
    ``_read_calibration_point`` does not silently change what the test
    thinks it is asserting.
    """

    def __init__(self, responses: dict[str, list[int]]) -> None:
        """Store the register block each named read should return."""
        self.responses = responses
        self.reads: list[str] = []
        self.writes: list[tuple[str, list]] = []
        self.levels: list[str] = []
        self.fail_on: set[str] = set()

    async def process_request(self, request: object) -> list[int]:
        """Unused: the sensor methods are patched at a higher level."""
        raise NotImplementedError

    def decode(self, words: list[int], kind: str) -> float:
        """Decode a word pair the way the real handler would."""
        if kind == "int":
            return words[1]
        # Enough of a float decode for the assertions below - hundredths
        # of the first word. The real word order is exercised by the
        # modbus module, not here.
        return float(words[0]) / 100.0


def _build_sensor(handler: FakeModbusHandler) -> HamiltonSensor:
    """A Hamilton sensor wired to the fake handler."""
    config = PhysicalInfo(
        model="ArcPh",
        address=0x01,
        type=PlcOutput.digital,
        channels=[],
    )
    sensor = HamiltonSensor("R0:ph", config, handler)

    async def read_holding_registers(param: str) -> list[int]:
        if param in handler.fail_on:
            error_message = f"bus failure on {param}"
            raise ModbusError(error_message)
        handler.reads.append(param)
        return handler.responses[param]

    async def write_registers(param: str, values: list) -> list[int]:
        if param in handler.fail_on:
            error_message = f"bus failure on {param}"
            raise ModbusError(error_message)
        handler.writes.append((param, values))
        return []

    async def set_operator_level(level_name: str) -> list[int]:
        if "operator" in handler.fail_on:
            error_message = "bus failure on operator"
            raise ModbusError(error_message)
        handler.levels.append(level_name)
        return []

    sensor.read_holding_registers = read_holding_registers
    sensor.write_registers = write_registers
    sensor.set_operator_level = set_operator_level
    return sensor


@pytest.fixture
def handler() -> FakeModbusHandler:
    """A fake bus that answers every read a status check makes."""
    return FakeModbusHandler(
        {
            "cp1_status": GOOD_STATUS,
            "cp2_status": REFUSED_STATUS,
            "quality": [98, 0],
            "pmc1": [0, 0, 701, 0, 0, 0, 0, 0, 0, 0],
        },
    )


class TestReadCalibrationStatus:
    """Reading a point without writing one."""

    async def test_reads_without_writing_a_calibration(
        self,
        handler: FakeModbusHandler,
    ) -> None:
        """The whole point of the split: no write happens on a read.

        Before this, CP status, stored value and quality were reachable
        only as a side effect of write_calibration, so an operator could
        not see the state of a point without changing it.
        """
        sensor = _build_sensor(handler)

        status = await sensor.read_calibration_status(1.0)

        assert handler.writes == []
        assert status is not None
        assert status.point == "cp1"
        assert status.ok

    async def test_reads_status_quality_and_process_value(
        self,
        handler: FakeModbusHandler,
    ) -> None:
        """All three blocks the operator panel shows are read."""
        sensor = _build_sensor(handler)

        await sensor.read_calibration_status(1.0)

        assert handler.reads == ["cp1_status", "quality", "pmc1"]

    async def test_raises_and_drops_the_operator_level(
        self,
        handler: FakeModbusHandler,
    ) -> None:
        """CP status needs specialist rights; user level is restored."""
        sensor = _build_sensor(handler)

        await sensor.read_calibration_status(1.0)

        assert handler.levels == ["specialist", "user"]

    async def test_drops_the_level_even_when_the_bus_fails(
        self,
        handler: FakeModbusHandler,
    ) -> None:
        """A sensor must never be left sitting at specialist level."""
        handler.fail_on = {"cp1_status"}
        sensor = _build_sensor(handler)

        status = await sensor.read_calibration_status(1.0)

        assert status is None
        assert handler.levels == ["specialist", "user"]

    async def test_an_invalid_point_never_touches_the_bus(
        self,
        handler: FakeModbusHandler,
    ) -> None:
        """A bad Cal_point is refused before the level is raised."""
        sensor = _build_sensor(handler)

        status = await sensor.read_calibration_status(6.0)

        assert status is None
        assert handler.levels == []
        assert handler.reads == []

    async def test_reports_a_refusal_code_from_the_sensor(
        self,
        handler: FakeModbusHandler,
    ) -> None:
        """A non-zero status code is carried through, not swallowed."""
        sensor = _build_sensor(handler)

        status = await sensor.read_calibration_status(2.0)

        assert status is not None
        assert not status.ok
        assert "7" in status.text


class TestWriteCalibration:
    """Writing a point, and what it reports afterwards."""

    async def test_writes_then_reads_the_point_back(
        self,
        handler: FakeModbusHandler,
    ) -> None:
        """The write happens at specialist level and is read back."""
        sensor = _build_sensor(handler)

        status, quality, value = await sensor.write_calibration(1.0, 7.0)

        assert handler.writes == [("cp1", [7.0])]
        assert handler.levels == ["specialist", "user"]
        assert status == "ok"
        assert quality == pytest.approx(0.98)
        assert value == pytest.approx(7.0)

    async def test_writes_the_value_as_a_float(
        self,
        handler: FakeModbusHandler,
    ) -> None:
        """Regression: an int value would encode as INT32, not FLOAT32.

        ModbusHandler._build_payload picks the encoding from the Python
        type, so an integer calibration value sent by a client would
        reach the sensor as an integer bit pattern.
        """
        sensor = _build_sensor(handler)

        await sensor.write_calibration(1.0, 7)

        (_, values) = handler.writes[0]
        assert isinstance(values[0], float)

    async def test_a_sensor_refusal_is_not_a_bus_failure(
        self,
        handler: FakeModbusHandler,
    ) -> None:
        """Regression: both used to return ("failed", 0.0, 0.0).

        An operator could not tell a calibration the sensor rejected -
        out-of-range standard, unstable reading - from a glitch on the
        RS485 line. The refusal now carries the sensor's own code and
        the value it holds.
        """
        sensor = _build_sensor(handler)

        refused, _, value = await sensor.write_calibration(2.0, 4.0)

        bus_failed, quality, zero = await _write_with_bus_failure(handler)

        assert refused != "failed"
        assert "7" in refused
        assert value != 0.0

        assert bus_failed == "failed"
        assert quality == 0.0
        assert zero == 0.0

    async def test_an_invalid_point_never_touches_the_bus(
        self,
        handler: FakeModbusHandler,
    ) -> None:
        """A bad Cal_point is refused before anything is written."""
        sensor = _build_sensor(handler)

        status, _, _ = await sensor.write_calibration(float("nan"), 7.0)

        assert status == "failed"
        assert handler.writes == []
        assert handler.levels == []


async def _write_with_bus_failure(
    handler: FakeModbusHandler,
) -> tuple[str, float, float]:
    """Run a calibration whose read-back fails on the bus."""
    handler.fail_on = {"quality"}
    sensor = _build_sensor(handler)
    return await sensor.write_calibration(1.0, 7.0)
