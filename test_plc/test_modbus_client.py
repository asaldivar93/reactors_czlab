"""Test RS485 in PLC."""

from pymodbus import FramerType
from pymodbus.client import ModbusSerialClient

# Create a Modbus RTU client
client = ModbusSerialClient(
    framer=FramerType.RTU, port="/dev/ttySC3", baudrate=9600
)

# Connect to the Modbus RTU slave
client.connect()

# Define the slave address
slave_address = 0x00

# Write to holding registers
register_address = 0
num_registers = 5
data_to_write = [10, 20, 30, 40, 50]
response = client.write_registers(
    register_address, data_to_write, slave=slave_address
)

if not response.isError():
    print("Write successful")
else:
    print("Error writing registers:", response)

# Read 5 holding registers starting from address 0
response = client.read_holding_registers(
    address=register_address, count=num_registers, slave=slave_address
)

if not response.isError():
    print("Read successful:", response.registers)
else:
    print("Error reading registers:", response)

# Close the connection
client.close()
