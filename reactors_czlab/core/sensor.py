"""Sensors Definitions."""

from __future__ import annotations

import logging
import random
import struct
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from reactors_czlab.core.modbus import ModbusError
from reactors_czlab.core.reactor import IN_RASPBERRYPI
from reactors_czlab.core.utils import Timer

if TYPE_CHECKING:
    from typing import ClassVar

    from reactors_czlab.core.modbus import ModbusHandler

if IN_RASPBERRYPI:
    from reactors_czlab.core.reactor import rpiplc

_logger = logging.getLogger("server.sensors")

# Hamilton sensors can have addresses from 1 to 32.
# There are four types of hamilton sensors.
# We'll divide the address space this way: 1-8: ph_sensors, 9-16: oxygen_sensors,
# 17-24: incyte_sensors, 25-32: co2_sensors
PH_SENSORS = {
    "ph_0": {
        "model": "ArcPh",
        "address": 0x01,
        "sample_interval": 3,
        "channels": [
            {"register": 2090, "units": "pH", "description": "pH"},
            {"register": 2410, "units": "oC", "description": "degree_celsius"},
        ],
    },
    "ph_1": {
        "model": "ArcPh",
        "address": 0x02,
        "sample_interval": 3,
        "channels": [
            {"register": 2090, "units": "pH", "description": "pH"},
            {"register": 2410, "units": "oC", "description": "degree_celsius"},
        ],
    },
    "ph_2": {
        "model": "ArcPh",
        "address": 0x03,
        "sample_interval": 3,
        "channels": [
            {"register": 2090, "units": "pH", "description": "pH"},
            {"register": 2410, "units": "oC", "description": "degree_celsius"},
        ],
    },
}

DO_SENSORS = {
    "do_0": {
        "model": "VisiFerm",
        "address": 0x09,
        "sample_interval": 1,
        "channels": [
            {
                "register": 2090,
                "units": "ppm",
                "description": "dissolved_oxygen",
            },
            {"register": 2410, "units": "oC", "description": "degree_celsius"},
        ],
    },
    "do_1": {
        "model": "VisiFerm",
        "address": 0x10,
        "sample_interval": 1,
        "channels": [
            {
                "register": 2090,
                "units": "ppm",
                "description": "dissolved_oxygen",
            },
            {"register": 2410, "units": "oC", "description": "degree_celsius"},
        ],
    },
    "do_2": {
        "model": "VisiFerm",
        "address": 0x11,
        "sample_interval": 1,
        "channels": [
            {
                "register": 2090,
                "units": "ppm",
                "description": "dissolved_oxygen",
            },
            {"register": 2410, "units": "oC", "description": "degree_celsius"},
        ],
    },
}


class Sensor(ABC):
    """Base sensor."""

    def __init__(self, identifier: str, config: dict) -> None:
        self.id = identifier
        self.address = config["address"]
        self.model = config["model"]
        self.channels = config["channels"]
        # We don't need to keep a reference of the timer instance inside the sensor?
        # Maybe create a list of [sensor, timer] pairs
        # Maybe group sensors by sampling interval and create a single timer for the group
        self.timer = Timer(config["sample_interval"])
        self.timer.add_suscriber(self)
        self._sampling_event = True

        # This variable holds the measurement from the sensor. It needs to be
        # updated every time we read the primary channel. This variable is used
        # by the method actuator.write_output()
        for ch in self.channels:
            ch["value"] = -0.111

    def __repr__(self) -> str:
        """Print sensor id."""
        return f"Sensor(id: {self.id})"

    def on_timer_callback(self) -> None:
        """Set sampling flag to True."""
        self._sampling_event = True
        _logger.debug(f"Timer callback on {self}")

    @abstractmethod
    def read(self) -> None:
        """Abstract class, all subclasses need to implent this method."""
        self.timer.is_elapsed()  # The timer should be called indepently of read operations?
        if self._sampling_event:
            for ch in self.channels:
                ch["value"] = random.gauss(35, 1)
            self._sampling_event = False


