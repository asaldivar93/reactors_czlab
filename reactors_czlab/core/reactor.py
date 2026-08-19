"""Define the reactor class."""

from __future__ import annotations

import asyncio
import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from reactors_czlab.core.data import ERROR_VALUE

if TYPE_CHECKING:
    from reactors_czlab.core.actuator import Actuator
    from reactors_czlab.core.autotune import AutotuneCoordinator, AutotuneRun
    from reactors_czlab.core.sensor import Sensor

_logger = logging.getLogger("server.reactor")

#: Reference value handed to controllers that are not paired to a sensor.
#: Manual and timer controllers ignore it; the others should not be used
#: unpaired.
UNPAIRED_INPUT = 0.0

#: How often unpaired actuators are refreshed and deliveries advanced, in
#: seconds.
UNPAIRED_PERIOD = 0.05

#: Operator-configurable bounds for the server-wide sampling period.
MIN_SAMPLE_PERIOD = 1.0
MAX_SAMPLE_PERIOD = 30.0


@dataclass
class SamplingState:
    """Shared state of the sampling loop.

    Attributes
    ----------
    pairings:
        {sensor_id: [(actuator_id, channel_index), ...]}
    sensors:
        Ids of the sensors read by the loop
    actuators:
        Ids of the actuators that may be paired to a sensor
    lock:
        Guards pairings against concurrent OPC method calls

    """

    pairings: dict[str, list[tuple[str, int]]] = field(
        default_factory=lambda: defaultdict(list),
    )
    sensors: list[str] = field(default_factory=list)
    actuators: list[str] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class UnpairedState:
    """Shared state of the loop driving actuators with no reference sensor."""

    actuators: list[str] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Reactor:
    """Class representation of the reactors."""

    def __init__(
        self,
        identifier: str,
        volume: float,
        sensors: list[Sensor],
        actuators: list[Actuator],
        period: float,
    ) -> None:
        """Initialize the reactor.

        Parameters
        ----------
        identifier:
            A unique identifier for the reactor.
        volume:
            The initial volume of the reactor.
        sensors:
            A list containing the Sensor instances.
        actuators:
            A list containing the Actuator instances.
        period:
            Seconds between sensor samples.

        """
        self.id: str = identifier
        self.volume: float = volume
        self.period: float = period
        self.sensors = sensors
        self.actuators = actuators
        self.sampling = SamplingState()
        self.unpaired = UnpairedState()
        # Restored timers are held at zero until a fresh process cycle makes
        # their reference context valid. Paired timers arm on their first
        # successful channel read; unpaired timers arm after this reactor's
        # first complete sampling cycle.
        self._recovery_paired_timers: set[str] = set()
        self._recovery_unpaired_timers: set[str] = set()
        # Installed by ReactorOpc after every actuator OPC node exists. Core
        # reactors used by tests or non-OPC callers remain perfectly valid
        # without an autotune coordinator.
        self.autotune: AutotuneCoordinator | None = None

        self.sampling.sensors = [s.id for s in self.sensors.values()]
        self.sampling.actuators = [a.id for a in self.actuators.values()]
        # Every actuator starts unpaired; set_pairing() moves it out of this
        # list and unpair() puts it back.
        self.unpaired.actuators = [a.id for a in self.actuators.values()]

        # The volume-mode re-trigger guard needs to know how often a paired
        # actuator gets a decision.
        for actuator in self.actuators.values():
            actuator.control_period = period

    def __repr__(self) -> str:
        """Print the reactor id."""
        return f"Reactor(id: {self.id})"

    @property
    def sensors(self) -> dict[str, Sensor]:
        """Get the sensors dict."""
        return self._sensors

    @sensors.setter
    def sensors(self, sensors: list[Sensor]) -> None:
        """Set the sensors as a dict."""
        if not isinstance(sensors, list):
            error_message = f"sensors must be a list, got {type(sensors)}"
            raise TypeError(error_message)
        self._sensors = {s.id: s for s in sensors}

    @property
    def actuators(self) -> dict[str, Actuator]:
        """Get the actuators dict."""
        return self._actuators

    @actuators.setter
    def actuators(self, actuators: list[Actuator]) -> None:
        """Set the actuators as a dict."""
        if not isinstance(actuators, list):
            error_message = f"actuators must be a list, got {type(actuators)}"
            raise TypeError(error_message)
        self._actuators = {a.id: a for a in actuators}

    def update_paired_actuators(self) -> None:
        """Drive every paired actuator from its reference sensor channel.

        The caller is responsible for holding ``self.sampling.lock``.
        """
        for sensor_id, paired in self.sampling.pairings.items():
            sensor = self.sensors[sensor_id]
            for aid, chn in paired:
                actuator = self.actuators[aid]
                try:
                    value = sensor.channels[chn].value
                except IndexError:
                    _logger.error("%s is not a channel in %s", chn, sensor.id)
                else:
                    if value != ERROR_VALUE and aid in self._recovery_paired_timers:
                        actuator.controller.reset_runtime()
                        self._recovery_paired_timers.remove(aid)
                    actuator.write_output(value)

    def defer_recovered_timer(self, actuator_id: str, *, paired: bool) -> None:
        """Hold one restored timer until its post-restart safety gate."""
        timers = (
            self._recovery_paired_timers
            if paired
            else self._recovery_unpaired_timers
        )
        timers.add(actuator_id)

    def clear_recovery_gates(self) -> None:
        """Clear all one-shot restart gates when recovery is abandoned."""
        self._recovery_paired_timers.clear()
        self._recovery_unpaired_timers.clear()

    def clear_recovery_gate(self, actuator_id: str) -> None:
        """Release one gate after an operator replaces its restored config."""
        self._recovery_paired_timers.discard(actuator_id)
        self._recovery_unpaired_timers.discard(actuator_id)

    def reclassify_recovery_gate(self, actuator_id: str, *, paired: bool) -> None:
        """Move a pending timer gate when its pairing changes before arming."""
        pending = actuator_id in (
            self._recovery_paired_timers | self._recovery_unpaired_timers
        )
        self.clear_recovery_gate(actuator_id)
        if pending:
            self.defer_recovered_timer(actuator_id, paired=paired)

    def _arm_recovered_unpaired_timers(self) -> None:
        """Start restored unpaired timers after the first sampling cycle."""
        for actuator_id in self._recovery_unpaired_timers:
            self.actuators[actuator_id].controller.reset_runtime()
        self._recovery_unpaired_timers.clear()

    def update_period(self, period: float) -> None:
        """Change the sampling and actuator control periods together.

        The running sampling loop snapshots ``self.period`` at the start of
        each read/sleep cycle, so an update takes effect on its next cycle
        without interrupting work in flight.

        Parameters
        ----------
        period:
            New sampling period in seconds.

        Raises
        ------
        ValueError
            If ``period`` is not finite or is outside the supported
            1--30 second range. No state is changed on rejection.

        """
        if (
            not math.isfinite(period)
            or period < MIN_SAMPLE_PERIOD
            or period > MAX_SAMPLE_PERIOD
        ):
            error_message = (
                f"sampling period must be finite and between "
                f"{MIN_SAMPLE_PERIOD:g} and {MAX_SAMPLE_PERIOD:g} seconds, "
                f"got {period}"
            )
            raise ValueError(error_message)

        # There are no awaits in this operation, so a running asyncio loop
        # cannot observe the reactor and its actuator guards half-updated.
        self.period = period
        for actuator in self.actuators.values():
            actuator.control_period = period

    def active_autotune_run(self) -> AutotuneRun | None:
        """Return the run that currently owns a pump pair, if any."""
        if self.autotune is None or self.autotune.run is None:
            return None
        run = self.autotune.run
        return run if run.is_active else None

    def autotune_involves_actuator(self, actuator_id: str) -> bool:
        """Whether an active run owns ``actuator_id`` as base or acid."""
        run = self.active_autotune_run()
        return run is not None and actuator_id in {run.base_id, run.acid_id}

    def abort_autotune_for_config_change(self, actuator_id: str) -> None:
        """Abort when another OPC client changes a selected controller."""
        run = self.active_autotune_run()
        if run is None or actuator_id not in {run.base_id, run.acid_id}:
            return
        run.abort(f"configuration changed for selected actuator {actuator_id}")

    def control_config_changed(self, actuator_id: str) -> None:
        """Release restart gates and abort an affected autotune run."""
        self.clear_recovery_gate(actuator_id)
        self.abort_autotune_for_config_change(actuator_id)

    def update_autotune(self) -> None:
        """Feed the selected pH reading to the active run.

        This is called immediately after all sensor reads and before ordinary
        paired controller decisions. The selected pumps are interlocked, so
        their normal decisions are inert; unrelated pairings still see the
        same fresh sample in :meth:`update_paired_actuators`.
        """
        run = self.active_autotune_run()
        if run is None:
            return
        try:
            sensor = self.sensors[run.sensor_id]
            channels = [
                channel
                for channel in sensor.channels
                if str(getattr(channel, "units", "")).lower() == "ph"
            ]
            if len(channels) != 1:
                error_message = (
                    f"{run.sensor_id} no longer has exactly one pH channel"
                )
                raise ValueError(error_message)
            ph = channels[0].value
        except (KeyError, ValueError) as exc:
            run.abort(f"configuration loss: {exc}")
            return
        run.sample(ph)

    async def sampling_loop(self, sample_ready: asyncio.Event) -> None:
        """Read all sensors, then update the actuators paired to them."""
        loop = asyncio.get_running_loop()
        next_tick = loop.time()
        while True:
            # Keep one complete read/sleep cycle on one period. A settings
            # change never wakes an existing sleep or shortens a read cycle;
            # the next iteration snapshots the new value.
            period = self.period
            async with asyncio.TaskGroup() as tg:
                for sensor in self.sensors.values():
                    tg.create_task(sensor.read())

            self.update_autotune()

            async with self.sampling.lock:
                self.update_paired_actuators()

            self._arm_recovered_unpaired_timers()

            # Flag that the sensing loop finished
            sample_ready.set()

            # Drift correct the next tick. If a read overran the period, skip
            # the ticks we missed instead of free running to catch up.
            next_tick += period
            now = loop.time()
            if next_tick < now:
                _logger.warning(
                    "%s sampling overran its %.1fs period by %.3fs",
                    self.id,
                    period,
                    now - next_tick,
                )
                next_tick = now + period
            await asyncio.sleep(max(0.0, next_tick - now))

    async def actuator_loop(self) -> None:
        """Refresh unpaired actuators and advance every delivery in flight.

        Two jobs, one loop. Unpaired actuators need their controller run
        often; paired ones are decided once per sampling period but their
        deliveries have to be ended on a far finer grain than that.

        No lock guards the tick: ``write_output()`` and ``tick()`` are both
        synchronous and never await, so a decision from the sampling loop
        cannot interleave with a delivery ending here.
        """
        while True:
            async with self.unpaired.lock:
                for aid in self.unpaired.actuators:
                    if aid in self._recovery_unpaired_timers:
                        continue
                    self.actuators[aid].write_output(UNPAIRED_INPUT)

            run = self.active_autotune_run()
            owned_ids: set[str] = set()
            if run is not None:
                owned_ids = {run.base_id, run.acid_id}
                run.tick()

            for aid, actuator in self.actuators.items():
                if aid not in owned_ids:
                    actuator.tick()

            await asyncio.sleep(UNPAIRED_PERIOD)

    def stop(self) -> None:
        """Drive every actuator to zero and cancel deliveries in flight."""
        run = self.active_autotune_run()
        if run is not None:
            run.abort("reactor stopped")
        for actuator in self.actuators.values():
            actuator.dispenser.reset()
            actuator.write(0)
