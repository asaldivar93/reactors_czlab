"""Test RS485 in PLC."""

from pymodbus import FramerType
from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server import StartSerialServer

# Define a simple data block with 10 registers
store = ModbusSlaveContext(
    di=ModbusSequentialDataBlock(0, [0x0] * 10000),
    co=ModbusSequentialDataBlock(0, [0x0] * 10000),
    hr=ModbusSequentialDataBlock(0, [0x0] * 10000),
    ir=ModbusSequentialDataBlock(0, [0x0] * 10000),
)
context = ModbusServerContext(slaves=store, single=True)

# Start the Modbus RTU server on a serial port
# In this case using '/dev/ttySC0' port from Raspberry PLC 21
StartSerialServer(
    context=context, port="/dev/ttySC0", framer=FramerType.RTU, baudrate=9600
)
