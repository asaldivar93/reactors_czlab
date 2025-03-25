"""Test hamilton rs485 communications."""
import struct

from pymodbus import FramerType
from pymodbus.client import ModbusSerialClient

port="/dev/ttySC2"
#  Default Hamilton sensor slave address
slave = 0x01
# Channels PMC1 and PMC6 address
pmc1=2089
pmc6=2409
count=10

def u16_to_float(low, high):
	"""Converts little endian notation to float."""
	packed = struct.pack("<HH", low, high)
	return struct.unpack("<f", packed)[0]
	
# Create a Modbus RTU client
client = ModbusSerialClient(
	framer=FramerType.RTU,
	port=port,
    baudrate=19200,
    timeout=1,
    stopbits=1,
    bytesize=8,
    parity="N",
)

# Connect to the Modbus RTU slave
client.connect()

response = client.read_holding_registers(
	address=pmc1,
    count=10,
    slave=slave,
)
print(response)
low, high = response.registers[2], response.registers[3]
pmc1 = u16_to_float(low, high)
print(f"PMC1: {pmc1}")

response = client.read_holding_registers(
	address=pmc6,
    count=10,
    slave=slave,
)
print(response)
low, high = response.registers[2], response.registers[3]
pmc6 = u16_to_float(low, high)
print(f"PMC6: {pmc6}")
