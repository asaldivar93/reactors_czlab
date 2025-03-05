"""Test functionality of Reactor class."""

from reactors_czlab import Actuator, Reactor, Sensor
from reactors_czlab.core.sensor import PH_SENSORS

control_dict = {"method": "manual", "value": 150}

sen1 = Sensor("sen1", PH_SENSORS["ph_0"])
sen2 = Sensor("sen2", PH_SENSORS["ph_0"])
sen1.timer.interval = 0
sen2.timer.interval = 0

actuator_1 = Actuator("act1", "address")
actuator_2 = Actuator("act2", "address")

sensors = [sen1, sen2]
actuators = [actuator_1, actuator_2]
reactor = Reactor("R1", 0, sensors, actuators)


class TestReactor:
    def test1(self):
        assert isinstance(reactor, Reactor)

    def test2(self):
        assert isinstance(reactor.sensors, dict)

    def test3(self):
        assert isinstance(reactor.actuators, dict)

    def test4(self):
        reactor.update_sensors()
        sen1 = reactor.sensors.get("sen2")
        assert sen1.channels[0]["value"] > 0

    def test5(self):
        act = reactor.actuators.get("act1")
        assert act.controller.value == 0

    def test6(self):
        self.act2 = reactor.actuators.get("act2")
        new_controller = {"method": "on_boundaries", "value": 255,
                          "lower_bound": 40, "upper_bound": 50}
        sen1 = reactor.sensors.get("sen1")
        self.act2.set_control_config(new_controller)
        self.act2.set_reference_sensor(sen1)
        reactor.update_sensors()
        reactor.update_actuators()

        assert self.act2.controller.value == 255
