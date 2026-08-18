"""Sensors Definitions."""

from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, NamedTuple

from reactors_czlab.core.data import ERROR_VALUE, PhysicalInfo
from reactors_czlab.core.hamilton import (
    CALIBRATION_POINTS,
    PROCESS_VALUE_WORDS,
    STATUS_WORDS,
    VALUE_WORDS,
    CalibrationStatus,
    calibration_point_name,
)
from reactors_czlab.core.hardware import IN_RASPBERRYPI
from reactors_czlab.core.modbus import (
    REGISTERS_PER_VALUE,
    ModbusError,
    ModbusRequest,
    valid_baudrates,
)

if TYPE_CHECKING:
    from typing import ClassVar

    from reactors_czlab.core.modbus import ModbusHandler

if IN_RASPBERRYPI:
    from adafruit_as7341 import AS7341

# Serialize access to the shared i2c bus. Defined unconditionally so
# SpectralSensor is importable (and unit testable) off the PLC.
_i2c_lock = asyncio.Lock()
_i2c_executor = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="i2c",
)

_logger = logging.getLogger("server.sensors")


class _RegisterInfo(NamedTuple):
    address: int
    num: int


class Sensor(ABC):
    """Base sensor."""

    def __init__(
        self,
        identifier: str,
        config: PhysicalInfo,
    ) -> None:
        """Instance a Base sensor class.

        Parameters
        ----------
        identifier:
            A unique identifier for the sensor
        config:
            A PhysicalInfo dataclass with sensor information

        """
        self.id = identifier
        self.sensor_info = config
        self.address = config.address
        self.channels = config.channels

    def __repr__(self) -> str:
        """Print sensor id."""
        return f"{type(self).__name__}(id: {self.id})"

    @abstractmethod
    async def read(self) -> None:
        """Read all sensor channels."""

    async def write_calibration(
        self,
        cal_point: float,
        cal_value: float,
    ) -> tuple[str, float, float]:
        """Do a one point calibration.

        The default reports that the sensor cannot be calibrated over the
        bus. Sensors that support it (HamiltonSensor) override this.
        """
        _logger.warning(
            "%s does not support calibration (point %s, value %s)",
            self.id,
            cal_point,
            cal_value,
        )
        return ("unsupported", 0.0, 0.0)

    async def read_calibration_status(
        self,
        cal_point: float,
    ) -> CalibrationStatus | None:
        """Report what a calibration point currently holds.

        The default reports that the sensor has no calibration points to
        read. Sensors that support it (HamiltonSensor) override this.

        Returns
        -------
        CalibrationStatus | None
            ``None`` when the sensor cannot be calibrated over the bus,
            which is what every non-Hamilton sensor returns. A caller
            showing this to an operator says "unsupported"; it is not an
            error.

        """
        _logger.debug(
            "%s has no calibration point %s to read",
            self.id,
            cal_point,
        )
        return None


class RandomSensor(Sensor):
    """Sensor producing gaussian noise, for running without hardware."""

    async def read(self) -> None:
        """Set every channel to a value with a gaussian distribution."""
        await asyncio.sleep(0.15)
        debug_msg = []
        for chn in self.channels:
            value = round(random.gauss(35, 1), 2)
            chn.value = value
            debug_msg.append([chn.description, value])
        _logger.debug("In %s - %s", self.id, debug_msg)


