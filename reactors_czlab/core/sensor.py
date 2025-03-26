"""Sensors Definitions."""

from __future__ import annotations

import logging
import random
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from reactors_czlab.core.modbus import (
    ModbusError,
    ModbusRequest,
    valid_baudrates,
)
from reactors_czlab.core.reactor import IN_RASPBERRYPI
from reactors_czlab.core.utils import (
    Calibration,
    Channel,
    PhysicalInfo,
    Timer,
    u16_to_float,
)

if TYPE_CHECKING:
    from typing import ClassVar

    from reactors_czlab.core.modbus import ModbusHandler

if IN_RASPBERRYPI:
    from reactors_czlab.core.reactor import rpiplc

_logger = logging.getLogger("server.sensors")

# Hamilton sensors can have addresses from 1 to 32.
# There are four types of hamilton sensors.
# We'll divide the address space this way: 1-8: ph_sensors,
# 9-16: oxygen_sensors, 17-24: incyte_sensors, 25-32: co2_sensors
PH_SENSORS = {
    "ph_0": PhysicalInfo(
        "ArcPh",
        0x01,
        3,
        [
            Channel("pH", "pH", register=2090),
            Channel("oC", "degree_celsius", register=2410),
        ],
    ),
    "ph_1": PhysicalInfo(
        "ArcPh",
        0x02,
        3,
        [
            Channel("pH", "pH", register=2090),
            Channel("oC", "degree_celsius", register=2410),
        ],
    ),
    "ph_2": PhysicalInfo(
        "ArcPh",
        0x03,
        3,
        [
            Channel("pH", "pH", register=2090),
            Channel("oC", "degree_celsius", register=2410),
        ],
    ),
}

DO_SENSORS = {
    "do_0": PhysicalInfo(
        "VisiFerm",
        0x09,
        1,
        [
            Channel("ppm", "dissolved_oxygen", register=2090),
            Channel("oC", "degree_celsius", register=2410),
        ],
    ),
    "do_1": PhysicalInfo(
        "VisiFerm",
        0x10,
        1,
        [
            Channel("ppm", "dissolved_oxygen", register=2090),
            Channel("oC", "degree_celsius", register=2410),
        ],
    ),
    "do_2": PhysicalInfo(
        "VisiFerm",
        0x11,
        1,
        [
            Channel("ppm", "dissolved_oxygen", register=2090),
            Channel("oC", "degree_celsius", register=2410),
        ],
    ),
}


class Sensor(ABC):
    """Base sensor."""

    def __init__(self, identifier: str, config: PhysicalInfo) -> None:
        """Instance a Base sensor class.

        Parameters
        ----------
        identifier: str
            A unique identifier for the sensor
        config: PhysicalInfo
            A class with sensor information: model, address,
            sample_interval, channels

        """
        self.id = identifier
        self.sensor_info = config
        self.address = config.address
        self.channels = config.channels
        # We don't need to keep a reference of the
        # timer instance inside the sensor?
        # Maybe create a list of [sensor, timer] pairs
        # Maybe group sensors by sampling interval and create
        # a single timer for the group, let reactor handle the timers?
        self.timer = Timer(config.sample_interval)
        self.timer.add_suscriber(self)
        self._sampling_event = True

    def __repr__(self) -> str:
        """Print sensor id."""
        return f"Sensor(id: {self.id})"

    def on_timer_callback(self) -> None:
        """Set sampling flag to True."""
        self._sampling_event = True
        # _logger.debug(f"Timer callback on {self}")

    @abstractmethod
    def read(self) -> None:
        """Read all sensor channels."""


class RandomSensor(Sensor):
    """Class used for testing."""

    def __init__(self, identifier: str, config: PhysicalInfo) -> None:
        """Instance a random sensor class used for testing.

        Parameters
        ----------
        identifier: str
            A unique identifier for the sensor
        config: PhysicalInfo
            A class with sensor information: model, address,
            sample_interval, channels

        """
        super().__init__(identifier, config)

    def read(self) -> None:
        """Print values with a gaussian distribution."""
        self.timer.is_elapsed()  # The timer should be called indepently of read operations?
        if self._sampling_event:
            self._sampling_event = False
            for ch in self.channels:
                ch.value = random.gauss(35, 1)


