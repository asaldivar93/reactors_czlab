"""Modbus Handler for managing Modbus communication."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import ClassVar

from pymodbus import FramerType
from pymodbus.client import ModbusSerialClient
from pymodbus.constants import Endian
from pymodbus.exceptions import ModbusException
from pymodbus.payload import BinaryPayloadBuilder, BinaryPayloadDecoder

_logger = logging.getLogger("server.modbus_handler")

valid_baudrates = {4800: 2, 9600: 3, 19200: 4, 38400: 5, 57600: 6, 115200: 7}

# Word/byte order used by the Hamilton Arc sensors. Encoding and decoding
# MUST use the same pair, so they live here and nowhere else: if a reading
# comes back byte swapped, flip these two constants and nothing else.
BYTE_ORDER = Endian.BIG
WORD_ORDER = Endian.LITTLE

#: Hamilton stores every measurement as a 32 bit value across two registers.
REGISTERS_PER_VALUE = 2


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
    values: list[int | float] | None = None


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
        timeout: float = 0.1,
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
            retries=0,
            stopbits=1,
            bytesize=8,
            parity="N",
        )
        # Serialize all Modbus calls so only 1 sensor uses the
        # modbus line at a time
        self.lock = asyncio.Lock()
        # Run modbus calls in a non-blocking thread
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="modbus",
        )
        if not self.client.connect():
            error_message = f"Failed to connect to Modbus device at port {port}"
            raise ModbusError(error_message)
        _logger.info("Initialized ModbusHandler at port: %s", port)

    @property
    def baudrate(self) -> int:
        """Serial speed."""
        return self._baudrate

    @baudrate.setter
    def baudrate(self, baudrate: int) -> None:
        if baudrate not in valid_baudrates:
            error_message = (
                f"Baudrate should be one of {sorted(valid_baudrates)}"
            )
            raise ModbusError(error_message)
        self._baudrate = baudrate

    async def process_request(self, request: ModbusRequest) -> list[int]:
        """Process a Modbus request.

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
                    operation="read_holding",
                    address=slave,
                    register=address,
                    count=count,
                ):
                    async with self.lock:
                        loop = asyncio.get_running_loop()
                        result = await loop.run_in_executor(
                            self._executor,
                            lambda: self.client.read_holding_registers(
                                address=address,
                                count=count,
                                slave=slave,
                            ),
                        )

                case ModbusRequest(
                    operation="read_input",
                    address=slave,
                    register=address,
                    count=count,
                ):
                    async with self.lock:
                        loop = asyncio.get_running_loop()
                        result = await loop.run_in_executor(
                            self._executor,
                            lambda: self.client.read_input_registers(
                                address=address,
                                count=count,
                                slave=slave,
                            ),
                        )

                case ModbusRequest(
                    operation="write",
                    address=slave,
                    register=address,
                    values=values,
                ):
                    if values is None:
                        error_message = (
                            "Write operation requires a list of values "
                            f"in: {request}"
                        )
                        raise ModbusError(error_message)
                    payload = self._build_payload(values)
                    async with self.lock:
                        loop = asyncio.get_running_loop()
                        result = await loop.run_in_executor(
                            self._executor,
                            lambda: self.client.write_registers(
                                address=address,
                                values=payload,
                                slave=slave,
                            ),
                        )

                case _:
                    error_message = f"Invalid operation in: {request}"
                    raise ModbusError(error_message)

            if result.isError():
                error_message = (
                    f"Error during {request.operation} on register "
                    f"{request.register} of unit {request.address} "
                    f"with code {result.exception_code}"
                )
                raise ModbusError(error_message)

            _logger.debug(
                "Modbus success - unit: %s, operation: %s, value: %s,"
                " result: %s",
                request.address,
                request.operation,
                request.values,
                result.registers,
            )
        except ModbusException as err:
            error_message = (
                f"Modbus error during {request.operation} "
                f"on unit {request.address}: {err}"
            )
            raise ModbusError(error_message) from err
        else:
            return result.registers

    def _build_payload(self, values: list) -> list:
        """Encode values into 16 bit registers."""
        builder = BinaryPayloadBuilder(
            byteorder=BYTE_ORDER,
            wordorder=WORD_ORDER,
        )
        for val in values:
            match val:
                case int():
                    if val < 0:
                        builder.add_32bit_int(val)
                    else:
                        builder.add_32bit_uint(val)
                case float():
                    builder.add_32bit_float(val)
                case _:
                    error_message = "Only float and ints are implemented"
                    raise ModbusError(error_message)

        return builder.to_registers()

    def decode(
        self,
        registers: Sequence[int],
        cast_type: str,
    ) -> float | int:
        """Translate register values to variables.

        Parameters
        ----------
        registers:
            Two 16 bit register values holding one 32 bit quantity
        cast_type:
            The type of the conversion. One of: int, float.

        Raises
        ------
        ModbusError
            If the register count is wrong or cast_type is unsupported.

        """
        registers = list(registers)
        if len(registers) != REGISTERS_PER_VALUE:
            error_message = (
                f"decode() needs exactly {REGISTERS_PER_VALUE} registers, "
                f"got {len(registers)}: {registers}"
            )
            raise ModbusError(error_message)

        # fromRegisters() packs the register list into bytes for us. The
        # previous code passed the raw ints as `payload`, which the decoder
        # treats as a byte buffer.
        decoder = BinaryPayloadDecoder.fromRegisters(
            registers,
            byteorder=BYTE_ORDER,
            wordorder=WORD_ORDER,
        )
        match cast_type:
            case "float":
                return decoder.decode_32bit_float()
            case "int":
                return decoder.decode_32bit_uint()
            case _:
                error_message = (
                    f"Unsupported cast_type {cast_type!r} in "
                    "ModbusHandler.decode(): use 'float' or 'int'"
                )
                raise ModbusError(error_message)

    def close(self) -> None:
        """Close the Modbus client connection."""
        self.client.close()
        _logger.info("Closed ModbusHandler")
