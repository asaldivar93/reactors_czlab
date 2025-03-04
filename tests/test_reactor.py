"""Test functionality of Reactor class."""

from reactors_czlab import Reactor, Sensor, Actuator, DictList
from reactors_czlab.core.sensor import PH_SENSORS

control_dict = {"method": "manual", "value": 150}

sen1 = Sensor("s1", PH_SENSORS["ph_0"])
sen2 = Sensor("s2", PH_SENSORS["ph_0"])

actuator_1 = Actuator("act1", "address")
actuator_2 = Actuator("act2", "address")

sensors = [sen1, sen2]
actuators = [actuator_1, actuator_2]
reactor = Reactor("R1", 0, sensors, actuators)

class TestReactor:
    def test1(self):
        assert isinstance(reactor, Reactor)

    def test2(self):
        assert isinstance(reactor.sensors, DictList)

    def test3(self):
        assert isinstance(reactor.actuators, DictList)

    def test4(self):
        reactor.update_sensors()
        sen1 = reactor.sensors.get_by_id("sen2")
        assert sen1.channels[0]["value"] > 0

    def test5(self):
        act = reactor.actuators.get_by_id("act1")
        assert act.controller.value == 0

    def test6(self):
        self.act2 = reactor.actuators.get_by_id("act2")
        new_controller = {"method": "on_boundaries", "value": 255,
                          "lower_bound": 40, "upper_bound": 50}
        sen1 = reactor.sensors.get_by_id("sen1")
        self.act2.set_reference_sensor(sen1)
        self.act2.set_control_config(new_controller)
        reactor.update_actuators()

        assert self.act2.controller.value == 255