class HamiltonSensor(Sensor):
    """Hamilton sensors common functions.

    Summary of relevant registers.

    Common:
    ----
    Operator:
        Start: 4288, No: 4, Reg1/Reg2: Operator Level Reg3/Reg4: password Level: password
    Address:
        Start: 4096, No: 2, Reg1/Reg2: device address Level: S
    BaudRate:
        Start: 4102, No: 2, Reg1/Reg2: baudrate Level: S
    PMC1: (Units Available in register 2408)
        Start: 2090, No: 10, Reg1/Reg2: Selected Unit Reg3/Reg4: PMC1 Reg5/Reg4: Measurment Status
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
    the calibration, the data set of the sensor is automatically traced back within
    the last 3 minutes and a decision is made immediately if the calibration is
    successful or not. The criteria for a successful calibration are:
        -the stability of pH value and temperature over the last 3 minutes
        -the currently measured pH value fits to one of the calibration
        standards defined in the selectedset of calibration standards
        -the limits of slope and offset at pH 7 have to be met
    """

    REGISTERS: ClassVar = {
        # Registers in Hamilton star with index 0
        "operator": 4288 - 1,
        "address": 4096 - 1,
        "baudrate": 4102 - 1,
        "pmc1": 2090 - 1,
        "pmc6": 2410 - 1,
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
        identifier: str
            A unique identifier for the sensor
        config: PhysicalInfo
            A class with sensor information: model, address,
            sample_interval, channels
        modbus_handler: ModbusHandles
            A sentinel which handles modbus communications for all sensors

        """
        super().__init__(identifier, config)
        self.modbus_handler = modbus_handler

    def __repr__(self) -> str:
        """Print sensor id."""
        return f"HamiltonSensor(id: {self.id}, model: {self.sensor_info.model}, addr: {self.address})"

    def set_operator_level(self, level_name: str) -> None:
        """Set the operator level for the sensor based on the operation type."""
        operator_levels = {
            "user": {"code": 0x03, "Password": 0},
            "administrator": {"code": 0x0C, "Password": 18111978},
            "specialist": {"code": 0x30, "Password": 16021966},
        }

        # Default operator level is set to 'user'
        level = operator_levels.get(
            level_name,
            {"code": 0x03, "Password": 0},
        )
        request = ModbusRequest(
            operation="write",
            address=self.address,
            register=self.REGISTERS["operator"],
            values=list(level.values()),
        )
        try:
            # Write the operator level and password to the sensor
            self.modbus_handler.process_request(request)
            # Ensure the write operation was successful
            self.modbus_handler.get_result()
            _logger.debug(f"Operator level '{level_name}' set successfully.")
        except ModbusError as e:
            _logger.error(f"Failed to set operator level for {level_name}: {e}")
            raise

    def set_address(
        self,
        new_address: int,
    ) -> None:
        """Set a new address for the sensor."""
        _logger.info(
            f"Changing address for sensor {self.id} at address {self.address}",
        )
        request = ModbusRequest(
            operation="write",
            address=self.address,
            register=self.REGISTERS["address"],
            values=[new_address],
        )
        try:
            self.set_operator_level("specialist")
            self.modbus_handler.process_request(request)
            self.modbus_handler.get_result()  # Make sure operation succeded
            self.address = new_address
            self.set_operator_level("user")
            _logger.info(f"Updated serial interface - address:{new_address}")
        except ModbusError as e:
            _logger.exception(f"Failed to set serial interface: {e}")
            raise

    def set_baudrate(self, baudrate: int) -> None:
        """Update the baudrate for the sensor."""
        _logger.info(
            f"Changing baudrate for sensor {self.id} at address {self.address}",
        )
        _logger.warning(
            "Carefull! If you update the baudrate in the sensor \
            you need to update the serial client as well",
        )
        try:
            # Carefull! If you update the baudrate in the sensor
            # you need to update the serial client as well
            baudrate_code = valid_baudrates[baudrate]
            request = ModbusRequest(
                operation="write",
                address=self.address,
                register=self.REGISTERS["baudrate"],
                values=[baudrate_code],
            )
            try:
                self.set_operator_level("specialist")
                self.modbus_handler.process_request(request)
                self.modbus_handler.get_result()  # Make sure operation succeded
                self.set_operator_level("user")
                _logger.info(
                    f"Updated updated baudrate interface - baudrate:{baudrate}",
                )
            except ModbusError as e:
                _logger.exception(f"Failed to set serial interface: {e}")
                raise
        except KeyError:
            _logger.warning(f"Baudrate should be one of: {valid_baudrates}")

    def read(self) -> None:
        """Read all available channels in the sensor."""
        request = ModbusRequest(
            operation="read",
            address=self.address,
            register=0,
            count=10,
        )
        self.timer.is_elapsed()
        if self._sampling_event:
            self._sampling_event = False
            for chn in self.channels:
                try:
                    request.register = chn.register - 1
                    self.modbus_handler.process_request(request)
                    result = self.modbus_handler.get_result()
                    # Channel measurments are stored as u16 vars
                    # in registers 2 and 3
                    low, high = result[2], result[3]
                    # convert two u16 to float32
                    chn.value = u16_to_float(low, high)

                except ModbusError as e:
                    _logger.exception(e)
                    chn.value = -0.111

    def set_measurement_configs(
        self,
        config_params: dict[int, list[int]],
    ) -> None:
        """Set measurement configurations for the sensor."""
        _logger.info(
            f"Setting measurement configs for sensor {self.id} at address {self.address}",
        )
        request = ModbusRequest(
            operation="write",
            address=self.address,
            register=0,
        )
        try:
            for param, values in config_params.items():
                request.register = param
                request.values = values
                self.modbus_handler.process_request(request)
                # Ensure the write operation was successful
                self.modbus_handler.get_result()
            _logger.info("Measurement configs set successfully.")
        except ModbusError as e:
            _logger.error(f"Failed to set measurement configs: {e}")
            raise

    def hex_to_float(self, registers: list[int]) -> float:
        """Convert the raw registers to a float."""
        if not registers or len(registers) < 2:
            error_message = "Invalid register data provided"
            raise ModbusError(error_message)
        raw = (registers[0] << 16) + registers[1]
        return struct.unpack(">f", raw.to_bytes(4, byteorder="big"))[0]


class AnalogSensor(Sensor):
    """Class for reading analog channels from the Raspberry."""

    def __init__(self, identifier: str, config: PhysicalInfo) -> None:
        """Analog sensor class. Analog input pins Range(0, 4095) (0-10V).

        Parameters
        ----------
        identifier: str
            A unique identifier for the sensor
        config: PhysicalInfo
            A class with sensor information: model, address,
            sample_interval, channels
        modbus_handler: ModbusHandles
            A sentinel which handles modbus communications for all sensors

        """
        super().__init__(identifier, config)
        self.cal = None
        if IN_RASPBERRYPI:
            for chn in self.channels:
                rpiplc.pin_mode(chn.pin, rpiplc.INPUT)

    def __repr__(self) -> str:
        """Print sensor id."""
        return f"AnalogSensor(id: {self.id}, channels: {self.channels})"

    def read(self) -> None:
        """Read analog values."""
        self.timer.is_elapsed()
        if IN_RASPBERRYPI and self._sampling_event:
            self._sampling_event = False
            for chn in self.channels:
                analog = rpiplc.analog_read(chn.pin)
                cal = chn.calibration
                value = self.get_value(analog, cal) if cal else analog
                chn.value = value

    def get_value(self, analog: float, cal: Calibration) -> float:
        """Apply a linear transformation to an analog value."""
        return cal.a * analog + cal.b

    def set_calibration(self, cal: list[list]) -> None:
        """Set calibration values for all the channels.

        Input:
        -----
        cal: list[list]
            A list of lists with [a, b] pairs of linear regression
            parameters for each channel
        """
        for i, par in enumerate(cal):
            self.channels[i].calibration = Calibration(par[0], par[1])
