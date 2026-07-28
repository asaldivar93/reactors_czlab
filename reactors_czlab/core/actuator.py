"""Define the actuator class."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from reactors_czlab.core.control import ControlFactory, _Control
from reactors_czlab.core.data import (
    ERROR_VALUE,
    ControlConfig,
    ControlMethod,
    OutputUnit,
    PlcOutput,
)
from reactors_czlab.core.dispenser import (
    DEFAULT_CONTROL_PERIOD,
    Dispenser,
    check_unit,
)
from reactors_czlab.core.hardware import IN_RASPBERRYPI, rpiplc

if TYPE_CHECKING:
    from reactors_czlab.core.data import PhysicalInfo

_logger = logging.getLogger("server.actuator")

PWM_FREQUENCY_HZ = 100


class Actuator(ABC):
    """Base Actuator class."""

    def __init__(
        self,
        identifier: str,
        config: PhysicalInfo,
    ) -> None:
        """Instance base actuator class.

        Parameters
        ----------
        identifier:
            A unique identifier for the actuator
        config:
            A data class with config parameters for the actuator

        """
        self.id = identifier
        self.info = config
        self.channel = config.channels[0]
        #: Set while a calibration run owns the pump. Both the sampling loop
        #: and the fast loop leave the actuator alone while it is set.
        #: Assigned directly (not through the property) - nothing has run
        #: yet, so there is nothing for the property's reset() to bank.
        self._calibrating = False
        self._control_period = DEFAULT_CONTROL_PERIOD
        self.dispenser = Dispenser(
            OutputUnit.duty,
            self.channel,
            self._control_period,
        )
        self.controller = ControlFactory().create_control(
            ControlConfig(method=ControlMethod.manual, value=0),
        )

    def __repr__(self) -> str:
        """Print the actuator id."""
        return f"{type(self).__name__}(id: {self.id})"

    @property
    def controller(self) -> _Control:
        """Get controller."""
        return self._controller

    @controller.setter
    def controller(self, controller: _Control) -> None:
        if not isinstance(controller, _Control):
            error_message = f"Expected a _Control, got {type(controller)}"
            raise TypeError(error_message)
        self._controller = controller

    @property
    def calibrating(self) -> bool:
        """Whether a calibration run currently owns the pump."""
        return self._calibrating

    @calibrating.setter
    def calibrating(self, calibrating: bool) -> None:
        """Toggle the interlock, resetting the dispenser's clock on edge.

        A calibration run drives the pump directly, outside
        ``write_output()`` and ``tick()``, so the dispenser's accrual
        clock would otherwise sit frozen at whatever duty was running
        when the run started. The first real decision after the run
        ends would then attribute the run's entire duration to that
        stale duty, injecting phantom volume into the total.
        ``Dispenser.reset()`` banks whatever was legitimately in flight
        and re-zeroes the clock, so neither edge can span a calibration
        run.
        """
        if calibrating != self._calibrating:
            self.dispenser.reset()
        self._calibrating = calibrating

    @property
    def control_period(self) -> float:
        """Seconds between control decisions."""
        return self._control_period

    @control_period.setter
    def control_period(self, period: float) -> None:
        """Set the period, keeping the dispenser's guard in step.

        Raises
        ------
        ValueError
            If ``period`` is not positive. Mirrors ``Dispenser.__init__``:
            a zero or negative period would silently disable the
            volume-mode re-trigger guard (every
            ``now - self._last_decision`` gap satisfies
            ``< control_period``), letting a standing manual volume
            demand re-fire on every call instead of once per period.

        """
        if period <= 0:
            error_message = f"control_period must be positive, got {period}"
            raise ValueError(error_message)
        self._control_period = period
        self.dispenser.control_period = period

    def write_output(self, sens_value: float) -> None:
        """Write the actuator value derived from a sensor reading."""
        if self.calibrating:
            return
        if sens_value == ERROR_VALUE:
            # The sentinel is not a measurement. Acting on it would make a
            # boundaries controller dose forever on a dead probe.
            _logger.warning(
                "Holding %s: the reference sensor read failed",
                self.id,
            )
            return
        demand = self.controller.get_value(sens_value)
        self._write_if_changed(self.dispenser.duty(demand))

    def tick(self) -> None:
        """Let the dispenser finish a delivery it already started."""
        if self.calibrating:
            return
        value = self.dispenser.tick()
        if value is not None:
            self._write_if_changed(value)

    def _write_if_changed(self, value: float) -> None:
        """Push a duty value to the hardware only when it actually moved."""
        if value != self.channel.old_value:
            self.channel.old_value = value
            self.write(value)
            _logger.debug(
                "Write %s to %s: %s", value, self.id, self.controller
            )

    def set_control_config(self, config: ControlConfig) -> None:
        """Change the current configuration of the actuator outputs.

        Parameters
        ----------
        config:
            A dataclass with the parameters of the new controller

        """
        reason = check_unit(config.output_unit, self.channel)
        if reason is not None:
            _logger.warning("Rejected config for %s: %s", self.id, reason)
            return

        dispenser = self.dispenser
        if config.output_unit is not dispenser.unit:
            dispenser = Dispenser(
                config.output_unit,
                self.channel,
                self._control_period,
            )
            # Force the outgoing dispenser to accrue whatever was still
            # in flight before reading its total - otherwise the last
            # partial accrual interval is silently dropped on the swap.
            self.dispenser.reset()
            # The total records the physical pump, not the configuration.
            dispenser.total_volume = self.dispenser.total_volume

        min_val, max_val = dispenser.demand_limits()
        try:
            new_controller = ControlFactory().create_control(
                config,
                min_val=min_val,
                max_val=max_val,
            )
        except TypeError:
            # Each control class checks that the values
            # passed are of the correct type
            _logger.exception("Wrong attributes in %s: %s", self.id, config)
            return

        # Replace the controller only if the configuration actually changed,
        # so an unrelated OPC write does not reset a running timer or PID.
        if (
            self.controller != new_controller
            or dispenser is not self.dispenser
        ):
            self.dispenser = dispenser
            self.controller = new_controller
            _logger.info(
                "Control config update - %s: %s in %s",
                self.id,
                self.controller,
                self.dispenser.unit,
            )

    def refresh_controller_limits(self) -> None:
        """Re-derive the controller's clamp range from the dispenser.

        A pump calibration refit changes what ``self.dispenser.
        demand_limits()`` returns (e.g. a steeper slope means the same
        max duty now delivers more mL/min), but it changes neither the
        control method nor its configuration, so ``set_control_config``
        would not rebuild the controller for it - nothing else picks the
        new range up on its own.

        ``min_val``/``max_val`` are mutated on the existing controller
        object rather than swapping in one built by ``ControlFactory``,
        so a running PID keeps its integral sum, its last measurement
        and its identity (``actuator.controller is`` the same object
        before and after). Rebuilding it would zero that state for a
        write that changed nothing about the configuration.
        """
        min_val, max_val = self.dispenser.demand_limits()
        self.controller.min_val = min_val
        self.controller.max_val = max_val

    @abstractmethod
    def write(self, value: float) -> None:
        """Write actuator method."""


class RandomActuator(Actuator):
    """Class for testing without hardware."""

    def write(self, value: float) -> None:
        """Record the value in the channel."""
        self.channel.value = value


class PlcActuator(Actuator):
    """Class to interface with the RaspberryPi PLC pins."""

    def __init__(
        self,
        identifier: str,
        config: PhysicalInfo,
    ) -> None:
        """Interface a pin as an actuator class.

        Parameters
        ----------
        identifier:
            A unique identifier for the actuator
        config:
            A data class with config parameters for the actuator

        """
        super().__init__(identifier, config)
        if IN_RASPBERRYPI:
            chn = self.channel
            rpiplc.pin_mode(chn.pin, rpiplc.OUTPUT)
            if chn.type == PlcOutput.pwm:
                rpiplc.analog_write_set_frequency(chn.pin, PWM_FREQUENCY_HZ)

    def write(self, value: float) -> None:
        """Write to physical pin."""
        if IN_RASPBERRYPI:
            chn = self.channel
            rpiplc.analog_write(chn.pin, int(value))
            chn.value = value
            _logger.debug("Actuator %s - %s", self.id, value)
