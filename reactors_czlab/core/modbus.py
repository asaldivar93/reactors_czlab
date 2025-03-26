"""Modbus Handler for managing Modbus communication."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import ClassVar

from pymodbus import FramerType
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException

_logger = logging.getLogger("server.modbus_handler")

valid_baudrates = {4800: 2, 9600: 3, 19200: 4, 38400: 5, 57600: 6, 115200: 7}


class ModbusError(Exception):
    """Custom exception for Modbus errors."""


@dataclass
class ModbusRequest:
    """Parameters for modbus communictaions.

    Parameters
    ----------
    operation: str
        The operation to perform ("read" or "write").
    address: int
        The Modbus address of the device.
    register: int
        The register to read from or write to.
    count: int
        The number of contiguous registers to read (default is 2).
    value: list[int]
        A list of values to write to contiguous registers.

    """

    operation: str
    address: int
    register: int
    count: int = 2
    values: list[int] | None = None


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
        """Initialize the Modbus handler.

        Parameters
        ----------
        port: str
            The serial port of the connection
        baudrate: int
            The speed of serial communication
        timeout: float
            Timeout

        """
        self.baudrate = baudrate
        self.client = ModbusSerialClient(
            framer=FramerType.RTU,
            port=port,
            baudrate=self.baudrate,
            timeout=timeout,
            stopbits=1,
            bytesize=8,
            parity="N",
        )
        if not self.client.connect():
            error_message = "Failed to connect to Modbus device"
            raise ModbusError(error_message)
        self._last_result = None
        _logger.info(f"Initialized ModbusHandler at port: {port}")

    @property
    def baudrate(self) -> int:
        """Serial speed."""
        return self._baudrate

    @baudrate.setter
    def baudrate(self, baudrate: int) -> None:
        if baudrate not in valid_baudrates:
            error_message = f"Baudrate should be one of {valid_baudrates}"
            raise ModbusError(error_message)
        self._baudrate = baudrate

    def process_request(self, request: ModbusRequest) -> None:
        """Process a Modbus request (read/write) and store the result internally.

        Parameters
        ----------
        request: ModbusRequest
            Dataclass with the parameters of the request.

        Raises
        ------
        ModbusError: If the operation fails.

        """
        try:
            match request:
                case ModbusRequest(
                    operation="read",
                    address=slave,
                    register=address,
                    count=count,
                ):
                    result = self.client.read_holding_registers(
                        address=address,
                        count=count,
                        slave=slave,
                    )

                case ModbusRequest(
                    operation="write",
                    address=slave,
                    register=address,
                    values=values,
                ):
                    if values is None:
                        error_message = f"Write operation requires a \
                                          list of values in: {request}"
                        raise ModbusError(error_message)
                    result = self.client.write_registers(
                        address=address,
                        values=values,
                        slave=slave,
                    )

                case _:
                    error_message = f"Invalid operation in: {request}"
                    raise ModbusError(error_message)

            if result.isError():
                # When a response is on of ERROR_CODES, pymodbus prints an
                # exception response, I'm not sure how to access that response,
                # I could not find the error code in result
                error_message = f"\
                Modbus error during {request.operation} \
                on {request.register} on unit {request.address}: {result}"
                self._last_result = None
                raise ModbusError(error_message)

            _logger.debug(
                f"Modbus success - slave: {request.address}, \
                operation: {request.operation}, value: {request.values}, \
                result: {result.registers}",
            )
            self._last_result = result.registers

        except ModbusException as e:
            error_message = f"Modbus error during {request.operation} \
                on {request.register}: {e}"
            self._last_result = None
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