class RandomSensor(Sensor):
    """Class used for testing."""

    def read(self) -> None:
        """Print values with a gaussian distribution."""
        self.timer.is_elapsed()  # The timer should be called indepently of read operations?
        if self._sampling_event:
            self._sampling_event = False
            for ch in self.channels:
                ch["value"] = random.gauss(35, 1)


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

    OPERATOR_LEVELS: ClassVar = {
        "user": {"code": 0x03, "Password": 0},
        "administrator": {"code": 0x0C, "Password": 18111978},
        "specialist": {"code": 0x30, "Password": 16021966},
    }

    def __init__(
        self,
        identifier: str,
        config: dict,
        modbus_handler: ModbusHandler,
    ):
        super().__init__(identifier, config)
        self.modbus_handler = modbus_handler
        _logger.info(
            f"Initialized HamiltonSensor {identifier} at address {self.address}",
        )

    def __repr__(self) -> str:
        """Print sensor id."""
        return f"HamiltonSensor(id: {self.id}, model: {self.model}, addr: {self.address})"

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

        request = {
            "operation": "write",
            "address": self.address,
            "register": 4288,
            "values": [level],
        }
        try:
            # Write the operator level and password to the sensor

            self.modbus_handler.process_request(request)
            # Ensure the write operation was successful
            self.modbus_handler.get_result()
            _logger.debug(f"Operator level '{level_name}' set successfully.")
        except ModbusError as e:
            _logger.error(f"Failed to set operator level for {level_name}: {e}")
            raise

    def set_serial_interface(
        self,
        new_address: int,
        baudrate: int | None = None,
    ) -> None:
        """Set the serial interface configuration for the sensor."""
        _logger.info(
            f"Setting serial interface for sensor {self.id} at address {self.address}",
        )
        request = {
            "operation": "write",
            "address": self.address,
            "register": 4288,
        }
        try:
            # Set operator level
            self.set_operator_level("specialist")
            # Set the address
            request["register"] = 4288
            request["values"] = [new_address]
            self.modbus_handler.process_request(request)
            # Ensure the write operation was successful
            self.modbus_handler.get_result()
            self.address = new_address

            if baudrate is not None:
                # Set the new sensor baudrate
                # Careful! If you change the baud rate you
                # also have to update the Modbus Client
                request["register"] = 4102
                request["values"] = [baudrate]
                self.modbus_handler.process_request(request)
                # Ensure the write operation was successful
                self.modbus_handler.get_result()

            self.set_operator_level("user")
            _logger.info(f"Updated serial interface - address:{new_address}")
        except ModbusError as e:
            _logger.exception(f"Failed to set serial interface: {e}")
            raise

    def read(self) -> None:
        """Read all available channels in the sensor."""
        request = {
            "operation": "read",
            "address": self.address,
            "count": 10,
        }
        self.timer.is_elapsed()
        if self._sampling_event:
            self._sampling_event = False
            for chn in self.channels:
                try:
                    request["register"] = chn["register"]
                    self.modbus_handler.process_request(request)
                    result = self.modbus_handler.get_result()
                    chn["value"] = self.hex_to_float(result)

                except ModbusError as e:
                    _logger.exception(e)
                    chn["value"] = -0.111

    def set_measurement_configs(self, config_params: dict[int, int]) -> None:
        """Set measurement configurations for the sensor."""
        _logger.info(
            f"Setting measurement configs for sensor {self.id} at address {self.address}",
        )
        request = {
            "operation": "write",
            "address": self.address,
        }
        try:
            for param, value in config_params.items():
                request["register"] = param
                request["values"] = [value]
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

    def __init__(self, identifier: str, config: dict):
        """Analog input pins in the Raspberry PLC. Range(0, 4095) (0-10V)."""
        super().__init__(identifier, config)
        self.cal = None
        if IN_RASPBERRYPI:
            for chn in self.channels:
                rpiplc.pin_mode(chn["pin"], rpiplc.INPUT)
                chn["calibration"] = None

    def __repr__(self) -> str:
        """Print sensor id."""
        return f"AnalogSensor(id: {self.id}, channels: {self.channels})"

    def read(self) -> None:
        """Read analog values."""
        self.timer.is_elapsed()
        if IN_RASPBERRYPI and self._sampling_event:
            self._sampling_event = False
            for chn in self.channels:
                analog = rpiplc.analog_read(chn["pin"])
                value = (
                    self.get_value(analog, chn["cal"]) if chn["cal"] else analog
                )
                chn["value"] = value

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
            self.channels[i]["cal"] = Calibration(par[0], par[1])


@dataclass
class Calibration:
    """Class holding linear regression parameters y = a*x + b."""

    a: float
    b: float
