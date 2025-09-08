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
        List of channels to keep

    """
    new_info = deepcopy(info)
    for chn in new_info.channels:
        if chn not in channels:
            new_info.channels.remove(chn)
    return new_info


ph_channels = [
    Channel("pH", "pH", register="pmc1"),
    Channel("oC", "degree_celsius", register="pmc6"),
]
do_channels = [
    Channel("ppm", "dissolved_oxygen", register="pmc1"),
    Channel("oC", "degree_celsius", register="pmc6"),
]

HAMILTON_SENSORS = {
    "R0": {
        "R0:ph": PhysicalInfo(
            model="ArcPh",
            address=0x01,
            channels=ph_channels,
            type="digital",
        ),
        "R0:do": PhysicalInfo(
            model="VisiFerm",
            address=0x04,
            type="digital",
            channels=do_channels,
        ),
    },
    "R1": {
        "R1:ph": PhysicalInfo(
            model="ArcPh",
            address=0x02,
            type="digital",
            channels=ph_channels,
        ),
        "R1:do": PhysicalInfo(
            model="VisiFerm",
            address=0x05,
            type="digital",
            channels=do_channels,
        ),
    },
    "R2": {
        "R2:ph": PhysicalInfo(
            model="ArcPh",
            address=0x03,
            type="digital",
            channels=ph_channels,
        ),
        "R2:do": PhysicalInfo(
            model="VisiFerm",
            address=0x06,
            type="digital",
            channels=do_channels,
        ),
    },
}

as7341_channels = [
    Channel("415", "dimensionles", register="all"),
    Channel("445", "dimensionles", register="all"),
    Channel("480", "dimensionles", register="all"),
    Channel("515", "dimensionles", register="all"),
    Channel("555", "dimensionles", register="all"),
    Channel("590", "dimensionles", register="all"),
    Channel("630", "dimensionles", register="all"),
    Channel("680", "dimensionles", register="all"),
    Channel("clear", "dimensionles", register="all"),
    Channel("nir", "dimensionles", register="all"),
]
BIOMASS_SENSORS = {
    "R0": {
        "R0:biomass": PhysicalInfo(
            model="biomass",
            address=0x32,
            type="digital",
            channels=as7341_channels,
        ),
    },
    "R1": {
        "R1:biomass": PhysicalInfo(
            model="biomass",
            address=0x32,
            type="digital",
            channels=as7341_channels,
        ),
    },
    "R2": {
        "R2:biomass": PhysicalInfo(
            model="biomass",
            address=0x32,
            type="digital",
            channels=as7341_channels,
        ),
    },
}

ANALOG_ACTUATORS = {
    "R0": {
        "R0:pwm0": PhysicalInfo(
            model="pwm",
            address=0,
            type="pwm",
            channels=[
                Channel(
                    "pwm0",
                    "pwm",
                    pin="Q2.7",
                    calibration=Calibration("pump_0"),
                ),
            ],
        ),
        "R0:pwm1": PhysicalInfo(
            model="pwm",
            address=0,
            type="pwm",
            channels=[
                Channel(
                    "pwm1",
                    "pwm",
                    pin="Q1.5",
                    calibration=Calibration("pump_1"),
                ),
            ],
        ),
        "R0:pwm2": PhysicalInfo(
            model="pwm",
            address=0,
            type="pwm",
            channels=[
                Channel(
                    "pwm2",
                    "pwm",
                    pin="Q1.6",
                    calibration=Calibration("pump_1"),
                ),
            ],
        ),
        "R0:pwm3": PhysicalInfo(
            model="pwm",
            address=0,
            type="pwm",
            channels=[
                Channel(
                    "pwm3",
                    "pwm",
                    pin="Q2.6",
                    calibration=Calibration("pump_1"),
                ),
            ],
        ),
    },
    "R1": {
        "R1:pwm0": PhysicalInfo(
            model="pwm",
            address=0,
            type="pwm",
            channels=[
                Channel(
                    "pwm0",
                    "pwm",
                    pin="Q2.5",
                    calibration=Calibration("pump_0"),
                ),
            ],
        ),
        "R1:pwm1": PhysicalInfo(
            model="pwm",
            address=0,
            type="pwm",
            channels=[
                Channel(
                    "pwm1",
                    "pwm",
                    pin="Q2.4",
                    calibration=Calibration("pump_1"),
                ),
            ],
        ),
        "R1:pwm2": PhysicalInfo(
            model="pwm",
            address=0,
            type="pwm",
            channels=[
                Channel(
                    "pwm2",
                    "pwm",
                    pin="Q1.7",
                    calibration=Calibration("pump_1"),
                ),
            ],
        ),
        "R1:pwm3": PhysicalInfo(
            model="pwm",
            address=0,
            type="pwm",
            channels=[
                Channel(
                    "pwm3",
                    "pwm",
                    pin="Q0.5",
                    calibration=Calibration("pump_1"),
                ),
            ],
        ),
    },
    "R2": {
        "R2:pwm0": PhysicalInfo(
            model="pwm",
            address=0,
            type="pwm",
            channels=[
                Channel(
                    "pwm0",
                    "pwm",
                    pin="Q0.6",
                    calibration=Calibration("pump_0"),
                ),
            ],
        ),
        "R2:pwm1": PhysicalInfo(
            model="pwm",
            address=0,
            type="pwm",
            channels=[
                Channel(
                    "pwm1",
                    "pwm",
                    pin="Q0.7",
                    calibration=Calibration("pump_1"),
                ),
            ],
        ),
        "R2:pwm2": PhysicalInfo(
            model="pwm",
            address=0,
            type="pwm",
            channels=[
                Channel(
                    "pwm2",
                    "pwm",
                    pin="Q1.4",
                    calibration=Calibration("pump_1"),
                ),
            ],
        ),
        "R2:pwm3": PhysicalInfo(
            model="pwm",
            address=0,
            type="pwm",
            channels=[
                Channel(
                    "pwm3",
                    "pwm",
                    pin="Q0.4",
                    calibration=Calibration("pump_1"),
                ),
            ],
        ),
    },
}

MFC_ACTUATORS = {
    "R0": {
        "R0:mfc": PhysicalInfo(
            model="mfc",
            address=0,
            type="ANALOG",
            channels=[
                Channel("lpm", "liters_per_minute", register="pmc1"),
            ],
        ),
    },
    "R1": {
        "R1:mfc": PhysicalInfo(
            model="mfc",
            address=0,
            type="ANALOG",
            channels=[
                Channel("lpm", "liters_per_minute", register="pmc1"),
            ],
        ),
    },
    "R2": {
        "R2:mfc": PhysicalInfo(
            model="mfc",
            address=0,
            type="ANALOG",
            channels=[
                Channel("lpm", "liters_per_minute", register="pmc1"),
            ],
        ),
    },
}

i2c_ports = {
    "R0": 2,
    "R1": 3,
    "R2": 4,
    "R3": 5,
}
