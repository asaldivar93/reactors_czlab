"""Sensors Definitions."""

from __future__ import annotations
from pymodbus.exceptions import ModbusException
import logging, struct, queue, threading, platform, random
from typing import TYPE_CHECKING
from reactors_czlab.core.utils import Timer

if TYPE_CHECKING:
    from typing import ClassVar

if platform.machine().startswith("arm"):
    from librpiplc import rpiplc as rp # type: ignore
    from pymodbus.client import ModbusSerialClient

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

    def __init__(self, identifier: str, config: dict, port="/dev/ttyUSB0", baudrate=19200, timeout=0.5):
        super().__init__(identifier, config)
        self.address = config["address"]
        self.client = ModbusSerialClient(method="rtu", port=port, baudrate=baudrate, timeout=timeout, stopbits=1, bytesize=8, parity="N")
        self.client.connect()
        self.request_queue = queue.Queue()  # FIFO queue for incoming requests
        self.lock = threading.Lock()  # Lock for thread-safe access to the Modbus client
        self._stop_event = threading.Event()  # Event to stop the request processing thread
        self._processing_thread = threading.Thread(target=self._process_requests, daemon=True)
        self._processing_thread.start()
        _logger.info(f"Initialized HamiltonSensor {identifier} at address {self.address}")
        
    # create a different file and class to handle the modbus client and queue. 
    def _process_requests(self):
        """Process requests from the FIFO queue sequentially."""
        while not self._stop_event.is_set():
            try:
                request = self.request_queue.get(timeout=1)
                if request is None:
                    break
                method, args, kwargs, result_queue = request
                with self.lock:
                    try:
                        result = method(*args, **kwargs)
                        result_queue.put((result, None))
                    except Exception as e:
                        _logger.error(f"Error processing request {method.__name__}: {e}")
                        result_queue.put((None, e))
                self.request_queue.task_done()
            except queue.Empty:
                continue

    def _enqueue_request(self, method, *args, **kwargs):
        """Enqueue a request and wait for the result."""
        result_queue = queue.Queue()
        _logger.debug(f"Queueing request: {method.__name__} with args={args}, kwargs={kwargs}")
        self.request_queue.put((method, args, kwargs, result_queue))
        result, error = result_queue.get()
        if error:
            _logger.error(f"Error executing {method.__name__}: {error}")
            raise error
        _logger.info(f"Successfully executed {method.__name__}")
        return result

    def _read(self, register, count=2, scale=1.0):
        """Read holding registers with FIFO queue support."""
        _logger.debug(f"Reading {count} registers from {register} on address {self.address}")
        try:
            # Enqueue the read request
            result = self._enqueue_request(self.client.read_holding_registers, register, count, slave=self.address)
            # Check if the result is an error
            if result.isError():
                error_code = result.exception_code
                error_message = f"Error reading register {register} from unit {self.address}: {self.ERROR_CODES.get(error_code, 'Unknown error')}"
                _logger.error(error_message)
                raise ModbusError(error_message)
            _logger.info(f"Read success: {result.registers}")
            # Return the result object without transforming
            return result
        except ModbusException as e:
            # Handle Modbus exceptions (e.g., disconnection, timeout)
            error_message = f"Error while reading {register}: {e}"
            _logger.error(error_message)
            raise ModbusError(error_message)
        except Exception as e:
            # Handle any other exceptions
            error_message = f"Error while reading {register}: {e}"
            _logger.error(error_message)
            raise ModbusError(error_message)
            
            
    def hex_to_float(self, result, scale=1.0) -> float:
        """Convert the raw registers from the result object to a float."""
        if not result or result.isError():
            raise ModbusError("Invalid result object provided")
        raw = (result.registers[0] << 16) + result.registers[1]
        value = struct.unpack(">f", raw.to_bytes(4, byteorder="big"))[0]
        return value / scale

    # Update it to hardcode the operator level based on the requested operation.
    def set_operator_level(self, operation: str, register=4288) -> None:
        """Set the operator level for the sensor based on the operation type."""
        OPERATION_LEVELS = {
            "read": "user",
            "write": "administrator",
            "calibration": "specialist",
            "PM1": "user",
            "PM6": "user",
        }
        level_name = OPERATION_LEVELS.get(operation, "user") # Default operator level is set to 'user'
        level = self.OPERATOR_LEVELS[level_name]
        _logger.info(f"Setting operator level to {level_name} for {operation} operation")
        try:
            self._enqueue_request(self.client.write_registers, register, [level["code"], level["password"]], slave=self.address)
            _logger.info(f"Operator level '{level_name}' set successfully.")
        except ModbusException as e:
            _logger.error(f"Failed to set operator level for {operation}: {e}")
            raise


    def set_serial_interface(self, baudrate_code, parity="N", address=None, register=4102) -> None:
        """Set the serial interface configuration for the sensor."""

        _logger.info("Setting serial interface for sensor %s at address %d", self.id, self.address)
        try:
            # Enqueue the request to set the baudrate
            self._enqueue_request(self.client.write_register, register, baudrate_code, slave=self.address)
            _logger.info(f"Setting serial interface: Baudrate Code={baudrate_code}, Parity={parity}")
            
            if address is not None:
                # Enqueue the request to set the new sensor address
                self._enqueue_request(self.client.write_register, 4096, address, slave=self.address)
                self.address = address  # Updating the object's address
                _logger.info(f"Sensor address updated to {address}")
        except ModbusError as e:
            _logger.error(f"Failed to set serial interface: {e}")
            raise


    def read_pm1(self, register: int) -> float:
        try:
            result = self._read(register=register)
            return self.hex_to_float(result)
        except ModbusError as e:
            _logger.error(f"Failed to read PM1: {e}")
            return 9999


    def read_pm6(self, register: int) -> float:
        try:
            result = self._read(register=register)
            return self.hex_to_float(result)
        except ModbusError as e:
            _logger.error(f"Failed to read PM6: {e}")
            return 9999
        

    def set_measurement_configs(self, config_params) -> None:
        """Set measurement configurations for the sensor."""
        _logger.info("Setting measurement configs for sensor %s at address %d", self.id, self.address)
        try:
            for param, value in config_params.items():
                self._enqueue_request(self.client.write_register, param, value, slave=self.address)
            _logger.info("Measurement configs set successfully.")
        except ModbusError as e:
            _logger.error(f"Failed to set measurement configs: {e}")
            raise


    def close(self) -> None:
        """Close the Modbus client connection and stop the processing thread."""
        self._stop_event.set()  # Signal the thread to stop
        self.request_queue.put(None)  # Sentinel value to stop the thread
        self._processing_thread.join()  # Wait for the thread to finish
        self.client.close()
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

    # sensor instances are passed each time measurement is 
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
