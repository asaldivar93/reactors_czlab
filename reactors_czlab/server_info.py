"""Define the variables in the server."""

from copy import deepcopy

from reactors_czlab.core.data import Calibration, Channel, PhysicalInfo

# Hamilton sensors can have addresses from 1 to 32.
# There are four types of hamilton sensors.
# We'll divide the address space this way: 1-8: ph_sensors,
# 9-16: oxygen_sensors, 17-24: incyte_sensors, 25-32: co2_sensors
VERBOSE = False


def copy_info(info: PhysicalInfo, channels: list[Channel]) -> PhysicalInfo:
    """Copy sensor info and remove the channles in list.

    Parameters
    ----------
    info:
        Sensor info to copy
    channels:
        List of channels to remove

    """
    new_info = deepcopy(info)
    for chn in channels:
        new_info.channels.remove(chn)
    return new_info


PH_SENSORS = {
    "R0:ph": PhysicalInfo(
        model="ArcPh",
        address=0x01,
        sample_interval=10,
        channels=[
            Channel("pH", "pH", register="pmc1"),
            Channel("oC", "degree_celsius", register="pmc6"),
        ],
    ),
    "R1:ph": PhysicalInfo(
        model="ArcPh",
        address=0x02,
        sample_interval=5,
        channels=[
            Channel("pH", "pH", register="pmc1"),
            Channel("oC", "degree_celsius", register="pmc6"),
        ],
    ),
    "R2:ph": PhysicalInfo(
        model="ArcPh",
        address=0x03,
        sample_interval=5,
        channels=[
            Channel("pH", "pH", register="pmc1"),
            Channel("oC", "degree_celsius", register="pmc6"),
        ],
    ),
}

DO_SENSORS = {
    "R0:do": PhysicalInfo(
        model="VisiFerm",
        address=0x09,
        sample_interval=5,
        channels=[
            Channel("ppm", "dissolved_oxygen", register="pmc1"),
            Channel("oC", "degree_celsius", register="pmc6"),
        ],
    ),
    "R1:do": PhysicalInfo(
        model="VisiFerm",
        address=0x10,
        sample_interval=5,
        channels=[
            Channel("ppm", "dissolved_oxygen", register="pmc1"),
            Channel("oC", "degree_celsius", register="pmc6"),
        ],
    ),
    "R2:ph": PhysicalInfo(
        model="VisiFerm",
        address=0x11,
        sample_interval=5,
        channels=[
            Channel("ppm", "dissolved_oxygen", register="pmc1"),
            Channel("oC", "degree_celsius", register="pmc6"),
        ],
    ),
}

ANALOG_SENSORS = {
    "R3:ph": PhysicalInfo(
        model="analog",
        address=0,
        sample_interval=5,
        channels=[
            Channel("mV", "pH"),
        ],
    ),
    "R3:do": PhysicalInfo(
        model="analog",
        address=0,
        sample_interval=5,
        channels=[Channel("mV", "dissolved_oxygen")],
    ),
}

PUMPS = {
    "R0:pump_0": PhysicalInfo(
        model="actuator",
        address=0,
        sample_interval=0,
        channels=[
            Channel(
                "pwm",
                "pump",
                pin="Q0.5",
                calibration=Calibration("pump0"),
            ),
        ],
    ),
    "R0:pump_1": PhysicalInfo(
        model="actuator",
        address=0,
        sample_interval=0,
        channels=[Channel("pwm", "pump", pin="Q0.6")],
    ),
    "R1:pump_0": PhysicalInfo(
        model="actuator",
        address=0,
        sample_interval=0,
        channels=[Channel("pwm", "pump", pin="Q0.7")],
    ),
    "R1:pump_1": PhysicalInfo(
        model="actuator",
        address=0,
        sample_interval=0,
        channels=[Channel("pwm", "pump", pin="Q0.7")],
    ),
    "R2:pump_0": PhysicalInfo(
        model="actuator",
        address=0,
        sample_interval=0,
        channels=[Channel("pwm", "pump", pin="Q0.7")],
    ),
    "R2:pump_1": PhysicalInfo(
        model="actuator",
        address=0,
        sample_interval=0,
        channels=[Channel("pwm", "pump", pin="Q0.7")],
    ),
}

server_vars = {
    "R0": {
        "ns=2;i=7": copy_info(DO_SENSORS["R0:do"], [Channel("oC")]),
        "ns=2;i=8": copy_info(DO_SENSORS["R0:do"], [Channel("ppm")]),
        "ns=2;i=12": copy_info(PUMPS["R0:pump_0"], []),
    },
}
