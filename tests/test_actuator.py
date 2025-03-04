
import time

import reactors_czlab.core.actuator as act
from reactors_czlab.core.actuator import BaseActuator as Actuator
from reactors_czlab.core.sensor import PH_SENSORS
from reactors_czlab import Sensor

control_dict = {"method": "manual", "value": 150}

con1 = act._ManualControl(50)
con2 = act._ManualControl(50)
con3 = act._ManualControl(10)
con5 = act._TimerControl(2.3, 5.1, 150)
con6 = act._TimerControl(2.3, 5.1, 150)
con7 = act._TimerControl(2.3, 2.3, 150)
con8 = act._OnBoundariesControl(1.1, 2.1, 150)
con9 = act._OnBoundariesControl(1.1, 2.1, 150)
con10 = act._OnBoundariesControl(1.1, 2.1, 150, backwards=True)
con11 = act._PidControl(35)
con12 = act._PidControl(35)
con13 = act._PidControl(40)

act2 = Actuator("a2", "address")
sen1 = Sensor("s1", PH_SENSORS["ph_0"])
sen2 = Sensor("s2", PH_SENSORS["ph_0"])

class TestEq:
    def test1(self):
        test1 = con1 != con2
        assert test1 == False

    def test2(self):
        test2 = con1 != con3
        assert test2 == True

    def test3(self):
        test3 = con1 != con5
        assert test3 == True

    def test4(self):
        test4 = con1 != con8
        assert test4 == True

    def test5(self):
        test5 = con1 != con11
        assert test5 == True

    def test6(self):
        test6 = con5 != con6
        assert test6 == False

    def test7(self):
        test7 = con5 != con7
        assert test7 == True

    def test8(self):
        test8 = con5 != con8
        assert test8 == True

    def test9(self):
        test9 = con5 != con11
        assert test9 == True

    def test10(self):
        test10 = con8 != con9
        assert test10 == False

    def test11(self):
        test11 = con8 != con10
        assert test11 == True

    def test12(self):
        test12 = con8 != con11
        assert test12 == True

    def test13(self):
        test13 = con11 != con12
        assert test13 == False

    def test14(self):
        test14 = con11 != con13
        assert test14 == True

class TestGetValue:
    def test1(self):
        value = con9.get_value(1.5)
        assert value == 0

    def test2(self):
        value = con9.get_value(0)
        assert value == 150

    def test3(self):
        value = con9.get_value(1.5)
        assert value == 150

    def test4(self):
        value = con9.get_value(3)
        assert value == 0

    def test5(self):
        value = con10.get_value(1.5)
        assert value == 150

    def test6(self):
        value = con10.get_value(0)
        assert value == 0

    def test7(self):
        value = con10.get_value(1.5)
        assert value == 0

    def test8(self):
        value = con10.get_value(3)
        assert value == 150

    def test9(self):
        value = con11.get_value(35)
        assert isinstance(value, int)

class TestActuator:
    def test1(self):
        act1 = Actuator("a1", "address")
        assert isinstance(act1, Actuator)

    def test2(self):
        act1 = Actuator("a1", "address")
        assert act1.controller.value == 0

    def test3(self):
        act1 = Actuator("a1", "address")
        new_controller = {"method": "timer", "time_on": 1, "time_off": 5, "value": 135}
        act1.set_control_config(new_controller)
        act1.write_output()
        time.sleep(3)
        act1.write_output()

        assert act1.controller.value == 0

    def test4(self):
        act1 = Actuator("a1", "address")
        new_controller = {"method": "timer", "time_on": 5, "time_off": 1, "value": 135}
        act1.set_control_config(new_controller)
        act1.write_output()

        assert act1.controller.value == 135

    def test5(self):
        new_controller = {"method": "on_boundaries", "value": 255,
                          "lower_bound": 1.1, "upper_bound": 2.1}
        act2.set_reference_sensor(sen1)
        act2.set_control_config(new_controller)
        sen1.channels[0]["value"] = 1.5
        act2.write_output()

        assert act2.controller.value == 0

    def test6(self):
        sen1.channels[0]["value"] = 1.0
        act2.write_output()

        assert act2.controller.value == 255

    def test7(self):
        sen1.channels[0]["value"] = 1.5
        act2.write_output()

        assert act2.controller.value == 255

    def test8(self):
        sen1.channels[0]["value"] = 2.2
        act2.write_output()

        assert act2.controller.value == 0

    def test9(self):
        sen1.channels[0]["value"] = 1.5
        act2.write_output()

        assert act2.controller.value == 0

    def test10(self):
        new_controller = {"method": "pid", "setpoint": 35}
        act2.set_reference_sensor(sen2)
        act2.set_control_config(new_controller)
        sen1.channels[0]["value"] = 35
        act2.write_output()

        assert act2.controller.value == 0

    def test11(self):
        sen1.channels[0]["value"] = 20
        act2.write_output()

        assert act2.controller.value >= 0
