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
)
from reactors_czlab.core.dispenser import (
    DEFAULT_CONTROL_PERIOD,
    Dispenser,
    check_unit,
)

if TYPE_CHECKING:
    from reactors_czlab.core.data import PhysicalInfo

_logger = logging.getLogger("server.actuator")


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
        # Identity token for the one autotune run allowed to use the
        # dispenser while the ordinary control paths are interlocked.
        self._autotune_owner: object | None = None
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

        That ``reset()`` also throws away any dose deadline, and this
        setter deliberately does not write 0 afterwards - the caller
        owns the pin across the whole interlock. ``CalibrationRun`` writes
        its requested duty immediately and zeroes it in ``finally``;
        ``claim_autotune()`` zeroes the pin as part of taking ownership and
        then permits only owner-authenticated dispenser demands and ticks.
        """
        if not calibrating and self._autotune_owner is not None:
            error_message = (
                f"{self.id} interlock may only be cleared by its autotune owner"
            )
            raise RuntimeError(error_message)
        if calibrating != self._calibrating:
            self.dispenser.reset()
        self._calibrating = calibrating

    @property
    def autotune_owner(self) -> object | None:
        """Identity token of the active autotune run, if any."""
        return self._autotune_owner

    def claim_autotune(self, owner: object) -> None:
        """Give ``owner`` exclusive autotune access to this actuator.

        Raises
        ------
        RuntimeError
            If a pump calibration or another autotune already owns the
            actuator.
        """
        if owner is None:
            error_message = "an autotune owner token may not be None"
            raise ValueError(error_message)
        if self._autotune_owner is not None:
            error_message = f"{self.id} is already owned by an autotune"
            raise RuntimeError(error_message)
        if self.calibrating:
            error_message = f"{self.id} is already calibrating"
            raise RuntimeError(error_message)
        self._autotune_owner = owner
        try:
            self.calibrating = True
            # Raising the interlock cancels any dispenser deadline, so stop
            # the physical pin here as part of taking ownership. Otherwise a
            # pre-existing dose would be left ON with nothing able to tick it.
            self.write(0.0)
            self.channel.old_value = 0.0
        except Exception:
            self._autotune_owner = None
            self.calibrating = False
            raise

    def autotune_demand(self, owner: object, volume_ml: float) -> None:
        """Issue an owned volume demand through the existing dispenser.

        The ownership check is intentionally based on object identity. A
        completed run must not be able to drive a later run merely because
        it reused the same reactor or actuator ids.
        """
        self._require_autotune_owner(owner)
        if self.dispenser.unit is not OutputUnit.volume:
            error_message = f"{self.id} is not configured for volume output"
            raise RuntimeError(error_message)
        self._write_if_changed(self.dispenser.duty(volume_ml))

    def autotune_tick(self, owner: object) -> None:
        """Advance only the delivery owned by ``owner``."""
        self._require_autotune_owner(owner)
        value = self.dispenser.tick()
        if value is not None:
            self._write_if_changed(value)

    def release_autotune(self, owner: object) -> None:
        """Bank delivered volume, stop the pump, and release ``owner``.

        Cleanup fields are restored even if the hardware write raises. This
        makes an unexpected terminal path unable to leave the software
        interlocks wedged around a stale run.
        """
        # Recover an orphaned interlock (owner was cleared unexpectedly), but
        # never let a stale run stop a pump that a different, newer run owns.
        if self._autotune_owner is not owner and self._autotune_owner is not None:
            error_message = f"{self.id} is not owned by this autotune run"
            raise PermissionError(error_message)
        try:
            self.dispenser.reset()
            self.write(0.0)
        finally:
            self.channel.old_value = 0.0
            self._autotune_owner = None
            self.calibrating = False

    def _require_autotune_owner(self, owner: object) -> None:
        """Reject missing, wrong, stale, or externally cleared ownership."""
        if self._autotune_owner is not owner or not self.calibrating:
            error_message = f"{self.id} is not owned by this autotune run"
            raise PermissionError(error_message)

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
        self.refresh_controller_limits()

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
        self._write_if_changed(
            self.dispenser.duty(
                demand,
                pid=self.controller.method is ControlMethod.pid,
            ),
        )

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

    def set_control_config(self, config: ControlConfig) -> str | None:
        """Change the current configuration of the actuator outputs.

        Parameters
        ----------
        config:
            A dataclass with the parameters of the new controller

        Returns
        -------
        str or None
            An operator-readable rejection reason, or ``None`` when the
            configuration was accepted.

        """
        reason = check_unit(config.output_unit, self.channel)
        if reason is not None:
            _logger.warning("Rejected config for %s: %s", self.id, reason)
            return reason

        dispenser = self.dispenser
        if config.output_unit is not dispenser.unit:
            dispenser = Dispenser(
                config.output_unit,
                self.channel,
                self._control_period,
            )
        min_val, max_val = dispenser.demand_limits(
            pid=config.method is ControlMethod.pid,
        )
        try:
            new_controller = ControlFactory().create_control(
                config,
                min_val=min_val,
                max_val=max_val,
            )
        except TypeError as err:
            # Each control class checks that the values
            # passed are of the correct type
            _logger.exception("Wrong attributes in %s: %s", self.id, config)
            return str(err)

        if dispenser is not self.dispenser:
            # Validation has succeeded, so it is now safe to disturb the
            # running dispenser. A rejected unit-and-field change must not
            # stop a pump before the caller learns that it was rejected.
            # Force the outgoing dispenser to accrue whatever was still in
            # flight before reading its total, then stop the old delivery.
            self.dispenser.reset()
            if not self.calibrating:
                self._write_if_changed(0.0)
            # The total records the physical pump, not the configuration.
            dispenser.total_volume = self.dispenser.total_volume

        # How the change is applied depends on what actually changed:
        #
        #  - output unit changed: a new dispenser is already built above,
        #    so swap both wholesale. The demand range moved to a new unit
        #    and the gains must be retuned for it anyway, so resetting the
        #    controller's runtime state is correct.
        #  - control method changed: swap the controller - the state of
        #    one method's controller means nothing to another's.
        #  - same method, config changed: adopt the new configuration onto
        #    the *running* controller, preserving its integral / timer
        #    phase / current output. An operator retuning a gain or the
        #    setpoint of a live PID must not bump it. refresh_derived_
        #    limits() follows so a controller that derives state from the
        #    (possibly changed) range tracks it, mirroring
        #    refresh_controller_limits().
        #  - nothing changed: no-op, so an unrelated OPC write is inert.
        if dispenser is not self.dispenser:
            self.dispenser = dispenser
            self.controller = new_controller
        elif type(self.controller) is not type(new_controller):
            self.controller = new_controller
        elif self.controller != new_controller:
            self.controller.adopt_config(new_controller)
            self.controller.refresh_derived_limits()
        else:
            return None

        _logger.info(
            "Control config update - %s: %s in %s",
            self.id,
            self.controller,
            self.dispenser.unit,
        )
        return None

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
        ``_Control.refresh_derived_limits()`` is called afterwards so a
        controller that derives extra state from the range - the PID's
        anti-windup band - follows it too, instead of only the clamp.

        A non-positive upper limit (``min_val >= max_val``) is zeroed
        rather than installed as-is: ``fit()``/``set_duties()`` refuse
        to install a calibration that produces one, but a hand-edited
        file loaded through ``reload()`` still could, and this is the
        last line of defense. Earlier this method *kept the stale
        range* on refusal instead - which is what let a PID go on
        commanding a positive demand against a calibration that could
        no longer deliver it, dividing by zero in
        ``Dispenser._start_dose``. Zeroing forces every clamped
        controller to demand nothing until the calibration is fixed,
        rather than continuing to demand something the pump can no
        longer express. Manual/timer/on-boundaries controllers do not
        consult ``min_val``/``max_val`` at all, so this by itself is
        not the primary guard against a bad calibration reaching those
        - ``fit()``/``set_duties()``/``reload()`` refusing to install
        one is - but it closes the gap for the controller that does.
        """
        min_val, max_val = self.dispenser.demand_limits(
            pid=self.controller.method is ControlMethod.pid,
        )
        if max_val <= min_val:
            _logger.warning(
                "%s demand range (%s, %s) is non-positive; zeroing the "
                "controller's limits instead of leaving the old (%s, "
                "%s) in place, since that range is no longer "
                "deliverable under the calibration now on the channel",
                self.id,
                min_val,
                max_val,
                self.controller.min_val,
                self.controller.max_val,
            )
            min_val, max_val = 0.0, 0.0
        self.controller.min_val = min_val
        self.controller.max_val = max_val
        self.controller.refresh_derived_limits()

    @abstractmethod
    def write(self, value: float) -> None:
        """Write actuator method."""


class RandomActuator(Actuator):
    """Class for testing without hardware."""

    def write(self, value: float) -> None:
        """Record the value in the channel."""
        self.channel.value = value
