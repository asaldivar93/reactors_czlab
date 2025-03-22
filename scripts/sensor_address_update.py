from sensor import HamiltonSensor  # Import the HamiltonSensor class
import logging

# Configuring logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
_logger = logging.getLogger("sensor_port_update")

# Default addresses of the sensors
DEFAULT_ADDRESSES = [1, 2]  # Example default addresses
NEW_ADDRESSES = [10, 11]   # New addresses to assign

# Serial port address (Need to update this based on our system)
PORT_ADDRESS = "/dev/ttyUSB0"  

def update_sensor_port_addresses():
    """Connect to sensors using their default addresses and update their port addresses."""
    for default_addr, new_addr in zip(DEFAULT_ADDRESSES, NEW_ADDRESSES):
        try:
            # Initialize the sensor with the default address
            sensor = HamiltonSensor(
                identifier=f"Sensor_{default_addr}",
                config={"address": default_addr, "model": "ArcPh", "channels": []},
                port=PORT_ADDRESS,  # Use the specified port address
                baudrate=19200,
                timeout=0.5
            )
            _logger.info(f"Connected to sensor at default address {default_addr}")

            # Update the sensor's port address
            sensor.set_serial_interface(baudrate_code=19200, address=new_addr)
            _logger.info(f"Updated sensor address from {default_addr} to {new_addr}")

            # Close the connection
            sensor.close()
            _logger.info(f"Closed connection to sensor at address {new_addr}")

        except Exception as e:
            _logger.error(f"Failed to update address for sensor at {default_addr}: {e}")

if __name__ == "__main__":
    update_sensor_port_addresses()