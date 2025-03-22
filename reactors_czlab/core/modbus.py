"""Modbus Handler for managing Modbus communication."""

from __future__ import annotations

import logging
from typing import ClassVar

from pymodbus import FramerType
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException

_logger = logging.getLogger("server.modbus_handler")


class ModbusError(Exception):
    """Custom exception for Modbus errors."""


class ModbusHandler:
    """Handles generic Modbus requests and processing."""

    ERROR_CODES: ClassVar = {
        0x00: "Ok",
        0x01: "Illegal function",
        0x02: "Illegal data address",
        0x03: "Illegal data Value",
        0x04: "Slave device failure",
    }

    def __init__(
        self,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 19200,
        timeout: float = 0.5,
    ):
        """Initialize the Modbus handler."""
        self.client = ModbusSerialClient(
            framer=FramerType.RTU,
            port=port,
            baudrate=baudrate,
            timeout=timeout,
            stopbits=1,
            bytesize=8,
            parity="N",
        )
        if not self.client.connect():
            raise ModbusError("Failed to connect to Modbus device")
        self._last_result = (
            None  # Stores the result of the last processed request
        )
        _logger.info("Initialized ModbusHandler")

    def process_request(self, request: dict) -> None:
        """Process a Modbus request (read/write) and store the result internally.

        request:
            address: int
                The Modbus address of the device.
            register: int
                The register to read from or write to.
            operation: str
                The operation to perform ("read" or "write").
            value: list[int]
                A list of values to write (required for write operations).
            count: int
                The number of registers to read (default is 2).

        Raises:
            ModbusError: If the operation fails.

        """
        try:
            match request:
                case {
                    "operation": "read",
                    "address": slave,
                    "register": address,
                    "count": count,
                }:
                    result = self.client.read_holding_registers(
                        address=address,
                        count=count,
                        slave=slave,
                    )
                case {
                    "operation": "write",
                    "address": slave,
                    "register": address,
                    "values": values,
                }:
                    if values is None:
                        error_message = "Write operation requires ann value"
                        raise ModbusError(error_message)
                    result = self.client.write_registers(
                        address=address,
                        values=values,
                        slave=slave,
                    )
                case _:
                    error_message = "Invalid operation specified"
                    raise ModbusError(error_message)

            if result.isError():
                # The error code is stored in either status or function_code,
                # I'm not sure which one
                error_code = result.status
                error_message = f"Modbus error during {operation} on {register} on unit {address}: {self.ERROR_CODES.get(error_code, 'Unknown error')}"
                self._last_result = None
                raise ModbusError(error_message)

            _logger.debug(
                f"Operation success - slave: {address}, operation: {operation}, value: {value}, result: {result}",
            )
            self._last_result = result.registers

        except ModbusException as e:
            error_message = (
                f"Modbus error during {operation} on {register}: {e}"
            )
            raise ModbusError(error_message)

    def get_result(self) -> list[int]:
        """Return the result of the last processed request.

        Returns:
            _last_result: list[int] | bool
                For read operations, returns a list of register values.
                For write operations, returns True if successful.

        Raises:
            ModbusError: If no result is available.

        """
        if self._last_result is None:
            error_message = "Invalid result"
            raise ModbusError(error_message)
        return self._last_result

    def close(self) -> None:
        """Close the Modbus client connection."""
        self.client.close()
        _logger.info("Closed ModbusHandler")
