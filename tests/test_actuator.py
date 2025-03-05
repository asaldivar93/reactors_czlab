
import time

import reactors_czlab.core.control as con
from reactors_czlab.core.actuator import Actuator
from reactors_czlab.core.sensor import PH_SENSORS, Sensor

control_dict = {"method": "manual", "value": 150}

con1 = con._ManualControl(50)
con2 = con._ManualControl(50)
con3 = con._ManualControl(10)
con5 = con._TimerControl(2.3, 5.1, 150)
con6 = con._TimerControl(2.3, 5.1, 150)
con7 = con._TimerControl(2.3, 2.3, 150)
con8 = con._OnBoundariesControl(1.1, 2.1, 150)
con9 = con._OnBoundariesControl(1.1, 2.1, 150)
con10 = con._OnBoundariesControl(1.1, 2.1, 150, backwards=True)
con11 = con._PidControl(35)
con12 = con._PidControl(35)
con13 = con._PidControl(40)

act2 = Actuator("a2", "address")
sen1 = Sensor("s1", PH_SENSORS["ph_0"])
sen1.timer.interval = 0

new_controller = {"method": "on_boundaries", "value": 255,
                  "lower_bound": 1.1, "upper_bound": 2.1}
act3 = Actuator("a2", "address")
act3.set_control_config(new_controller)
sen1.timer.add_suscriber(con1)
sen1.timer.add_suscriber(con2)
sen1.timer.add_suscriber(con3)
sen1.timer.add_suscriber(con5)
sen1.timer.add_suscriber(con6)
sen1.timer.add_suscriber(con7)
sen1.timer.add_suscriber(con8)
sen1.timer.add_suscriber(con9)
sen1.timer.add_suscriber(con10)
sen1.timer.add_suscriber(con11)
sen1.timer.add_suscriber(con12)
sen1.timer.add_suscriber(con13)

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
        sen1.read()
        sen1.channels[0]["value"] = 1.5
        value = con9.get_value(sen1)
        assert value == 0

    def test2(self):
        sen1.read()
        sen1.channels[0]["value"] = 0
        value = con9.get_value(sen1)
        assert value == 150

    def test3(self):
        sen1.read()
        sen1.channels[0]["value"] = 1.5
        value = con9.get_value(sen1)
        assert value == 150

    def test4(self):
        sen1.read()
        sen1.channels[0]["value"] = 3
        value = con9.get_value(sen1)
        assert value == 0

    def test5(self):
        sen1.read()
        sen1.channels[0]["value"] = 3
        value = con10.get_value(sen1)
        assert value == 150

    def test6(self):
        sen1.read()
        sen1.channels[0]["value"] = 0
        value = con10.get_value(sen1)
        assert value == 0

    def test7(self):
        sen1.read()
        sen1.channels[0]["value"] = 1.5
        value = con10.get_value(sen1)
        assert value == 0

    def test8(self):
        sen1.read()
        sen1.channels[0]["value"] = 3
        value = con10.get_value(sen1)
        assert value == 150

    def test9(self):
        sen1.read()
        sen1.channels[0]["value"] = 35
        value = con10.get_value(sen1)
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
        new_controller = {"method": "timer", "time_on": 3, "time_off": 5, "value": 135}
        act1.set_control_config(new_controller)
        act1.write_output()
        time.sleep(3)
        act1.write_output()
        assert act1.controller.value == 0

    def test4(self):
        act1 = Actuator("a1", "address")
        new_controller = {"method": "timer", "time_on": 5, "time_off": 1, "value": 135}
        act1.set_control_config(new_controller)
        sen1.read()
        act1.write_output()

        assert act1.controller.value == 135

    def test5(self):

        act3.set_reference_sensor(sen1)
        sen1.read()
        sen1.channels[0]["value"] = 0
        act3.write_output()

        assert act3.controller.value == 255

    def test6(self):
        sen1.read()
        sen1.channels[0]["value"] = 1.5
        act3.write_output()
        assert act3.controller.value == 255

    def test7(self):
        sen1.read()
        sen1.channels[0]["value"] = 2.2
        act3.write_output()

        assert act3.controller.value == 0

    def test8(self):
        sen1.read()
        sen1.channels[0]["value"] = 1.5
        act3.write_output()

        assert act3.controller.value == 0

    def test9(self):
        sen1.read()
        sen1.channels[0]["value"] = 1
        act3.write_output()

        assert act3.controller.value == 255

    def test10(self):
        sen1.read()
        sen1.channels[0]["value"] = 1.5
        act3.write_output()

        assert act3.controller.value == 255

    def test10(self):
        new_controller = {"method": "pid", "setpoint": 35}
        act2.set_control_config(new_controller)
        act2.set_reference_sensor(sen1)
        sen1.read()
        sen1.channels[0]["value"] = 35
        act2.write_output()

        assert act2.controller.value == 0

    def test11(self):
        sen1.read()
        sen1.channels[0]["value"] = 20
        act2.write_output()

        assert act2.controller.value >= 0