class HamiltonSensor(Sensor):
    """Hamilton sensors common functions.

    Summary of relevant registers.

    Common
    ----
    - Operator:
    Start: 4288, No: 4, Reg1/Reg2: Operator Level Reg3/Reg4: password Level: password
    - Address:
    Start: 4096, No: 2, Reg1/Reg2: device address Level: S
    BaudRate:
    - Start: 4102, No: 2, Reg1/Reg2: baudrate Level: S
    PMC1: (Units Available in register 2408)
    - Start: 2090, No: 10, Reg1/Reg2: Selected Unit Reg3/Reg4: PMC1 Reg5/Reg4: Measurement Status
        Reg7/Reg8:min_val Reg9/Reg10: max_val Level: U,A,S
    PMC6: (Units Available in register 2088)
        Start: 2410, No: 10, Reg1/Reg2: Selected Unit Reg3/Reg4: PMC1 Reg5/Reg4: Measurement Status
        Reg7/Reg8:min_val Reg9/Reg10: max_val Level: U,A,S
    PA9 (moving average):
        Start: 3370, No: 2, Reg1/Reg2: Selected Unit Reg3/Reg4: Value for PA9 (1-16, default: 2)
        Level: U,A,S
    CP1Status:
        Start: 5158, No: 6, Reg1/Reg2: status Reg3/Reg4: unit Reg5/6: value level: A,S
    CP1:
        Start: 5162, No: 2, Reg1/Reg2: value level: A,S
    CP2Status:
        Start: 5190, No: 6, Reg1/Reg2: status Reg3/Reg4: unit Reg5/6: value level: A,S
    CP2:
        Start: 5194, No: 2, Reg1/Reg2: value level: A,S
    QualityIndicator:
        Start: 4872, No: 2, Reg1/Reg2: value level: U,A,S

    Dissolved Oxygen:
    ----
    PA1 (salinity):
    PA2 (air pressure):

    Incyte:
    ----

    Calibration procedure:
    ----
    The Arc Sensor family has a unique calibration routine. When initiating
    the calibration, the data set of the sensor is automatically traced back
    within the last 3 minutes and a decision is made immediately if the
    calibration is successful or not. The criteria for a successful calibration
    are:
        -the stability of pH value and temperature over the last 3 minutes
        -the currently measured pH value fits to one of the calibration
        standards defined in the selected set of calibration standards
        -the limits of slope and offset at pH 7 have to be met
    """

    REGISTERS: ClassVar = {
        # Registers in Hamilton start with index 0
        "operator": _RegisterInfo(4288 - 1, 4),
        "address": _RegisterInfo(4096 - 1, 2),
        "baudrate": _RegisterInfo(4102 - 1, 2),
        "pmc1": _RegisterInfo(2090 - 1, 10),
        "pmc6": _RegisterInfo(2410 - 1, 10),
        "cp1_info": _RegisterInfo(5152 - 1, 6),
        "cp2_info": _RegisterInfo(5184 - 1, 6),
        "cp6_info": _RegisterInfo(5312 - 1, 6),
        "cp1_status": _RegisterInfo(5158 - 1, 6),
        "cp2_status": _RegisterInfo(5190 - 1, 6),
        "cp6_status": _RegisterInfo(5318 - 1, 6),
        "cp1": _RegisterInfo(5162 - 1, 2),
        "cp2": _RegisterInfo(5194 - 1, 2),
        "quality": _RegisterInfo(4872 - 1, 2),
    }

    OPERATOR_LEVELS: ClassVar = {
        "user": {"code": 0x03, "Password": 0},
        "administrator": {"code": 0x0C, "Password": 18111978},
        "specialist": {"code": 0x30, "Password": 16021966},
    }

    #: Calibration points that have a writable register in REGISTERS.
    #: Aliased from core.hamilton so there is one list, not two that can
    #: drift: ``calibration_point_name`` validates against that one.
    CALIBRATION_POINTS: ClassVar = CALIBRATION_POINTS

    def __init__(
        self,
        identifier: str,
        config: PhysicalInfo,
        modbus_handler: ModbusHandler,
    ) -> None:
        """Instance a Base sensor class.

        Parameters
        ----------
        identifier:
            A unique identifier for the sensor
        config:
            A class with sensor information
        modbus_handler:
            A sentinel which handles modbus communications for all sensors

        """
        super().__init__(identifier, config)
        self.modbus_handler = modbus_handler

    def __repr__(self) -> str:
        """Print sensor id."""
        return f"HamiltonSensor(id: {self.id}, model: {self.sensor_info.model}, addr: {self.address})"

    async def read_holding_registers(self, param: str) -> list[int]:
        """Read holding registers.

        Parameters
        ----------
        param: str
            One of the available channels in self.REGISTERS

        """
        try:
            register = self.REGISTERS[param]
        except KeyError as err:
            error_message = (
                f"Invalid register {param!r}, expected one of "
                f"{sorted(self.REGISTERS)}"
            )
            raise KeyError(error_message) from err

        request = ModbusRequest(
            operation="read_holding",
            unit=self.address,
            register=register.address,
            count=register.num,
        )
        return await self.modbus_handler.process_request(request)

    async def write_registers(
        self,
        param: str,
        values: list[int | float],
    ) -> list[int]:
        """Write multiple registers.

        Parameters
        ----------
        param: str
            One of self.REGISTERS.keys()
        values: list[int | float]
            A list of value to write

        """
        try:
            register = self.REGISTERS[param]
        except KeyError as err:
            error_message = (
                f"Invalid register {param!r}, expected one of "
                f"{sorted(self.REGISTERS)}"
            )
            raise KeyError(error_message) from err

        request = ModbusRequest(
            operation="write",
            unit=self.address,
            register=register.address,
            values=values,
        )
        return await self.modbus_handler.process_request(request)

    async def set_operator_level(self, level_name: str) -> None:
        """Set the operator level for the sensor based on the operation type.

        Raises
        ------
        KeyError
            If level_name is not one of OPERATOR_LEVELS.
        ModbusError
            If the sensor did not accept the new level. Callers must not
            continue as if the level had been raised.

        """
        try:
            level = self.OPERATOR_LEVELS[level_name]
        except KeyError as err:
            error_message = (
                f"Operator level should be one of "
                f"{sorted(self.OPERATOR_LEVELS)}, got {level_name!r}"
            )
            raise KeyError(error_message) from err

        await self.write_registers("operator", list(level.values()))
        _logger.debug("Operator level %r set on %s", level_name, self.id)

    async def set_address(
        self,
        new_address: int,
    ) -> None:
        """Set a new address for the sensor."""
        try:
            await self.set_operator_level("specialist")
            await self.write_registers("address", [new_address])
            self.address = new_address
            await self.set_operator_level("user")
            _logger.info("Updated address of unit %s: %s", self.id, new_address)
        except ModbusError:
            _logger.warning("Failed to update address of unit %s", self.id)
            raise

    async def set_baudrate(self, baudrate: int) -> None:
        """Update the baudrate for the sensor.

        Raises
        ------
        KeyError
            If the baudrate is not supported by the sensor.
        ModbusError
            If the sensor did not accept the new baudrate.

        """
        try:
            baudrate_code = valid_baudrates[baudrate]
        except KeyError as err:
            error_message = (
                f"Baudrate should be one of {sorted(valid_baudrates)}, "
                f"got {baudrate}"
            )
            raise KeyError(error_message) from err

        try:
            await self.set_operator_level("specialist")
            await self.write_registers("baudrate", [baudrate_code])
            await self.set_operator_level("user")
            _logger.info(
                "Updated baudrate of unit %s: %s",
                self.id,
                baudrate,
            )
        except ModbusError:
            _logger.warning("Failed to set baudrate of unit %s", self.id)
            raise

    async def _read_calibration_point(self, cp: str) -> CalibrationStatus:
        """Read one calibration point's status block, quality and PMC1.

        The bus half of ``read_calibration_status``, split out so both
        the read path and the write path use exactly one implementation
        of "what does this point hold". Assumes the caller has already
        raised the operator level: ``CP{n}Status`` is readable at
        administrator or specialist level only.

        Raises
        ------
        ModbusError
            If any of the three reads failed. The caller decides what a
            failure means - for a write it is a different outcome than
            for a plain read.

        """
        status_response = await self.read_holding_registers(f"{cp}_status")
        code = self.modbus_handler.decode(
            status_response[STATUS_WORDS],
            "int",
        )
        value = self.modbus_handler.decode(
            status_response[VALUE_WORDS],
            "float",
        )

        quality_response = await self.read_holding_registers("quality")
        quality = self.modbus_handler.decode(
            quality_response[STATUS_WORDS],
            "float",
        )

        # The sensor's primary measurement. Named for what it is rather
        # than for pH: this same code path serves a VisiFerm, where PMC1
        # is dissolved oxygen.
        process_response = await self.read_holding_registers("pmc1")
        process_value = self.modbus_handler.decode(
            process_response[PROCESS_VALUE_WORDS],
            "float",
        )

        return CalibrationStatus(
            point=cp,
            code=code,
            value=value,
            quality=quality,
            process_value=process_value,
        )

    async def read_calibration_status(
        self,
        cal_point: float,
    ) -> CalibrationStatus | None:
        """Report what a calibration point currently holds.

        Reads only - it never writes a calibration - so an operator can
        see the state of CP1 and CP2 before deciding to change either.

        The operator level is raised for the read because ``CP{n}Status``
        is not readable as ``user``, and always dropped back afterwards.

        Returns
        -------
        CalibrationStatus | None
            ``None`` if the point is not one this sensor can calibrate,
            or if the bus read failed. Both are logged.

        """
        try:
            cp = calibration_point_name(cal_point)
        except ValueError:
            _logger.warning("Cannot read calibration status of %s", self.id)
            return None

        try:
            await self.set_operator_level("specialist")
            status = await self._read_calibration_point(cp)
        except ModbusError:
            _logger.warning(
                "Failed to read %s status of unit %s",
                cp,
                self.id,
            )
            return None
        else:
            _logger.info(
                "Read %s of %s - status: %s, value: %s, quality: %s",
                cp,
                self.id,
                status.text,
                status.value,
                status.quality,
            )
            return status
        finally:
            await self._drop_to_user_level()

    async def write_calibration(
        self,
        cal_point: float,
        cal_value: float,
    ) -> tuple[str, float, float]:
        """Write a value to a calibration point and report the outcome.

        The write and the read-back that follows it are reported apart:
        a sensor that refuses the calibration returns its own status,
        while a bus failure returns ``"failed"``. Conflating the two -
        as returning ``("failed", 0.0, 0.0)`` for both used to - leaves
        an operator unable to tell a rejected calibration from a glitch
        on the RS485 line.

        The operator level is always dropped back to "user" afterwards.

        Returns
        -------
        tuple[str, float, float]
            Status text, sensor quality and the value the sensor stored.
            The order matches the OPC method's declared out-arguments.

        """
        try:
            cp = calibration_point_name(cal_point)
        except ValueError:
            _logger.warning("Cannot calibrate %s", self.id)
            return ("failed", 0.0, 0.0)

        try:
            # A failure here must abort: writing calibration registers
            # without specialist rights silently does nothing.
            await self.set_operator_level("specialist")

            await self.write_registers(cp, [float(cal_value)])

            status = await self._read_calibration_point(cp)

            _logger.info(
                "Calibration at %s - %s: %s, value: %s, quality: %s, "
                "process value: %s",
                self.id,
                cp,
                status.text,
                status.value,
                status.quality,
                status.process_value,
            )
        except ModbusError:
            _logger.warning("Error during calibration of unit %s", self.id)
            return ("failed", 0.0, 0.0)
        else:
            return (status.text, status.quality, status.value)
        finally:
            await self._drop_to_user_level()

    async def _drop_to_user_level(self) -> None:
        """Never leave the sensor sitting at specialist level.

        A failure to step back down is logged, not raised: it must not
        replace the outcome the caller is about to report.
        """
        try:
            await self.set_operator_level("user")
        except ModbusError:
            _logger.warning(
                "Failed to drop %s back to user level",
                self.id,
            )

    async def read(self) -> None:
        """Read all available channels in the sensor."""
        try:
            debug_msg = []
            for chn in self.channels:
                result = await self.read_holding_registers(chn.register)
                # Channel measurements are stored as a 32 bit value
                # across registers 2 and 3
                value = self.modbus_handler.decode(
                    result[2 : 2 + REGISTERS_PER_VALUE],
                    "float",
                )
                chn.value = round(value, 3)
                debug_msg.append([chn.description, chn.value])
            _logger.debug("In %s - %s", self.id, debug_msg)

        except ModbusError:
            # A sensor dropping off the bus is an operational problem, not a
            # debug detail: it must show up in record.log.
            _logger.warning(
                "Error during read of unit %s, channels set to %s",
                self.id,
                ERROR_VALUE,
                exc_info=True,
            )
            for chn in self.channels:
                chn.value = ERROR_VALUE


