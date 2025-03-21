"""Sensors Definitions."""

from __future__ import annotations
import logging, struct, platform, random
from typing import TYPE_CHECKING
from reactors_czlab.core.utils import Timer
from modbus_handler import ModbusHandler, ModbusError

if TYPE_CHECKING:
    from typing import ClassVar,Dict,Optional,List

if platform.machine().startswith("arm"):
    from librpiplc import rpiplc as rp # type: ignore

# Configuring logging
logging.basicConfig(
    level=logging.DEBUG, 
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
_logger = logging.getLogger("server.sensors")

class ModbusError(Exception):
    """Custom exception for Modbus errors."""
    pass

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
            ch["value"] = -0.111

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

    def __init__(self, identifier: str, config: dict, modbus_handler: ModbusHandler):
        super().__init__(identifier, config)
        self.modbus_handler = modbus_handler
        _logger.info(f"Initialized HamiltonSensor {identifier} at address {self.address}")

        # Dispatcher for operations and register addresses
        self.dispatcher = {
            "read": {
                2090: self.read_pm1,
                2410: self.read_pm6,
            },
            "write": {
                4288: self.set_operator_level,
                4102: self.set_serial_interface,
            },
        }

    
    def set_operator_level(self, operation: str, register: int = 4288) -> None:
        """Set the operator level for the sensor based on the operation type."""
        OPERATION_LEVELS = {
            "read": "user",
            "write": "specialist",  # Set write operations to specialist level
            "calibration": "specialist",
            "PM1": "user",
            "PM6": "user",
        }
        level_name = OPERATION_LEVELS.get(operation, "user")  # Default operator level is set to 'user'
        level = self.OPERATOR_LEVELS[level_name]
        _logger.info(f"Setting operator level to {level_name} for {operation} operation")
        try:
            # Write the operator level and password to the sensor
            self.modbus_handler.process_request(
                address=self.address,
                register=register,
                operation="write",
                value=[level["code"], level["password"]]
            )
            self.modbus_handler.get_result()  # Ensure the write operation was successful
            _logger.info(f"Operator level '{level_name}' set successfully.")
        except ModbusError as e:
            _logger.error(f"Failed to set operator level for {operation}: {e}")
            raise

    
    def set_serial_interface(self, baudrate_code: int, parity: str = "N", address: Optional[int] = None, register: int = 4102) -> None:
        """Set the serial interface configuration for the sensor."""
        _logger.info(f"Setting serial interface for sensor {self.id} at address {self.address}")
        try:
            # Set the baudrate
            self.modbus_handler.process_request(
                address=self.address,
                register=register,
                operation="write",
                value=baudrate_code
            )
            self.modbus_handler.get_result()  # Ensure the write operation was successful
            _logger.info(f"Setting serial interface: Baudrate Code={baudrate_code}, Parity={parity}")

            if address is not None:
                # Set the new sensor address
                self.modbus_handler.process_request(
                    address=self.address,
                    register=4096,
                    operation="write",
                    value=address
                )
                self.modbus_handler.get_result()  # Ensure the write operation was successful
                self.address = address  # Update the object's address
                _logger.info(f"Sensor address updated to {address}")
        except ModbusError as e:
            _logger.error(f"Failed to set serial interface: {e}")
            raise


    def read_pm1(self, register: int) -> float:
        """Read PM1 value from the sensor."""
        try:
            self.modbus_handler.process_request(
                address=self.address,
                register=register,
                operation="read"
            )
            result = self.modbus_handler.get_result()
            return self.hex_to_float(result)
        except ModbusError as e:
            _logger.error(f"Failed to read PM1: {e}")
            return 9999

    def read_pm6(self, register: int) -> float:
        """Read PM6 value from the sensor."""
        try:
            self.modbus_handler.process_request(
                address=self.address,
                register=register,
                operation="read"
            )
            result = self.modbus_handler.get_result()
            return self.hex_to_float(result)
        except ModbusError as e:
            _logger.error(f"Failed to read PM6: {e}")
            return 9999
        

    def set_measurement_configs(self, config_params: Dict[int, int]) -> None:
        """Set measurement configurations for the sensor."""
        _logger.info(f"Setting measurement configs for sensor {self.id} at address {self.address}")
        try:
            for param, value in config_params.items():
                self.modbus_handler.process_request(
                    address=self.address,
                    register=param,
                    operation="write",
                    value=value
                )
                self.modbus_handler.get_result()  # Ensure the write operation was successful
            _logger.info("Measurement configs set successfully.")
        except ModbusError as e:
            _logger.error(f"Failed to set measurement configs: {e}")
            raise

    def hex_to_float(self, registers: List[int]) -> float:
        """Convert the raw registers to a float."""
        if not registers or len(registers) < 2:
            raise ModbusError("Invalid register data provided")
        raw = (registers[0] << 16) + registers[1]
        value = struct.unpack(">f", raw.to_bytes(4, byteorder="big"))[0]
        return value

    def handle_request(self, operation: str, register_address: int, **kwargs):
        """Automatically invoke the appropriate method based on the operation and register address."""
        try:
            # Set the operator level based on the operation type
            if operation == "read":
                self.set_operator_level("read")
            elif operation == "write":
                self.set_operator_level("write")

            # Look up the method in the dispatcher
            method = self.dispatcher.get(operation, {}).get(register_address)
            if not method:
                raise ModbusError(f"No handler found for operation '{operation}' and register address '{register_address}'")

            # Invoke the method with additional arguments
            result = method(register_address, **kwargs)

            # Reset the operator level to 'user' after a write operation
            if operation == "write":
                self.set_operator_level("read")  # Reset to user level
                _logger.info("Operator level reset to 'user' after write operation.")

            return result
        except ModbusError as e:
            _logger.error(f"Failed to handle request: {e}")
            raise


    def close(self) -> None:
        """Close the Modbus client connection."""
        self.modbus_handler.close()
        _logger.info(f"Closed HamiltonSensor {self.id} at address {self.address}")


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
            "identifier": "sensor1",
            "config": PH_SENSORS["ph_0"]
        },
        {
            "identifier": "sensor2",
            "config": DO_SENSORS["do_0"]
        },
    ]

 
    for sensor in sensors:
        reader = HamiltonSensor(
            identifier=sensor["identifier"],
            config=sensor["config"]
        )
        print(
            f"Reading from {sensor['config']['model']} at Address {sensor['config']['address']}",
        )
        print("PM1:", reader.read_pm1(register=sensor["config"]["channels"][0]["register"]))
        print("PM6:", reader.read_pm6(register=sensor["config"]["channels"][1]["register"]))
        reader.close()



    


    