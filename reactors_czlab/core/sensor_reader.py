from pymodbus.client import ModbusSerialClient
import struct

class SensorReader:

    OPERATOR_LEVELS = {
        'Admin': 3,
        'User': 2,
        'Guest': 1
    }

    def __init__(self, address, sensor_type, units, port='/dev/ttyUSB0', baudrate=19200, timeout=1):
        
        self.address = address
        self.sensor_type = sensor_type
        self.units = units
        self.client = ModbusSerialClient(method='rtu', port=port, baudrate=baudrate, timeout=timeout, stopbits=1, bytesize=8, parity='N')
        self.client.connect()
    
    def _read(self, register, count=2, scale=1.0):
        
        result = self.client.read_holding_registers(register, count, slave=self.address)
        if result.isError():
            print(f"Error reading register {register} from unit {self.address}")
            return None
        raw = (result.registers[0] << 16) + result.registers[1]
        value = struct.unpack('>f', raw.to_bytes(4, byteorder='big'))[0]
        return value / scale
    
    def set_operator_level(self, register=4288):
        
        print("Select an operator level:")
        for level_name, level_value in self.OPERATOR_LEVELS.items():
            print(f"{level_name}: {level_value}")
        level_name = input("Enter the operator level: ")
        level = self.OPERATOR_LEVELS.get(level_name, 1)
        password = int(input("Enter password (default 0): ") or 0)
        print(f"Setting operator level to {level_name} ({level})")
        self.client.write_registers(register, [level, password], slave=self.address)

    def set_serial_interface(self, baudrate_code, parity='N', address=None, register=4102):
        
        print(f"Setting serial interface: Baudrate Code={baudrate_code}, Parity={parity}")
        self.client.write_register(register, baudrate_code, slave=self.address)
        
        if address is not None:
            print(f"Setting new sensor address to {address}")
            self.client.write_register(4096, address, slave=self.address)
            self.address = address  # Updating object's address to the new one
    
    def read_pm1(self, register):
        return self._read(register=register)
    
    def read_pm6(self, register):
        return self._read(register=register)
    
    def set_measurement_configs(self, config_params):
        
        print(f"Setting measurement configs: {config_params}")
        for param, value in config_params.items():
            self.client.write_register(param, value, slave=self.address)

    def close(self):
        self.client.close()

if __name__ == "__main__":
    sensors = [
        {'address': 1, 'sensor_type': 'pH Arc', 'units': 'pH', 'pm1_register': 2090, 'pm6_register': 2410},
        {'address': 2, 'sensor_type': 'VisiFerm DO', 'units': '%-vol', 'pm1_register': 2090, 'pm6_register': 2410}
    ]
    
    for sensor in sensors:
        reader = SensorReader(address=sensor['address'], sensor_type=sensor['sensor_type'], units=sensor['units'])
        print(f"Reading from {sensor['sensor_type']} at Address {sensor['address']}")
        print("PM1:", reader.read_pm1(register=sensor['pm1_register']))
        print("PM6:", reader.read_pm6(register=sensor['pm6_register']))
        reader.close()
