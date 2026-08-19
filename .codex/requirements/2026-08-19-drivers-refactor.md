# Goal
the core/sensors.py and core/actuators files might benefit from extracting parts of them into their own subpackage to have a clean distinction between the generic reactor/control model and concrete hardware adapters. Please evaluate if this change is beneficial for codebase architecture, readability and maintenace. If so make a plan for it, If not let me know the reasons why. In this file you'll find a suggestion for the architecture

## Proposed architecture

reactors_czlab/
    core/
        sensor.py        # Sensor ABC, RandomSensor
        actuator.py      # Actuator ABC, RandomActuator
        ...

    drivers/
        modbus.py
        hamilton.py
        spectral.py
        plc.py
