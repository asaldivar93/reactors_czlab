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


HAMILTON_SENSORS = {
    "R0": {
        "R0:ph": PhysicalInfo(
            model="ArcPh",
            address=0x01,
            type="digital",
            channels=[
                Channel("pH", "pH", register="pmc1"),
                Channel("oC", "degree_celsius", register="pmc6"),
            ],
        ),
        "R0:do": PhysicalInfo(
            model="VisiFerm",
            address=0x04,
            type="digital",
            channels=[
                Channel("ppm", "dissolved_oxygen", register="pmc1"),
                Channel("oC", "degree_celsius", register="pmc6"),
            ],
        ),
    },
    "R1": {
        "R1:ph": PhysicalInfo(
            model="ArcPh",
            address=0x02,
            type="digital",
            channels=[
                Channel("pH", "pH", register="pmc1"),
                Channel("oC", "degree_celsius", register="pmc6"),
            ],
        ),
        "R1:do": PhysicalInfo(
            model="VisiFerm",
            address=0x05,
            type="digital",
            channels=[
                Channel("ppm", "dissolved_oxygen", register="pmc1"),
                Channel("oC", "degree_celsius", register="pmc6"),
            ],
        ),
    },
    "R2": {
        "R2:ph": PhysicalInfo(
            model="ArcPh",
            address=0x03,
            type="digital",
            channels=[
                Channel("pH", "pH", register="pmc1"),
                Channel("oC", "degree_celsius", register="pmc6"),
            ],
        ),
        "R2:do": PhysicalInfo(
            model="VisiFerm",
            address=0x06,
            type="digital",
            channels=[
                Channel("ppm", "dissolved_oxygen", register="pmc1"),
                Channel("oC", "degree_celsius", register="pmc6"),
            ],
        ),
    },
}

