This project is about building a controller interface for bioreactors. It is designed to read sensors and control actuators from a raspberrypi and then transmit the data to a PC using opc-ua.

The project has four modules:
	-core: contains hardware-independent reactor, sensor, actuator, and control abstractions
		-hardware.py: Raspberry Pi PLC handles and init_hardware()
		-reactor.py: Class to represent a single reactor
		-sensor.py: Generic and simulated sensors
		-actuator.py: Generic and simulated actuators
		-control.py: Classes to implement the different types of control actions
		-data.py: Dataclasses shared by the server and the client
	-drivers: concrete Hamilton, spectral, PLC, and Modbus adapters
	-opcua: Contains the components for opc communication
	-sql: Contains operation to store data in a postgresql database
	-run_server.py: Script used in the raspberry pi to initialize the server
	-run_client.py: Script used in the PC to communicate with the server
	-run_plots.py: Live plots of the archived data
	-export_data.py: Dumps the archive to csv
