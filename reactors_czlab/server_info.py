"""Define the variables in the server."""

from copy import deepcopy

from reactors_czlab.core.utils import Calibration, Channel, PhysicalInfo

# Hamilton sensors can have addresses from 1 to 32.
# There are four types of hamilton sensors.
# We'll divide the address space this way: 1-8: ph_sensors,
# 9-16: oxygen_sensors, 17-24: incyte_sensors, 25-32: co2_sensors
PH_SENSORS = {
    "ph_0": PhysicalInfo(
        model="ArcPh",
        address=0x01,
        sample_interval=3,
        channels=[
            Channel("pH", "pH", register="pmc1"),
            Channel("oC", "degree_celsius", register="pmc6"),
        ],
    ),
    "ph_1": PhysicalInfo(
        model="ArcPh",
        address=0x02,
        sample_interval=3,
        channels=[
            Channel("pH", "pH", register="pmc1"),
            Channel("oC", "degree_celsius", register="pmc6"),
        ],
    ),
    "ph_2": PhysicalInfo(
        model="ArcPh",
        address=0x03,
        sample_interval=3,
        channels=[
            Channel("pH", "pH", register="pmc1"),
            Channel("oC", "degree_celsius", register="pmc6"),
        ],
    ),
}

DO_SENSORS = {
    "do_0": PhysicalInfo(
        model="VisiFerm",
        address=0x09,
        sample_interval=1,
        channels=[
            Channel("ppm", "dissolved_oxygen", register="pmc1"),
            Channel("oC", "degree_celsius", register="pmc6"),
        ],
    ),
    "do_1": PhysicalInfo(
        model="VisiFerm",
        address=0x10,
        sample_interval=1,
        channels=[
            Channel("ppm", "dissolved_oxygen", register="pmc1"),
            Channel("oC", "degree_celsius", register="pmc6"),
        ],
    ),
    "do_2": PhysicalInfo(
        model="VisiFerm",
        address=0x11,
        sample_interval=1,
        channels=[
            Channel("ppm", "dissolved_oxygen", register="pmc1"),
            Channel("oC", "degree_celsius", register="pmc6"),
        ],
    ),
}

ANALOG_SENSORS = {
    "ph_3": PhysicalInfo(
        model="analog",
        address=0,
        sample_interval=5,
        channels=[
            Channel(
                "ph",
                "ph",
                calibration=Calibration("ph_250328.csv", 34, 5),
            ),
        ],
    ),
    "do_3": PhysicalInfo(
        model="analog",
        address=0,
        sample_interval=5,
        channels=[
            Channel(
                "%",
                "dissolved_oxygen",
                calibration=Calibration("do_250328.csv", 34, 5),
            ),
        ],
    ),
}

PUMPS = {
    "pump_0": PhysicalInfo(
        model="actuator",
        address=0,
        sample_interval=0,
        channels=[Channel("analog", "pump", pin="Q0.5")],
    ),
    "pump_1": PhysicalInfo(
        model="actuator",
        address=0,
        sample_interval=0,
        channels=[Channel("analog", "pump", pin="Q0.6")],
    ),
    "pump_2": PhysicalInfo(
        model="actuator",
        address=0,
        sample_interval=0,
        channels=[Channel("analog", "pump", pin="Q0.7")],
    ),
}

server_vars = {
    "R_0": {
        "ns=2;i=7": deepcopy(DO_SENSORS["do_0"]).channels.pop(1),
        "ns=2;i=8": deepcopy(DO_SENSORS["do_0"]).channels.pop(0),
    },
}
