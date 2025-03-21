"""Modbus Handler for managing Modbus communication."""

import logging
from typing import Optional, Dict, List, Union
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException

_logger = logging.getLogger("server.modbus_handler")


class ModbusError(Exception):
    """Custom exception for Modbus errors."""
    pass


class ModbusHandler:
    """Handles generic Modbus requests and processing."""
    
    ERROR_CODES: Dict[int, str] = {
        0x00: "Ok",
        0x01: "Illegal function",
        0x02: "Illegal data address",
        0x03: "Illegal data Value",
        0x04: "Slave device failure",
    }

    def __init__(self, port: str = "/dev/ttyUSB0", baudrate: int = 19200, timeout: float = 0.5):
        """Initialize the Modbus handler."""
        self.client = ModbusSerialClient(
            method="rtu", port=port, baudrate=baudrate, timeout=timeout, stopbits=1, bytesize=8, parity="N"
        )
        if not self.client.connect():
            raise ModbusError("Failed to connect to Modbus device")
        self._last_result = None  # Stores the result of the last processed request
        _logger.info("Initialized ModbusHandler")

    def process_request(self, address: int, register: int, operation: str, value: Optional[int] = None, count: int = 2) -> None:
        """
        Process a Modbus request (read/write) and store the result internally.
        Args:
            address (int): The Modbus address of the device.
            register (int): The register to read from or write to.
            operation (str): The operation to perform ("read" or "write").
            value (Optional[int]): The value to write (required for write operations).
            count (int): The number of registers to read (default is 2).
        Raises:
            ModbusError: If the operation fails.
        """
        _logger.debug(f"Processing {operation} operation on register {register} at address {address}")
        try:
            if operation == "read":
                result = self.client.read_holding_registers(register, count, slave=address)
                if result.isError():
                    error_code = result.exception_code
                    error_message = f"Error reading register {register} from unit {address}: {self.ERROR_CODES.get(error_code, 'Unknown error')}"
                    _logger.error(error_message)
                    raise ModbusError(error_message)
                self._last_result = result.registers
                _logger.info(f"Read success: {self._last_result}")
            elif operation == "write":
                if value is None:
                    raise ModbusError("Write operation requires a value")
                result = self.client.write_register(register, value, slave=address)
                if result.isError():
                    error_code = result.exception_code
                    error_message = f"Error writing to register {register} on unit {address}: {self.ERROR_CODES.get(error_code, 'Unknown error')}"
                    _logger.error(error_message)
                    raise ModbusError(error_message)
                self._last_result = True
                _logger.info(f"Write success to register {register} with value {value}")
            else:
                raise ModbusError("Invalid operation specified")
        except ModbusException as e:
            error_message = f"Modbus error during {operation} on {register}: {e}"
            _logger.error(error_message)
            raise ModbusError(error_message)

    def get_result(self) -> Union[List[int], bool]:
        """
        Return the result of the last processed request.
        Returns:
            Union[List[int], bool]: For read operations, returns a list of register values.
                                   For write operations, returns True if successful.
        Raises:
            ModbusError: If no result is available.
        """
        if self._last_result is None:
            raise ModbusError("No result available. Process a request first.")
        return self._last_result

    def close(self):
        """Close the Modbus client connection."""
        self.client.close()
        _logger.info("Closed ModbusHandler")