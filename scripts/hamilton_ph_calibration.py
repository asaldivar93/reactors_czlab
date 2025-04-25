import struct

from pymodbus import FramerType
from pymodbus.client import ModbusSerialClient

REGISTERS = {
    "cp1_info": 5152 - 1,
    "cp2_info": 5184 - 1,
    "cp6_info": 5312 - 1,
    "cp1_status": 5158 - 1,
    "cp2_status": 5190 - 1,
    "cp6_status": 5318 - 1,
    "cp1": 5162 - 1,
    "cp2": 5194 - 1,
    "quality": 4872 - 1,
    "pmc1": 2090 - 1,
}

OPERATOR_LEVELS = {
    "user": {"code": 0x03, "Password": 0},
    "administrator": {"code": 0x0C, "Password": 18111978},
    "specialist": {"code": 0x30, "Password": 16021966},
}

port = "/dev/ttySC2"
#  Default Hamilton sensor slave address
slave = 0x01
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


def u16_to_float(low, high):
    """Convert little endian notation to float."""
    packed = struct.pack("<HH", low, high)
    return struct.unpack("<f", packed)[0]


if __name__ == "__main__":
    # Connect to the Modbus RTU slave
    client.connect()

    # Change operator level
    level = OPERATOR_LEVELS["specialist"]
    response = client.write_registers(
        address=REGISTERS["cp1_status"],
        values=list(level.values()),
        slave=slave,
    )

    # Read current calibration status
    response = client.read_input_registers(
        address=REGISTERS["cp1_status"],
        count=6,
        slave=slave,
    )
    print(f"CP status: {response}")

    # Calibration
    response = client.write_registers(
        address=REGISTERS["cp1"],
        values=[0],
        slave=slave,
    )

    # Read current calibration status
    response = client.read_input_registers(
        address=REGISTERS["cp1_status"],
        count=6,
        slave=slave,
    )
    print(f"CP status: {response}")
    # Read current quality indicator
    response = client.read_input_registers(
        address=REGISTERS["cp1_status"],
        count=6,
        slave=slave,
    )
    print(f"CP quality: {response}")
    # Read current ph value
    response = client.read_holding_registers(
        address=REGISTERS["pmc1"],
        count=10,
        slave=slave,
    )
    print(response)
    low, high = response.registers[2], response.registers[3]
    pmc1 = u16_to_float(low, high)
    print(f"pH: {pmc1}")
