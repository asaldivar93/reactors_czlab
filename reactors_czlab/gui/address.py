"""An index over what the OPC client browsed.

``OpcClient`` hands back three flat ``{nodeid: info}`` dicts. Pages ask
questions those dicts cannot answer directly - "which sensors does R0
have", "what is the node id of R0's pwm0 setpoint", "where is this
reactor's set_pairing method". This turns the flat dicts into those
lookups, once, at connect time.

Pure: it takes dicts and returns strings. Everything here is testable
without a server, which is the point - node id bookkeeping is exactly
the kind of thing that silently returns None and shows an empty page.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Actuator channels that are the actuator's own state rather than a
#: piece of its control configuration. Used to tell the two apart when
#: listing what an actuator publishes.
ACTUATOR_STATE_CHANNELS = frozenset(
    {"curr_value", "total_volume", "cal_a", "cal_b", "cal_r2"},
)


@dataclass(frozen=True)
class VariableRef:
    """One published variable, and where to find it."""

    nodeid: str
    reactor: str
    name: str
    channel: str


@dataclass
class AddressBook:
    """Everything a page needs to look up, keyed the way pages ask.

    Attributes
    ----------
    variables:
        ``(reactor, name, channel)`` -> node id.
    methods:
        ``(reactor, owner, method)`` -> node id, where ``owner`` is the
        sensor or actuator the method sits on, or ``None`` for a method
        on the reactor itself such as ``set_pairing``.
    sensor_refs, actuator_refs:
        ``reactor`` -> ``name`` -> the variables that device publishes.

    """

    variables: dict[tuple[str, str, str], str] = field(default_factory=dict)
    methods: dict[tuple[str, str | None, str], str] = field(
        default_factory=dict,
    )
    sensor_refs: dict[str, dict[str, list[VariableRef]]] = field(
        default_factory=dict,
    )
    actuator_refs: dict[str, dict[str, list[VariableRef]]] = field(
        default_factory=dict,
    )

    def __repr__(self) -> str:
        """Print how much was indexed."""
        return (
            f"AddressBook({len(self.reactors)} reactors, "
            f"{len(self.variables)} variables, {len(self.methods)} methods)"
        )

    @classmethod
    def from_client(cls, client: object) -> AddressBook:
        """Index a connected ``OpcClient``."""
        return cls.from_mappings(
            client.sensor_vars,
            client.actuator_vars,
            client.methods,
        )

    @classmethod
    def from_mappings(
        cls,
        sensor_vars: dict[str, dict],
        actuator_vars: dict[str, dict],
        methods: dict[str, dict],
    ) -> AddressBook:
        """Index the three browse dicts.

        Parameters
        ----------
        sensor_vars, actuator_vars:
            ``{nodeid: {"reactor", "name", "channel"}}`` as
            ``OpcClient.match_tree`` builds them.
        methods:
            ``{nodeid: {"reactor": str, "name": list[str]}}`` as
            ``OpcClient.get_methods`` builds them. A one-element name is
            a method on the reactor; two elements are a method on a
            sensor or actuator.

        """
        book = cls()
        for source, refs in (
            (sensor_vars, book.sensor_refs),
            (actuator_vars, book.actuator_refs),
        ):
            for nodeid, info in source.items():
                ref = VariableRef(
                    nodeid=nodeid,
                    reactor=info["reactor"],
                    name=info["name"],
                    channel=info["channel"],
                )
                book.variables[(ref.reactor, ref.name, ref.channel)] = nodeid
                refs.setdefault(ref.reactor, {}).setdefault(
                    ref.name,
                    [],
                ).append(ref)

        for nodeid, info in methods.items():
            parts = list(info["name"])
            if not parts:
                continue
            owner = parts[0] if len(parts) > 1 else None
            book.methods[(info["reactor"], owner, parts[-1])] = nodeid

        for refs in (book.sensor_refs, book.actuator_refs):
            for devices in refs.values():
                for device_refs in devices.values():
                    device_refs.sort(key=lambda ref: ref.channel)

        return book

    @property
    def reactors(self) -> list[str]:
        """Every reactor that published a sensor or an actuator."""
        return sorted(set(self.sensor_refs) | set(self.actuator_refs))

    def sensors(self, reactor: str) -> dict[str, list[VariableRef]]:
        """The sensors of one reactor, and what each publishes."""
        return self.sensor_refs.get(reactor, {})

    def actuators(self, reactor: str) -> dict[str, list[VariableRef]]:
        """The actuators of one reactor, and what each publishes."""
        return self.actuator_refs.get(reactor, {})

    def variable(
        self,
        reactor: str,
        name: str,
        channel: str,
    ) -> str | None:
        """The node id of one variable, or None if it is not published."""
        return self.variables.get((reactor, name, channel))

    def method(
        self,
        reactor: str,
        owner: str | None,
        name: str,
    ) -> str | None:
        """The node id of one method, or None if the server has no such."""
        return self.methods.get((reactor, owner, name))

    def has_method(self, reactor: str, owner: str | None, name: str) -> bool:
        """Whether the server publishes a method.

        Lets a page hide a control the server cannot serve, rather than
        offering it and failing at the call.
        """
        return self.method(reactor, owner, name) is not None

    def control_channels(self, reactor: str, actuator: str) -> list[str]:
        """The control-config channels an actuator publishes.

        Everything under its ControlMethod object, which is whatever is
        left once the actuator's own state channels are removed. Derived
        rather than hardcoded so a config field added to the server
        appears here without this module changing.
        """
        refs = self.actuators(reactor).get(actuator, [])
        return [
            ref.channel
            for ref in refs
            if ref.channel not in ACTUATOR_STATE_CHANNELS
        ]
