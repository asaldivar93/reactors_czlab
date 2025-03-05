"""Sensors Definitions."""

from __future__ import annotations

import logging
import platform
import random
import struct
from typing import TYPE_CHECKING

from reactors_czlab.core.utils import Timer

if TYPE_CHECKING:
    from typing import ClassVar

if platform.machine().startswith("arm"):
    from librpiplc import rpiplc as rp
    from pymodbus.client import ModbusSerialClient

_logger = logging.getLogger("server.sensors")

IN_RASPBERRYPI = platform.machine().startswith("arm")
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


class Sensor:
    """Base sensor."""

    def __init__(self, identifier: str, config: dict) -> None:
        self.id = identifier
        self.address = config["address"]
        self.model = config["model"]
        self.channels = config["channels"]
        self.timer = Timer(config["sample_interval"])
        self.timer.add_suscriber(self)
        self._sampling_event = True

        # This variable holds the measurement from the sensor. It needs to be
        # updated every time we read the primary channel. This variable is used
        # by the method actuator.write_output()
        for ch in self.channels:
            ch["value"] = 9999

    def __repr__(self) -> str:
        return f"Sensor(id: {self.id})"

    def on_timer_callback(self) -> None:
        self._sampling_event = True
        _logger.debug(f"Timer callback on {self}")

    def read(self) -> None:
        self.timer.is_elapsed()
        if self._sampling_event:
            for ch in self.channels:
                ch["value"] = random.gauss(35, 1)
            self._sampling_event = False


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

    # Updated with information from Hamilton Documentation
    # You were right, administrator and specialist do need a password
    # we can change the password but we won't
    OPERATOR_LEVELS: ClassVar = {
        "user": {"code": 0x03, "Password": 0},
        "administrator": {"code": 0x0C, "Password": 18111978},
        "specialist": {"code": 0x30, "Password": 16021966},
    }

    ERROR_CODES: ClassVar = {
        0x00: "Ok",
        0x01: "Illegal function",
        0x02: "Illegal data address",
        0x03: "Illegal data Value",
        0x04: "Slave device failure",
    }

    def __init__(
        self,
        identifier,
        config,
        port="/dev/ttyUSB0",
        baudrate=19200,
        timeout=1,
    ):
        super().__init__(identifier, config)
        self.client = ModbusSerialClient(
            method="rtu",
            port=port,
            baudrate=baudrate,
            timeout=timeout,
            stopbits=1,
            bytesize=8,
            parity="N",
        )
        self.client.connect()

    def _read(self, register, count=2, scale=1.0):
        # Do you know what happens in case of a time out? Does it raise an error?
        # Say for example, we disconnect the sensor during operation. In that
        # situation this function needs to raise an error

        # The timeout should be less than a second to avoid blocking
        # the main thread that is going to be runnig the server.
        # Or consider using the AsyncModbusSerialClient but keeping
        # the FIFO queue we discuss to avoid collading the serial channel
        result = self.client.read_holding_registers(
            register,
            count,
            slave=self.address,
        )

        # Instead of return None, we need to raise an error as well
        # and we need to print the ERROR_CODE
        # Maybe create our own exception (ModbusError?) to handle modbus errors
        # or check if the pymodbus library has exceptions we can use (ModbusException?)
        if result.isError():
            # and replace error print statements with _logger.error("")
            print(f"Error reading register {register} from unit {self.address}")
            return None

        # I think its better if this function returns the result object itself
        # and to make another function to convert the registers to float (hex_to_float?)
        raw = (result.registers[0] << 16) + result.registers[1]
        value = struct.unpack(">f", raw.to_bytes(4, byteorder="big"))[0]
        return value / scale

    def set_operator_level(self, register=4288):
        print("Select an operator level:")
        for level_name, level_value in self.OPERATOR_LEVELS.items():
            print(f"{level_name}: {level_value}")
        level_name = input("Enter the operator level: ")
        level = self.OPERATOR_LEVELS.get(level_name, 1)
        password = int(input("Enter password (default 0): ") or 0)
        print(f"Setting operator level to {level_name} ({level})")
        self.client.write_registers(
            register,
            [level, password],
            slave=self.address,
        )

    def set_serial_interface(
        self,
        baudrate_code,
        parity="N",
        address=None,
        register=4102,
    ):
        print(
            f"Setting serial interface: Baudrate Code={baudrate_code}, Parity={parity}",
        )
        self.client.write_register(register, baudrate_code, slave=self.address)

        if address is not None:
            print(f"Setting new sensor address to {address}")
            self.client.write_register(4096, address, slave=self.address)
            self.address = address  # Updating object"s address to the new one

    def read_pm1(self, register):
        # If the read returns an error we need to catch the error and return 9999
        # If there is no error then we would call (hex_to_float?)
        return self._read(register=register)

    def read_pm6(self, register):
        return self._read(register=register)

    def set_measurement_configs(self, config_params):
        print(f"Setting measurement configs: {config_params}")
        for param, value in config_params.items():
            self.client.write_register(param, value, slave=self.address)

    def close(self):
        self.client.close()


class AnalogSensor(Sensor):
    """Class for reading analog channels from the Raspberry."""

    def __init__(self, identifier: str, config: str):
        super().__init__(identifier, config)
        self.cal = None
        if IN_RASPBERRYPI:
            rp.pin_mode(self.channel, rp.INPUT)

    def read(self) -> None:
        if IN_RASPBERRYPI:
            analog = rp.analog_read(self.channel)
            if self.cal:
                self.value = self.get_value(analog)
            else:
                self.value = analog

    def get_value(self, value: float) -> float:
        return self.cal[0] * value + self.cal[1]

    def set_calibration(self, cal: list[float, float]) -> None:
        self.cal = cal


if __name__ == "__main__":
    sensors = [
        {
            "address": 1,
            "sensor_type": "pH Arc",
            "units": "pH",
            "pm1_register": 2090,
            "pm6_register": 2410,
        },
        {
            "address": 2,
            "sensor_type": "VisiFerm DO",
            "units": "%-vol",
            "pm1_register": 2090,
            "pm6_register": 2410,
        },
    ]

    for sensor in sensors:
        reader = SensorReader(
            address=sensor["address"],
            sensor_type=sensor["sensor_type"],
            units=sensor["units"],
        )
        print(
            f"Reading from {sensor['sensor_type']} at Address {sensor['address']}",
        )
        print("PM1:", reader.read_pm1(register=sensor["pm1_register"]))
        print("PM6:", reader.read_pm6(register=sensor["pm6_register"]))
        reader.close()
