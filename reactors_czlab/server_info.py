"""Define the variables in the server."""

from copy import deepcopy

from reactors_czlab.core.data import Calibration, Channel, PhysicalInfo

VERBOSE = True


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

ACTUATORS = {
    "R0": {
        "R0:pump_0": PhysicalInfo(
            model="actuator",
            address=0,
            sample_interval=0,
            channels=[
                Channel(
                    "pwm",
                    "pump",
                    pin="Q0.5",
                    calibration=Calibration("R0:pump_0"),
                ),
            ],
        ),
        "R0:pump_1": PhysicalInfo(
            model="actuator",
            address=0,
            sample_interval=0,
            channels=[
                Channel(
                    "pwm",
                    "pump",
                    pin="Q0.5",
                    calibration=Calibration("R0:pump_1"),
                ),
            ],
        ),
    },
    "R1": {
        "R1:pump_0": PhysicalInfo(
            model="actuator",
            address=0,
            sample_interval=0,
            channels=[
                Channel(
                    "pwm",
                    "pump",
                    pin="Q0.5",
                    calibration=Calibration("R1:pump_0"),
                ),
            ],
        ),
        "R1:pump_1": PhysicalInfo(
            model="actuator",
            address=0,
            sample_interval=0,
            channels=[
                Channel(
                    "pwm",
                    "pump",
                    pin="Q0.5",
                    calibration=Calibration("R1:pump_1"),
                ),
            ],
        ),
    },
    "R2": {
        "R2:pump_0": PhysicalInfo(
            model="actuator",
            address=0,
            sample_interval=0,
            channels=[
                Channel(
                    "pwm",
                    "pump",
                    pin="Q0.5",
                    calibration=Calibration("R2:pump_0"),
                ),
            ],
        ),
        "R2:pump_1": PhysicalInfo(
            model="actuator",
            address=0,
            sample_interval=0,
            channels=[
                Channel(
                    "pwm",
                    "pump",
                    pin="Q0.5",
                    calibration=Calibration("R2:pump_1"),
                ),
            ],
        ),
    },
    "R3": {},
}


server_vars = {
    "R0": {
        "ns=2;i=7": copy_info(SENSORS["R0"]["R0:do"], [Channel("oC")]),
        "ns=2;i=8": copy_info(SENSORS["R0"]["R0:do"], [Channel("ppm")]),
        "ns=2;i=12": copy_info(ACTUATORS["R0"]["R0:pump_0"], []),
    },
}

server_test = {
    "R0": {
        "ns=2;i=7": copy_info(SENSORS["R0"]["R0:do"], [Channel("oC")]),
        "ns=2;i=8": copy_info(SENSORS["R0"]["R0:do"], [Channel("ppm")]),
        "ns=2;i=12": copy_info(ACTUATORS["R0"]["R0:pump_0"], []),
    },
    "R2": {
        "ns=2;i=67": copy_info(SENSORS["R2"]["R2:do"], [Channel("oC")]),
        "ns=2;i=68": copy_info(SENSORS["R2"]["R2:do"], [Channel("ppm")]),
        "ns=2;i=72": copy_info(ACTUATORS["R2"]["R2:pump_0"], []),
    },
}
