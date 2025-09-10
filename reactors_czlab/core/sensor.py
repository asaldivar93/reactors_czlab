"""Sensors Definitions."""

from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, NamedTuple

from reactors_czlab.core.data import Calibration, PhysicalInfo
from reactors_czlab.core.modbus import (
    ModbusError,
    ModbusRequest,
    valid_baudrates,
)
from reactors_czlab.core.reactor import IN_RASPBERRYPI
from reactors_czlab.server_info import VERBOSE

if TYPE_CHECKING:
    from typing import ClassVar

    from reactors_czlab.core.modbus import ModbusHandler

if IN_RASPBERRYPI:
    from adafruit_as7341 import AS7341

    from reactors_czlab.core.reactor import rpiplc

    _i2c_lock = asyncio.Lock()  # Serialize access to i2c channel
    _i2c_executor = ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="i2c",
    )


ERROR_VAL = -0.111

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
        return f"Sensor(id: {self.id})"

    def __eq__(self, other: object) -> bool:
        """Test equality by senor id."""
        this = self.id
        return this == other

    @abstractmethod
    async def read(self) -> None:
        """Read all sensor channels."""


class RandomSensor(Sensor):
    """Class used for testing."""

    def __init__(
        self,
        identifier: str,
        config: PhysicalInfo,
    ) -> None:
        """Instance a random sensor class used for testing.

        Parameters
        ----------
        identifier:
            A unique identifier for the sensor
        config:
            A PhysicalInfo dataclass with sensor information

        """
        super().__init__(identifier, config)

    async def read(self) -> None:
        """Print values with a gaussian distribution."""
        await asyncio.sleep(0.15)
        debug_msg = []
        for chn in self.channels:
            value = round(random.gauss(35, 1), 2)
            chn.value = value
            debug_msg.extend([[chn.description, value]])
        _logger.debug(f"In {self.id} - {debug_msg}")


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
    - Start: 2090, No: 10, Reg1/Reg2: Selected Unit Reg3/Reg4: PMC1 Reg5/Reg4: Measurment Status
        Reg7/Reg8:min_val Reg9/Reg10: max_val Level: U,A,S
    PMC6: (Units Available in register 2088)
        Start: 2410, No: 10, Reg1/Reg2: Selected Unit Reg3/Reg4: PMC1 Reg5/Reg4: Measurment Status
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
    QualityIndictator:
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
        standards defined in the selectedset of calibration standards
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
            request = ModbusRequest(
                operation="read_holding",
                address=self.address,
                register=register.address,
                count=register.num,
            )
            return await self.modbus_handler.process_request(request)
        except KeyError as err:
            error_message = f"Invalid register: {self.REGISTERS.keys()}"
            raise KeyError(error_message) from err
        except ModbusError as err:
            raise ModbusError from err

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
            request = ModbusRequest(
                operation="write",
                address=self.address,
                register=register.address,
                values=values,
            )
            return await self.modbus_handler.process_request(request)
        except KeyError as err:
            error_message = f"Invalid register: {self.REGISTERS.keys()}"
            raise KeyError(error_message) from err
        except ModbusError as err:
            raise ModbusError from err

    async def set_operator_level(self, level_name: str) -> None:
        """Set the operator level for the sensor based on the operation type."""
        try:
            level = self.OPERATOR_LEVELS[level_name]
            await self.write_registers("operator", list(level.values()))
            _logger.debug(f"Operator level '{level_name}' set successfully.")
        except ModbusError:
            error_message = f"Failed to set operator level for unit {self.id}"
            _logger.exception(error_message)
        except KeyError:
            error_message = f"Operator level should be \
                one of {self.OPERATOR_LEVELS.keys()}"
            _logger.exception(error_message)

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
            _logger.info(f"Updated address of unit {self.id}: {new_address}")
        except ModbusError:
            error_message = f"Failed to update address of unit {self.id}"
            _logger.exception(error_message)
            raise

    async def set_baudrate(self, baudrate: int) -> None:
        """Update the baudrate for the sensor."""
        try:
            baudrate_code = valid_baudrates[baudrate]
            await self.set_operator_level("specialist")
            await self.write_registers("baudrate", [baudrate_code])
            await self.set_operator_level("user")
            _logger.info(
                f"Updated updated baudrate interface - baudrate:{baudrate}",
            )
        except ModbusError:
            error_message = f"Failed to set badu_rate of unit {self.id}"
            _logger.exception(error_message)
        except KeyError:
            error_message = f"Baudrate should be one of: {valid_baudrates}"
            _logger.exception(error_message)

    async def write_calibration(self, cp: str, value: float) -> None:
        """Write value to calibration points."""
        try:
            await self.set_operator_level("specialist")

            await self.write_registers(cp, [value])

            status_response = await self.read_holding_registers(cp + "_status")
            low, high = status_response[0], status_response[1]
            status = self.modbus_handler.decode((low, high), "int")
            low, high = status_response[4], status_response[5]
            cal_value = self.modbus_handler.decode((low, high), "float")

            quality_response = await self.read_holding_registers("quality")
            low, high = quality_response[0], quality_response[1]
            quality = self.modbus_handler.decode((low, high), "float")

            ph_response = await self.read_holding_registers("pmc1")
            low, high = ph_response[2], ph_response[3]
            ph = self.modbus_handler.decode((low, high), "float")
            info_message = f"Calibration at {self.id} - status: {status}, \
                            cp: {cal_value}, quality: {quality}, pH: {ph}"
            _logger.info(info_message)
            await self.set_operator_level("user")
        except ModbusError:
            error_message = f"Error during calibration of unit {self.id}"
            _logger.exception(error_message)

    async def read(self) -> None:
        """Read all available channels in the sensor."""
        try:
            debug_msg = []
            for chn in self.channels:
                result = await self.read_holding_registers(chn.register)
                # Channel measurments are stored as u16 vars
                # in registers 2 and 3
                low, high = result[2], result[3]
                value = self.modbus_handler.decode((low, high), "float")
                chn.value = round(value, 3)
                debug_msg.append([[chn.description, value]])
            _logger.debug(f"In {self.id} - {debug_msg}")

        except ModbusError as err:
            error_message = f"Error during read of unit {self.id}\n {err}"
            _logger.debug(error_message)
            for chn in self.channels:
                chn.value = ERROR_VAL