BIOMASS_SENSORS = {
    "R0": {
        "R0:biomass": PhysicalInfo(
            model="biomass",
            address=0x32,
            type="digital",
            channels=[
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
            ],
        ),
    },
    "R1": {
        "R1:biomass": PhysicalInfo(
            model="biomass",
            address=0x32,
            type="digital",
            channels=[
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
            ],
        ),
    },
    "R2": {
        "R2:biomass": PhysicalInfo(
            model="biomass",
            address=0x32,
            type="digital",
            channels=[
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
            ],
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

I2C_PORTS = {
    "R0": 2,
    "R1": 3,
    "R2": 4,
    "R3": 5,
}

SERVER_VARS = {
    "R0": {
        "ns=2;i=9": copy_info(
            BIOMASS_SENSORS["R0"]["R0:biomass"],
            [Channel("415")],
        ),
        "ns=2;i=10": copy_info(
            BIOMASS_SENSORS["R0"]["R0:biomass"],
            [Channel("445")],
        ),
        "ns=2;i=11": copy_info(
            BIOMASS_SENSORS["R0"]["R0:biomass"],
            [Channel("480")],
        ),
        "ns=2;i=12": copy_info(
            BIOMASS_SENSORS["R0"]["R0:biomass"],
            [Channel("515")],
        ),
        "ns=2;i=13": copy_info(
            BIOMASS_SENSORS["R0"]["R0:biomass"],
            [Channel("555")],
        ),
        "ns=2;i=14": copy_info(
            BIOMASS_SENSORS["R0"]["R0:biomass"],
            [Channel("590")],
        ),
        "ns=2;i=15": copy_info(
            BIOMASS_SENSORS["R0"]["R0:biomass"],
            [Channel("630")],
        ),
        "ns=2;i=16": copy_info(
            BIOMASS_SENSORS["R0"]["R0:biomass"],
            [Channel("680")],
        ),
        "ns=2;i=17": copy_info(
            BIOMASS_SENSORS["R0"]["R0:biomass"],
            [Channel("clear")],
        ),
        "ns=2;i=18": copy_info(
            BIOMASS_SENSORS["R0"]["R0:biomass"],
            [Channel("nir")],
        ),
        "ns=2;i=6": copy_info(
            HAMILTON_SENSORS["R0"]["R0:do"],
            [Channel("ppm")],
        ),
        "ns=2;i=7": copy_info(HAMILTON_SENSORS["R0"]["R0:do"], [Channel("oC")]),
        "ns=2;i=66": copy_info(MFC_ACTUATORS["R0"]["R0:mfc"], [Channel("lpm")]),
        "ns=2;i=3": copy_info(HAMILTON_SENSORS["R0"]["R0:ph"], [Channel("pH")]),
        "ns=2;i=4": copy_info(HAMILTON_SENSORS["R0"]["R0:ph"], [Channel("oC")]),
        "ns=2;i=22": copy_info(
            ANALOG_ACTUATORS["R0"]["R0:pwm0"],
            [Channel("pwm0")],
        ),
        "ns=2;i=33": copy_info(
            ANALOG_ACTUATORS["R0"]["R0:pwm1"],
            [Channel("pwm1")],
        ),
        "ns=2;i=44": copy_info(
            ANALOG_ACTUATORS["R0"]["R0:pwm2"],
            [Channel("pwm2")],
        ),
        "ns=2;i=55": copy_info(
            ANALOG_ACTUATORS["R0"]["R0:pwm3"],
            [Channel("pwm3")],
        ),
    },
    "R1": {
        "ns=2;i=88": copy_info(
            BIOMASS_SENSORS["R1"]["R1:biomass"],
            [Channel("415")],
        ),
        "ns=2;i=89": copy_info(
            BIOMASS_SENSORS["R1"]["R1:biomass"],
            [Channel("445")],
        ),
        "ns=2;i=90": copy_info(
            BIOMASS_SENSORS["R1"]["R1:biomass"],
            [Channel("480")],
        ),
        "ns=2;i=91": copy_info(
            BIOMASS_SENSORS["R1"]["R1:biomass"],
            [Channel("515")],
        ),
        "ns=2;i=92": copy_info(
            BIOMASS_SENSORS["R1"]["R1:biomass"],
            [Channel("555")],
        ),
        "ns=2;i=93": copy_info(
            BIOMASS_SENSORS["R1"]["R1:biomass"],
            [Channel("590")],
        ),
        "ns=2;i=94": copy_info(
            BIOMASS_SENSORS["R1"]["R1:biomass"],
            [Channel("630")],
        ),
        "ns=2;i=95": copy_info(
            BIOMASS_SENSORS["R1"]["R1:biomass"],
            [Channel("680")],
        ),
        "ns=2;i=96": copy_info(
            BIOMASS_SENSORS["R1"]["R1:biomass"],
            [Channel("clear")],
        ),
        "ns=2;i=97": copy_info(
            BIOMASS_SENSORS["R1"]["R1:biomass"],
            [Channel("nir")],
        ),
        "ns=2;i=85": copy_info(
            HAMILTON_SENSORS["R1"]["R1:do"],
            [Channel("ppm")],
        ),
        "ns=2;i=86": copy_info(
            HAMILTON_SENSORS["R1"]["R1:do"],
            [Channel("oC")],
        ),
        "ns=2;i=145": copy_info(
            MFC_ACTUATORS["R1"]["R1:mfc"],
            [Channel("lpm")],
        ),
        "ns=2;i=82": copy_info(
            HAMILTON_SENSORS["R1"]["R1:ph"],
            [Channel("pH")],
        ),
        "ns=2;i=83": copy_info(
            HAMILTON_SENSORS["R1"]["R1:ph"],
            [Channel("oC")],
        ),
        "ns=2;i=101": copy_info(
            ANALOG_ACTUATORS["R1"]["R1:pwm0"],
            [Channel("pwm0")],
        ),
        "ns=2;i=112": copy_info(
            ANALOG_ACTUATORS["R1"]["R1:pwm1"],
            [Channel("pwm1")],
        ),
        "ns=2;i=123": copy_info(
            ANALOG_ACTUATORS["R1"]["R1:pwm2"],
            [Channel("pwm2")],
        ),
        "ns=2;i=134": copy_info(
            ANALOG_ACTUATORS["R1"]["R1:pwm3"],
            [Channel("pwm3")],
        ),
    },
    "R2": {
        "ns=2;i=167": copy_info(
            BIOMASS_SENSORS["R2"]["R2:biomass"],
            [Channel("415")],
        ),
        "ns=2;i=168": copy_info(
            BIOMASS_SENSORS["R2"]["R2:biomass"],
            [Channel("445")],
        ),
        "ns=2;i=169": copy_info(
            BIOMASS_SENSORS["R2"]["R2:biomass"],
            [Channel("480")],
        ),
        "ns=2;i=170": copy_info(
            BIOMASS_SENSORS["R2"]["R2:biomass"],
            [Channel("515")],
        ),
        "ns=2;i=171": copy_info(
            BIOMASS_SENSORS["R2"]["R2:biomass"],
            [Channel("555")],
        ),
        "ns=2;i=172": copy_info(
            BIOMASS_SENSORS["R2"]["R2:biomass"],
            [Channel("590")],
        ),
        "ns=2;i=173": copy_info(
            BIOMASS_SENSORS["R2"]["R2:biomass"],
            [Channel("630")],
        ),
        "ns=2;i=174": copy_info(
            BIOMASS_SENSORS["R2"]["R2:biomass"],
            [Channel("680")],
        ),
        "ns=2;i=175": copy_info(
            BIOMASS_SENSORS["R2"]["R2:biomass"],
            [Channel("clear")],
        ),
        "ns=2;i=176": copy_info(
            BIOMASS_SENSORS["R2"]["R2:biomass"],
            [Channel("nir")],
        ),
        "ns=2;i=164": copy_info(
            HAMILTON_SENSORS["R2"]["R2:do"],
            [Channel("ppm")],
        ),
        "ns=2;i=165": copy_info(
            HAMILTON_SENSORS["R2"]["R2:do"],
            [Channel("oC")],
        ),
        "ns=2;i=224": copy_info(
            MFC_ACTUATORS["R2"]["R2:mfc"],
            [Channel("lpm")],
        ),
        "ns=2;i=161": copy_info(
            HAMILTON_SENSORS["R2"]["R2:ph"],
            [Channel("pH")],
        ),
        "ns=2;i=162": copy_info(
            HAMILTON_SENSORS["R2"]["R2:ph"],
            [Channel("oC")],
        ),
        "ns=2;i=180": copy_info(
            ANALOG_ACTUATORS["R2"]["R2:pwm0"],
            [Channel("pwm0")],
        ),
        "ns=2;i=191": copy_info(
            ANALOG_ACTUATORS["R2"]["R2:pwm1"],
            [Channel("pwm1")],
        ),
        "ns=2;i=202": copy_info(
            ANALOG_ACTUATORS["R2"]["R2:pwm2"],
            [Channel("pwm2")],
        ),
        "ns=2;i=213": copy_info(
            ANALOG_ACTUATORS["R2"]["R2:pwm3"],
            [Channel("pwm3")],
        ),
    },
}
