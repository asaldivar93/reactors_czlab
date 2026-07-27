"""Define the variables in the server."""

from reactors_czlab.core.data import (
    Calibration,
    Channel,
    PhysicalInfo,
    PlcOutput,
)

VERBOSE = True


HAMILTON_SENSORS = {
    "R0": {
        "R0:ph": PhysicalInfo(
            model="ArcPh",
            address=0x01,
            type=PlcOutput.digital,
            channels=[
                Channel("pH", "pH", register="pmc1"),
                Channel("oC", "degree_celsius", register="pmc6"),
            ],
        ),
        "R0:do": PhysicalInfo(
            model="VisiFerm",
            address=0x04,
            type=PlcOutput.digital,
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
            type=PlcOutput.digital,
            channels=[
                Channel("pH", "pH", register="pmc1"),
                Channel("oC", "degree_celsius", register="pmc6"),
            ],
        ),
        "R1:do": PhysicalInfo(
            model="VisiFerm",
            address=0x05,
            type=PlcOutput.digital,
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
            type=PlcOutput.digital,
            channels=[
                Channel("pH", "pH", register="pmc1"),
                Channel("oC", "degree_celsius", register="pmc6"),
            ],
        ),
        "R2:do": PhysicalInfo(
            model="VisiFerm",
            address=0x06,
            type=PlcOutput.digital,
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
            type=PlcOutput.digital,
            channels=[
                Channel("415", "dimensionless", register="all"),
                Channel("445", "dimensionless", register="all"),
                Channel("480", "dimensionless", register="all"),
                Channel("515", "dimensionless", register="all"),
                Channel("555", "dimensionless", register="all"),
                Channel("590", "dimensionless", register="all"),
                Channel("630", "dimensionless", register="all"),
                Channel("680", "dimensionless", register="all"),
                Channel("clear", "dimensionless", register="all"),
                Channel("nir", "dimensionless", register="all"),
            ],
        ),
    },
    "R1": {
        "R1:biomass": PhysicalInfo(
            model="biomass",
            address=0x32,
            type=PlcOutput.digital,
            channels=[
                Channel("415", "dimensionless", register="all"),
                Channel("445", "dimensionless", register="all"),
                Channel("480", "dimensionless", register="all"),
                Channel("515", "dimensionless", register="all"),
                Channel("555", "dimensionless", register="all"),
                Channel("590", "dimensionless", register="all"),
                Channel("630", "dimensionless", register="all"),
                Channel("680", "dimensionless", register="all"),
                Channel("clear", "dimensionless", register="all"),
                Channel("nir", "dimensionless", register="all"),
            ],
        ),
    },
    "R2": {
        "R2:biomass": PhysicalInfo(
            model="biomass",
            address=0x32,
            type=PlcOutput.digital,
            channels=[
                Channel("415", "dimensionless", register="all"),
                Channel("445", "dimensionless", register="all"),
                Channel("480", "dimensionless", register="all"),
                Channel("515", "dimensionless", register="all"),
                Channel("555", "dimensionless", register="all"),
                Channel("590", "dimensionless", register="all"),
                Channel("630", "dimensionless", register="all"),
                Channel("680", "dimensionless", register="all"),
                Channel("clear", "dimensionless", register="all"),
                Channel("nir", "dimensionless", register="all"),
            ],
        ),
    },
}

ANALOG_ACTUATORS = {
    "R0": {
        "R0:pwm0": PhysicalInfo(
            model="pwm",
            address=0,
            type=PlcOutput.pwm,
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
            type=PlcOutput.pwm,
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
            type=PlcOutput.pwm,
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
            type=PlcOutput.pwm,
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
            type=PlcOutput.pwm,
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
            type=PlcOutput.pwm,
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
            type=PlcOutput.pwm,
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
            type=PlcOutput.pwm,
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
            type=PlcOutput.pwm,
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
            type=PlcOutput.pwm,
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
            type=PlcOutput.pwm,
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
            type=PlcOutput.pwm,
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
            type=PlcOutput.analog,
            channels=[
                Channel(
                    "lpm",
                    "liters_per_minute",
                    register="pmc1",
                    type=PlcOutput.analog,
                ),
            ],
        ),
    },
    "R1": {
        "R1:mfc": PhysicalInfo(
            model="mfc",
            address=0,
            type=PlcOutput.analog,
            channels=[
                Channel(
                    "lpm",
                    "liters_per_minute",
                    register="pmc1",
                    type=PlcOutput.analog,
                ),
            ],
        ),
    },
    "R2": {
        "R2:mfc": PhysicalInfo(
            model="mfc",
            address=0,
            type=PlcOutput.analog,
            channels=[
                Channel(
                    "lpm",
                    "liters_per_minute",
                    register="pmc1",
                    type=PlcOutput.analog,
                ),
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
