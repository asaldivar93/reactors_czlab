"""Generic sensor interfaces and simulated sensors."""

from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from typing import Protocol

from reactors_czlab.core.data import PhysicalInfo

_logger = logging.getLogger("server.sensors")


class CalibrationStatus(Protocol):
    """Structural result returned by sensors with calibration support."""

    point: str
    code: int
    value: float
    quality: float
    process_value: float

    @property
    def ok(self) -> bool:
        """Whether the sensor accepted this calibration point."""
        ...

    @property
    def text(self) -> str:
        """Operator-facing interpretation of the status code."""
        ...


class Sensor(ABC):
    """Base sensor."""

    def __init__(
        self,
        identifier: str,
        config: PhysicalInfo,
    ) -> None:
        """Instance a Base sensor class.

        Parameters
        ----------
        identifier:
            A unique identifier for the sensor
        config:
            A PhysicalInfo dataclass with sensor information

        """
        self.id = identifier
        self.sensor_info = config
        self.address = config.address
        self.channels = config.channels

    def __repr__(self) -> str:
        """Print sensor id."""
        return f"{type(self).__name__}(id: {self.id})"

    @abstractmethod
    async def read(self) -> None:
        """Read all sensor channels."""

    async def write_calibration(
        self,
        cal_point: float,
        cal_value: float,
    ) -> tuple[str, float, float]:
        """Do a one point calibration.

        The default reports that the sensor cannot be calibrated over the
        bus. Sensors that support it override this.
        """
        _logger.warning(
            "%s does not support calibration (point %s, value %s)",
            self.id,
            cal_point,
            cal_value,
        )
        return ("unsupported", 0.0, 0.0)

    async def read_calibration_status(
        self,
        cal_point: float,
    ) -> CalibrationStatus | None:
        """Report what a calibration point currently holds.

        The default reports that the sensor has no calibration points to
        read. Sensors that support it override this.

        Returns
        -------
        CalibrationStatus | None
            None when the sensor cannot be calibrated over the bus.
        """
        _logger.debug(
            "%s has no calibration point %s to read",
            self.id,
            cal_point,
        )
        return None


class RandomSensor(Sensor):
    """Sensor producing gaussian noise, for running without hardware."""

    async def read(self) -> None:
        """Set every channel to a value with a gaussian distribution."""
        await asyncio.sleep(0.15)
        debug_msg = []
        for chn in self.channels:
            value = round(random.gauss(35, 1), 2)
            chn.value = value
            debug_msg.append([chn.description, value])
        _logger.debug("In %s - %s", self.id, debug_msg)
