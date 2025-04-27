from pymodbus import FramerType
from pymodbus.client import ModbusSerialClient
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadBuilder, BinaryPayloadDecoder

REGISTERS = {
    "operator": 4288 - 1,
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

Builder = BinaryPayloadBuilder
Decoder = BinaryPayloadDecoder


def update_operator_level(operator: str) -> None:
    """Change operator level."""
    builder = Builder(byteorder=Endian.LITTLE, wordorder=Endian.LITTLE)
    level = OPERATOR_LEVELS[operator]
    for val in level.values():
        builder.add_32bit_uint(val)
    payload = builder.build()

    response = client.write_registers(
        address=REGISTERS["operator"],
        values=payload,
        slave=slave,
        skip_encode=True,
    )
    print("\nOperator Update:")
    print(response)


if __name__ == "__main__":
    # Connect to the Modbus RTU slave
    client.connect()

    # Change operator level
    update_operator_level("specialist")

    # Read current calibration status
    response = client.read_input_registers(
        address=REGISTERS["cp1_status"],
        count=6,
        slave=slave,
    )
    print("\nCP status:")
    print(response)

    # Calibration
    builder = Builder(byteorder=Endian.LITTLE)
    builder.add_32bit_float(10.01)
    payload = builder.build()
    response = client.write_registers(
        address=REGISTERS["cp1"],
        values=payload,
        slave=slave,
        skip_encode=True,
    )

    # Read current calibration status
    response = client.read_input_registers(
        address=REGISTERS["cp1_status"],
        count=6,
        slave=slave,
    )
    print("\nCP status:")
    print(response)

    # Read current quality indicator
    response = client.read_input_registers(
        address=REGISTERS["cp1_status"],
        count=6,
        slave=slave,
    )
    print("\nProbe quality:")
    print(response)

    # Read current ph value
    response = client.read_holding_registers(
        address=REGISTERS["pmc1"],
        count=10,
        slave=slave,
    )
    print("\npH value:")
    print(response)
    low, high = response.registers[2], response.registers[3]
    decoder = Decoder(
        payload=[low, high],
        byteorder=Endian.LITTLE,
        wordorder=Endian.LITTLE,
    )
    pmc1 = decoder.decode_32bit_float()
    print(f"pH: {pmc1}")

    update_operator_level("user")