class AnalogSensor(Sensor):
    """Class for reading analog channels from the Raspberry."""

    def __init__(
        self,
        identifier: str,
        config: PhysicalInfo,
    ) -> None:
        """Analog sensor class. Analog input pins Range(0, 4095) (0-10V).

        Parameters
        ----------
        identifier:
            A unique identifier for the sensor
        config:
            PhysicalInfo sensor information: model, address,
            sample_interval, channels

        """
        super().__init__(identifier, config)
        self.cal = None
        if IN_RASPBERRYPI:
            for chn in self.channels:
                rpiplc.pin_mode(chn.pin, rpiplc.INPUT)

    def __repr__(self) -> str:
        """Print sensor id."""
        return f"AnalogSensor(id: {self.id}, channels: {self.channels})"

    def get_value(self, analog: float, cal: Calibration) -> float:
        """Apply a linear transformation to an analog value."""
        return cal.a * analog + cal.b

    def set_calibration(
        self,
        cal: list[tuple[str, tuple[float, float]]],
    ) -> None:
        """Set calibration values for all the channels.

        Input:
        -----
        cal: list[list]
            A list of lists with [a, b] pairs of linear regression
            parameters for each channel
        """
        for i, info in enumerate(cal):
            file, pars = info[0], info[1]
            self.channels[i].calibration = Calibration(file, pars[0], pars[1])

    async def read(self) -> None:
        """Read analog values."""
        await asyncio.sleep(0.0005)
        if IN_RASPBERRYPI:
            for chn in self.channels:
                analog = rpiplc.analog_read(chn.pin)
                cal = chn.calibration
                value = self.get_value(analog, cal) if cal else analog
                chn.value = value
                if VERBOSE:
                    _logger.debug(f"In {self.id} {chn.description}: {value}")


class SpectralSensor(Sensor):
    """AS7341 11 channel sensor."""

    def __init__(
        self,
        identifier: str,
        config: PhysicalInfo,
    ) -> None:
        """AS7341 spectral sensor.

        Parameters
        ----------
        identifier:
            A unique identifier for the sensor
        config:
            PhysicalInfo sensor information: model, address,
            sample_interval, channels
        i2c:
            The I2C bus device

        """
        super().__init__(identifier, config)
        self.bus: None | AS7341 = None

    def set_i2c(self, i2c: busio.I2C) -> None:
        self.bus = AS7341(i2c)

    async def read(self) -> None:
        """Read spectral sensor."""
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
            _logger.debug(f"In {self.id}: {values}")

        async with _i2c_lock:
            await loop.run_in_executor(_i2c_executor, _blocking_call)
