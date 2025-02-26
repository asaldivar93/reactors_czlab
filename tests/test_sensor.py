import unittest
from unittest.mock import MagicMock, patch
from pymodbus.client import ModbusSerialClient
from reactors_czlab import SensorReader

class TestSensorReader(unittest.TestCase):

    def setUp(self):
        self.mock_client = MagicMock(spec=ModbusSerialClient)
        self.mock_client.connect.return_value = True
        self.mock_client.close.return_value = None
        self.mock_client.read_holding_registers.return_value = MagicMock(isError=lambda: False)
        self.mock_client.write_registers.return_value = None
        self.mock_client.write_register.return_value = None

        self.patcher = patch('your_module.ModbusSerialClient', return_value=self.mock_client)
        self.patcher.start()
        
        self.sensor_reader = SensorReader(address=1, sensor_type='pH Arc', units='pH')
        
    def tearDown(self):
        self.patcher.stop()

    def test_initialization(self):
        self.assertEqual(self.sensor_reader.address, 1)
        self.assertEqual(self.sensor_reader.sensor_type, 'pH Arc')
        self.assertEqual(self.sensor_reader.units, 'pH')
        self.mock_client.connect.assert_called_once()

    def test_read(self):
        value = self.sensor_reader._read(register=2090)
        self.assertAlmostEqual(value, 12.5)
        self.mock_client.read_holding_registers.assert_called_once_with(2090, 2, slave=1)

    def test_read_error(self):
        self.mock_client.read_holding_registers.return_value.isError.return_value = True
        with self.assertLogs('reactors_czlab', level='ERROR') as log:
            value = self.sensor_reader._read(register=2090)
            self.assertIsNone(value)
            self.assertIn('Error reading register 2090', log.output[0])

    def test_set_operator_level(self):
        with patch('builtins.input', side_effect=['User', '1234']):
            self.sensor_reader.set_operator_level()
            self.mock_client.write_registers.assert_called_once_with(4288, [2, 1234], slave=1)

    def test_set_serial_interface(self):
        self.sensor_reader.set_serial_interface(baudrate_code=9600, parity='N', address=3)
        self.mock_client.write_register.assert_any_call(4102, 9600, slave=1)
        self.mock_client.write_register.assert_any_call(4096, 3, slave=1)
        
    def test_read_pm1(self):
        value = self.sensor_reader.read_pm1(register=2090)
        self.assertAlmostEqual(value, 12.5)
        self.mock_client.read_holding_registers.assert_called_once_with(2090, 2, slave=1)

    def test_read_pm6(self):
        value = self.sensor_reader.read_pm6(register=2410)
        self.assertAlmostEqual(value, 12.5)
        self.mock_client.read_holding_registers.assert_called_once_with(2410, 2, slave=1)

    def test_set_measurement_configs(self):
        config_params = {4097: 1, 4098: 2, 4100: 5}
        self.sensor_reader.set_measurement_configs(config_params)
        for register, value in config_params.items():
            self.mock_client.write_register.assert_any_call(register, value, slave=1)

    def test_close(self):
        self.sensor_reader.close()
        self.mock_client.close.assert_called_once()

if __name__ == "__main__":
    unittest.main()
