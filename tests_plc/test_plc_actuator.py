import time

from reactors_czlab.core.actuator import PlcActuator
from reactors_czlab.core.data import ControlConfig, ControlMethod
from reactors_czlab.server_info import ANALOG_ACTUATORS

control_method = {
    0: ControlMethod.manual,
    1: ControlMethod.timer,
    2: ControlMethod.on_boundaries,
    3: ControlMethod.pid,
}

if __name__ == "__main__":
    actuator = PlcActuator("1", ANALOG_ACTUATORS["R0"]["R0:pwm0"])
    method = control_method[1]
    control = ControlConfig(method, value=4000)
    actuator.set_control_config(control)
    while True:
        actuator.write_output(0)
        time.sleep(2)
