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
            sample_interval=7,
            channels=ph_channels,
        ),
        "R0:do": PhysicalInfo(
            model="VisiFerm",
            address=0x04,
            sample_interval=7,
            channels=do_channels,
        ),
    },
    "R1": {
        "R1:ph": PhysicalInfo(
            model="ArcPh",
            address=0x02,
            sample_interval=7,
            channels=ph_channels,
        ),
        "R1:do": PhysicalInfo(
            model="VisiFerm",
            address=0x05,
            sample_interval=7,
            channels=do_channels,
        ),
    },
    "R2": {
        "R2:ph": PhysicalInfo(
            model="ArcPh",
            address=0x03,
            sample_interval=7,
            channels=ph_channels,
        ),
        "R2:do": PhysicalInfo(
            model="VisiFerm",
            address=0x06,
            sample_interval=7,
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
    Channel("415", "dimensionles", register="all"),
    Channel("415", "dimensionles", register="all"),
    Channel("clear", "dimensionles", register="all"),
    Channel("nir", "dimensionles", register="all"),
]
BIOMASS_SENSORS = {
    "R0": {
        "R0:biomass": PhysicalInfo(
            model="biomass",
            address=0x32,
            sample_interval=7,
            channels=as7341_channels,
        ),
    },
    "R1": {
        "R1:biomass": PhysicalInfo(
            model="biomass",
            address=0x32,
            sample_interval=7,
            channels=as7341_channels,
        ),
    },
    "R2": {
        "R2:biomass": PhysicalInfo(
            model="biomass",
            address=0x32,
            sample_interval=7,
            channels=as7341_channels,
        ),
    },
}

ANALOG_ACTUATORS = {
    "R0": {
        "R0:pump_0": PhysicalInfo(
            model="pump",
            address=0,
            sample_interval=0,
            channels=[
                Channel(
                    "pwm",
                    "pump_pwm",
                    pin="Q0.5",
                    calibration=Calibration("pump_0"),
                ),
            ],
        ),
        "R0:pump_1": PhysicalInfo(
            model="pump",
            address=0,
            sample_interval=0,
            channels=[
                Channel(
                    "pwm",
                    "pump_pwm",
                    pin="Q0.5",
                    calibration=Calibration("pump_1"),
                ),
            ],
        ),
        "R0:light": PhysicalInfo(
            model="light",
            address=0,
            sample_interval=0,
            channels=[
                Channel(
                    "pwm",
                    "led_pwm",
                    pin="Q0.5",
                    calibration=Calibration("pump_1"),
                ),
            ],
        ),
    },
    "R1": {
        "R1:pump_0": PhysicalInfo(
            model="pump",
            address=0,
            sample_interval=0,
            channels=[
                Channel(
                    "pwm",
                    "pump_pwm",
                    pin="Q0.5",
                    calibration=Calibration("pump_0"),
                ),
            ],
        ),
        "R1:pump_1": PhysicalInfo(
            model="pump",
            address=0,
            sample_interval=0,
            channels=[
                Channel(
                    "pwm",
                    "pump_pwm",
                    pin="Q0.5",
                    calibration=Calibration("pump_1"),
                ),
            ],
        ),
        "R1:light": PhysicalInfo(
            model="light",
            address=0,
            sample_interval=0,
            channels=[
                Channel(
                    "pwm",
                    "led_pwm",
                    pin="Q0.5",
                    calibration=Calibration("pump_1"),
                ),
            ],
        ),
    },
    "R2": {
        "R2:pump_0": PhysicalInfo(
            model="pump",
            address=0,
            sample_interval=0,
            channels=[
                Channel(
                    "pwm",
                    "pump_pwm",
                    pin="Q0.5",
                    calibration=Calibration("pump_0"),
                ),
            ],
        ),
        "R2:pump_1": PhysicalInfo(
            model="pump",
            address=0,
            sample_interval=0,
            channels=[
                Channel(
                    "pwm",
                    "pump_pwm",
                    pin="Q0.5",
                    calibration=Calibration("pump_1"),
                ),
            ],
        ),
        "R2:light": PhysicalInfo(
            model="light",
            address=0,
            sample_interval=0,
            channels=[
                Channel(
                    "pwm",
                    "led_pwm",
                    pin="Q0.5",
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
            sample_interval=0,
            channels=[
                Channel("lpm", "liters_per_minute", register="pmc1"),
            ],
        ),
    },
    "R1": {
        "R1:mfc": PhysicalInfo(
            model="mfc",
            address=0,
            sample_interval=0,
            channels=[
                Channel("lpm", "liters_per_minute", register="pmc1"),
            ],
        ),
    },
    "R2": {
        "R2:mfc": PhysicalInfo(
            model="mfc",
            address=0,
            sample_interval=0,
            channels=[
                Channel("lpm", "liters_per_minute", register="pmc1"),
            ],
        ),
    },
}


server_vars = {
    "R0": {
        "ns=2;i=10": copy_info(BIOMASS_SENSORS["R0"]["R0:biomass"], []),
        "ns=2;i=7": copy_info(HAMILTON_SENSORS["R0"]["R0:do"], [Channel("oC")]),
        "ns=2;i=8": copy_info(
            HAMILTON_SENSORS["R0"]["R0:do"],
            [Channel("ppm")],
        ),
        "ns=2;i=36": copy_info(ANALOG_ACTUATORS["R0"]["R0:light"], []),
        "ns=2;i=47": copy_info(MFC_ACTUATORS["R0"]["R0:mfc"], []),
        "ns=2;i=4": copy_info(HAMILTON_SENSORS["R0"]["R0:ph"], [Channel("oC")]),
        "ns=2;i=5": copy_info(HAMILTON_SENSORS["R0"]["R0:ph"], [Channel("pH")]),
        "ns=2;i=14": copy_info(ANALOG_ACTUATORS["R0"]["R0:pump_0"], []),
        "ns=2;i=25": copy_info(ANALOG_ACTUATORS["R0"]["R0:pump_0"], []),
    },
    "R1": {
        "ns=2;i=64": copy_info(BIOMASS_SENSORS["R1"]["R1:biomass"], []),
        "ns=2;i=61": copy_info(
            HAMILTON_SENSORS["R1"]["R1:do"],
            [Channel("oC")],
        ),
        "ns=2;i=62": copy_info(
            HAMILTON_SENSORS["R1"]["R1:do"],
            [Channel("ppm")],
        ),
        "ns=2;i=90": copy_info(ANALOG_ACTUATORS["R1"]["R1:light"], []),
        "ns=2;i=101": copy_info(MFC_ACTUATORS["R1"]["R1:mfc"], []),
        "ns=2;i=58": copy_info(
            HAMILTON_SENSORS["R1"]["R1:ph"],
            [Channel("oC")],
        ),
        "ns=2;i=59": copy_info(
            HAMILTON_SENSORS["R1"]["R1:ph"],
            [Channel("pH")],
        ),
        "ns=2;i=68": copy_info(ANALOG_ACTUATORS["R1"]["R1:pump_0"], []),
        "ns=2;i=79": copy_info(ANALOG_ACTUATORS["R1"]["R1:pump_0"], []),
    },
    "R2": {
        "ns=2;i=118": copy_info(BIOMASS_SENSORS["R2"]["R2:biomass"], []),
        "ns=2;i=115": copy_info(
            HAMILTON_SENSORS["R2"]["R2:do"],
            [Channel("oC")],
        ),
        "ns=2;i=116": copy_info(
            HAMILTON_SENSORS["R2"]["R2:do"],
            [Channel("ppm")],
        ),
        "ns=2;i=144": copy_info(ANALOG_ACTUATORS["R2"]["R2:light"], []),
        "ns=2;i=155": copy_info(MFC_ACTUATORS["R2"]["R2:mfc"], []),
        "ns=2;i=112": copy_info(
            HAMILTON_SENSORS["R2"]["R2:ph"],
            [Channel("oC")],
        ),
        "ns=2;i=113": copy_info(
            HAMILTON_SENSORS["R2"]["R2:ph"],
            [Channel("pH")],
        ),
        "ns=2;i=122": copy_info(ANALOG_ACTUATORS["R2"]["R2:pump_0"], []),
        "ns=2;i=133": copy_info(ANALOG_ACTUATORS["R2"]["R2:pump_0"], []),
    },
}

# server_test = {
#     "R0": {
#         "ns=2;i=7": copy_info(SENSORS["R0"]["R0:do"], [Channel("oC")]),
#         "ns=2;i=8": copy_info(SENSORS["R0"]["R0:do"], [Channel("ppm")]),
#         "ns=2;i=12": copy_info(ACTUATORS["R0"]["R0:pump_0"], []),
#     },
#     "R2": {
#         "ns=2;i=67": copy_info(SENSORS["R2"]["R2:do"], [Channel("oC")]),
#         "ns=2;i=68": copy_info(SENSORS["R2"]["R2:do"], [Channel("ppm")]),
#         "ns=2;i=72": copy_info(ACTUATORS["R2"]["R2:pump_0"], []),
#     },
# }