class SpectralSensor(Sensor):
    """AS7341 11 channel sensor."""

    def __init__(
        self,
        identifier: str,
        config: PhysicalInfo,
    ) -> None:
        """AS7341 spectral sensor.

        Call set_i2c() with the multiplexer channel before reading.

        Parameters
        ----------
        identifier:
            A unique identifier for the sensor
        config:
            PhysicalInfo sensor information: model, address, channels

        """
        super().__init__(identifier, config)
        self.bus: AS7341 | None = None

    def set_i2c(self, i2c: object) -> None:
        """Attach the sensor to an I2C bus (usually a TCA9548A channel)."""
        self.bus = AS7341(i2c)

    async def read(self) -> None:
        """Read every spectral channel over I2C."""
        if self.bus is None:
            _logger.warning(
                "%s has no i2c bus, channels set to %s",
                self.id,
                ERROR_VALUE,
            )
            for chn in self.channels:
                chn.value = ERROR_VALUE
            return

        loop = asyncio.get_running_loop()

        def _blocking_call() -> None:
            values = {
                "415": self.bus.channel_415nm,
                "445": self.bus.channel_445nm,
                "480": self.bus.channel_480nm,
                "515": self.bus.channel_515nm,
                "555": self.bus.channel_555nm,
                "590": self.bus.channel_590nm,
                "630": self.bus.channel_630nm,
                "680": self.bus.channel_680nm,
                "clear": self.bus.channel_clear,
                "nir": self.bus.channel_nir,
            }
            for chn in self.channels:
                chn.value = values[chn.units]
            _logger.debug("In %s: %s", self.id, values)

        try:
            async with _i2c_lock:
                await loop.run_in_executor(_i2c_executor, _blocking_call)
        except OSError:
            _logger.warning(
                "i2c read failed for %s, channels set to %s",
                self.id,
                ERROR_VALUE,
                exc_info=True,
            )
            for chn in self.channels:
                chn.value = ERROR_VALUE
