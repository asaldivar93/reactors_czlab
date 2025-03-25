"""Test RS485 in PLC."""

from pymodbus import FramerType
from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server import StartSerialServer
serial_port="/dev/ttySC2"
# Define a simple data block with 10 registers
store = ModbusSlaveContext(
    di=ModbusSequentialDataBlock(0, [0x0] * 10000),
    co=ModbusSequentialDataBlock(0, [0x0] * 10000),
    hr=ModbusSequentialDataBlock(0, [0x0] * 10000),
    ir=ModbusSequentialDataBlock(0, [0x0] * 10000),
)
context = ModbusServerContext(slaves=store, single=True)

# Start the Modbus RTU server on a serial port
# RPI PLC v6 uses serial ports ttySC2 and ttysc3
print(f"Server running on port {serial_port}")
StartSerialServer(
    context=context, port=serial_port, framer=FramerType.RTU, baudrate=9600
)
