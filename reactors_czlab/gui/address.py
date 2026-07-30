"""Index the OPC address space by reactor, name and channel.

``OpcClient`` browses the server into three flat ``{nodeid: info}``
dicts. Every screen needs the opposite lookup - given a reactor, a device
and a channel, what is the node id - and the ``<reactor>:<name>:<channel>``
browse-name contract is unwound here, once, rather than in each page.

Pure: it touches no network and imports no nicegui, so it is testable
with fixture dicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reactors_czlab.opcua.client import OpcClient


@dataclass(frozen=True)
class VariableRef:
    """One published variable, and where it sits."""

    nodeid: str
    reactor: str
    name: str
    channel: str


class AddressBook:
    """Lookups over a browsed OPC address space."""

    def __init__(
        self,
        sensors: dict[str, dict[str, tuple[VariableRef, ...]]],
        actuators: dict[str, dict[str, dict[str, VariableRef]]],
        methods: dict[tuple[str, str | None, str], str],
    ) -> None:
        """Store the prepared indices. Use ``build`` instead."""
        self._sensors = sensors
        self._actuators = actuators
        self._methods = methods

    def __repr__(self) -> str:
        """Print how many reactors are indexed."""
        return f"AddressBook({len(self.reactors)} reactors)"

    @classmethod
    def build(
        cls,
        sensor_vars: dict[str, dict],
        actuator_vars: dict[str, dict],
        methods: dict[str, dict],
    ) -> AddressBook:
        """Index the three dicts ``OpcClient`` produces.

        Parameters
        ----------
        sensor_vars, actuator_vars:
            ``{nodeid: {"reactor", "name", "channel", ...}}``.
        methods:
            ``{nodeid: {"reactor": str, "name": list[str]}}``, where
            ``name`` is the browse name split on ``:`` with the reactor
            dropped - one element for a reactor-level method, two for
            one owned by a sensor or an actuator.

        """
        sensors: dict[str, dict[str, list[VariableRef]]] = {}
        for nodeid, info in sensor_vars.items():
            ref = VariableRef(
                nodeid,
                info["reactor"],
                info["name"],
                info["channel"],
            )
            sensors.setdefault(ref.reactor, {}).setdefault(
                ref.name,
                [],
            ).append(ref)

        actuators: dict[str, dict[str, dict[str, VariableRef]]] = {}
        for nodeid, info in actuator_vars.items():
            ref = VariableRef(
                nodeid,
                info["reactor"],
                info["name"],
                info["channel"],
            )
            actuators.setdefault(ref.reactor, {}).setdefault(ref.name, {})[
                ref.channel
            ] = ref

        by_key: dict[tuple[str, str | None, str], str] = {}
        for nodeid, info in methods.items():
            parts = list(info["name"])
            if len(parts) == 1:
                by_key[(info["reactor"], None, parts[0])] = nodeid
            elif len(parts) >= 2:
                by_key[(info["reactor"], parts[0], parts[-1])] = nodeid

        frozen_sensors = {
            reactor: {
                name: tuple(sorted(refs, key=lambda r: r.channel))
                for name, refs in names.items()
            }
            for reactor, names in sensors.items()
        }
        return cls(frozen_sensors, actuators, by_key)

    @classmethod
    def from_client(cls, client: OpcClient) -> AddressBook:
        """Index a connected client's browse results."""
        return cls.build(
            client.sensor_vars,
            client.actuator_vars,
            client.methods,
        )

    @property
    def reactors(self) -> tuple[str, ...]:
        """Every reactor id, sorted, so the UI order is stable."""
        return tuple(sorted({*self._sensors, *self._actuators}))

    def sensors(self, reactor: str) -> dict[str, tuple[VariableRef, ...]]:
        """Sensor name -> its channel variables, sorted by channel."""
        return self._sensors.get(reactor, {})

    def actuators(self, reactor: str) -> dict[str, dict[str, VariableRef]]:
        """Actuator name -> {channel: variable}."""
        return self._actuators.get(reactor, {})

    def variable(
        self,
        reactor: str,
        name: str,
        channel: str,
    ) -> str | None:
        """Node id of one variable, or ``None`` if it is not published."""
        actuator = self._actuators.get(reactor, {}).get(name, {})
        if channel in actuator:
            return actuator[channel].nodeid
        for ref in self._sensors.get(reactor, {}).get(name, ()):
            if ref.channel == channel:
                return ref.nodeid
        return None

    def method(
        self,
        reactor: str,
        owner: str | None,
        name: str,
    ) -> str | None:
        """Node id of a method.

        Parameters
        ----------
        reactor:
            Reactor id, e.g. ``R0``.
        owner:
            The sensor or actuator name the method hangs off, or
            ``None`` for a reactor-level method such as ``set_pairing``.
        name:
            The bare method name.

        """
        return self._methods.get((reactor, owner, name))
