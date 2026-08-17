"""Pair and unpair actuators from sensor channels.

The table is read from the ``R{n}:pairings`` variable the server
publishes. The add form pre-validates everything ``_validate_pair`` and
``set_pairing`` check - reactor membership, and that the actuator is not
already paired - so a ``False`` return really is unexpected and is
reported as such rather than being the normal failure path.

The channel selector submits a ``ChannelIndex``, read from the property
on each channel variable, because ``set_pairing`` takes the index into
``sensor.channels`` and browsing only yields names.
"""

from __future__ import annotations

import json
import logging

from nicegui import ui

from reactors_czlab.gui.components.shell import disable_when_read_only
from reactors_czlab.gui.state import STATE

_logger = logging.getLogger("gui")

#: Cached ``R{n}:pairings`` node ids, and the channel indices read off
#: the sensor variables. Node ids are only stable for the life of a
#: server process, so both are cleared on disconnect - a stale id
#: degrades to an empty panel, which is indistinguishable from "nothing
#: is paired", the worst way for this screen to fail.
_PAIRINGS_NODES: dict[str, str] = {}
_CHANNEL_INDICES: dict[tuple[str, str, str], int] = {}


def forget_cached_nodes() -> None:
    """Drop the cached node ids. Called on disconnect."""
    _PAIRINGS_NODES.clear()
    _CHANNEL_INDICES.clear()


async def _pairings_node(reactor: str) -> object | None:
    """The node holding this reactor's pairing table."""
    client = STATE.client
    if client is None or client.client is None:
        return None

    nodeid = _PAIRINGS_NODES.get(reactor)
    if nodeid is not None:
        return client.client.get_node(nodeid)

    objects = client.client.nodes.objects
    for node in await objects.get_children():
        name = (await node.read_browse_name()).Name
        if name != reactor:
            continue
        for child in await node.get_children():
            child_name = (await child.read_browse_name()).Name
            if child_name == f"{reactor}:pairings":
                _PAIRINGS_NODES[reactor] = child.nodeid.to_string()
                return child
    return None


async def read_pairings(reactor: str) -> list[dict]:
    """The reactor's current pairings, as published by the server."""
    node = await _pairings_node(reactor)
    if node is None:
        _logger.warning("No pairings variable for %s", reactor)
        return []
    try:
        payload = await node.read_value()
        return json.loads(payload)
    except (OSError, TypeError, ValueError) as err:
        _logger.warning("Could not read %s pairings: %s", reactor, err)
        return []


async def channel_index(
    reactor: str,
    sensor: str,
    channel: str,
) -> int | None:
    """The index set_pairing wants for a named channel.

    Read from the ``ChannelIndex`` property the server publishes on each
    channel variable rather than inferred from list order, which would
    silently pair the wrong channel if the inventory were reordered.
    """
    key = (reactor, sensor, channel)
    if key in _CHANNEL_INDICES:
        return _CHANNEL_INDICES[key]

    client = STATE.client
    if client is None or client.client is None or STATE.book is None:
        return None
    nodeid = STATE.book.variable(reactor, sensor, channel)
    if nodeid is None:
        return None

    node = client.client.get_node(nodeid)
    try:
        for child in await node.get_children():
            name = (await child.read_browse_name()).Name
            if name == "ChannelIndex":
                index = int(await child.read_value())
                _CHANNEL_INDICES[key] = index
                return index
    except (OSError, TypeError, ValueError) as err:
        _logger.warning(
            "Could not read ChannelIndex of %s:%s: %s",
            sensor,
            channel,
            err,
        )
    return None


def device_id(reactor: str, name: str) -> str:
    """The full device id the server's methods expect.

    The address book keys devices by the middle part of their browse
    name - ``R0:biomass:415`` gives ``biomass`` - but ``set_pairing``
    validates its arguments against ``sampling.sensors``, which holds
    the full ids. Passing the short name is refused with
    "biomass is not a sensor of R0".
    """
    return f"{reactor}:{name}"


def short_name(identifier: str) -> str:
    """The address book's key for a full device id.

    ``R0:biomass`` gives ``biomass``. The published pairing table
    carries full ids, so this is what turns a published row back into
    something that can be looked up or compared against the book.
    """
    _, _, name = identifier.partition(":")
    return name or identifier


def paired_actuators(pairings: list[dict]) -> set[str]:
    """Which actuators are already following a sensor channel.

    Returned as short names, so the result can be compared against the
    address book's actuator keys. Comparing published full ids against
    those keys never matched, so every actuator stayed on offer however
    many were already paired.
    """
    return {short_name(row["actuator"]) for row in pairings}


async def label_channels(reactor: str, rows: list[dict]) -> list[dict]:
    """Add the channel *name* to each published pairing row.

    The server publishes the channel as the index set_pairing was given,
    which is what unpair needs back but means nothing to an operator.
    The name cannot be recovered from the address book - its channel
    lists are sorted for display, not in index order - so each sensor's
    ChannelIndex properties are resolved and inverted here.
    """
    if STATE.book is None:
        return rows

    labelled = []
    for row in rows:
        sensor = short_name(row["sensor"])
        name = None
        for ref in STATE.book.sensors(reactor).get(sensor, []):
            index = await channel_index(reactor, sensor, ref.channel)
            if index == row["channel"]:
                name = ref.channel
                break
        labelled.append(
            {
                **row,
                "channel_name": name or str(row["channel"]),
                # Short names in the table, matching the add form's
                # selectors, so the same pump is not called "pwm0" in
                # one place and "R0:pwm0" a line below.
                "sensor_name": sensor,
                "actuator_name": short_name(row["actuator"]),
            },
        )
    return labelled


async def pairing_panel(reactor: str) -> None:
    """The pairing table and the form that adds to it.

    Async because the initial row list depends on a network read that
    cannot run inside a plain refreshable render. Awaiting it here means
    the first paint already carries the published table, rather than
    depending on a deferred timer that can outlive the request.
    """
    rows = await label_channels(reactor, await read_pairings(reactor))

    container = ui.column().classes("w-full").style("gap: 0.5rem")

    async def reconcile() -> None:
        """Re-read the table and rebuild the panel."""
        fresh = await label_channels(reactor, await read_pairings(reactor))
        container.clear()
        with container:
            _render(reactor, fresh, reconcile)

    with container:
        _render(reactor, rows, reconcile)


def _render(reactor: str, rows: list[dict], reconcile) -> None:
    """Draw the table and the add form."""
    if STATE.book is None:
        ui.label("Not connected")
        return

    if rows:
        with ui.element("div").style("overflow-x: auto; width: 100%"):
            table = ui.table(
                columns=[
                    {
                        "name": "sensor_name",
                        "label": "Sensor",
                        "field": "sensor_name",
                    },
                    {
                        "name": "channel_name",
                        "label": "Channel",
                        "field": "channel_name",
                    },
                    {
                        "name": "actuator_name",
                        "label": "Actuator",
                        "field": "actuator_name",
                    },
                ],
                rows=rows,
            ).classes("w-full")
            table.props("dense flat")
    else:
        ui.label("Nothing is paired on this reactor").classes(
            "text-gray-500 text-sm",
        )

    _add_form(reactor, rows, reconcile)
    _unpair_buttons(reactor, rows, reconcile)


def _unpair_buttons(reactor: str, rows: list[dict], reconcile) -> None:
    """An Unpair button per current pairing."""
    if not rows:
        return
    with ui.row().classes("flex-wrap").style("gap: 0.5rem"):
        for row in rows:
            label = f"Unpair {short_name(row['actuator'])}"
            disable_when_read_only(
                ui.button(
                    label,
                    on_click=lambda r=row: _unpair(reactor, r, reconcile),
                ).props("outline size=sm color=warning"),
            )


def _add_form(reactor: str, rows: list[dict], reconcile) -> None:
    """Sensor, channel and actuator selectors, plus the Pair button."""
    book = STATE.book
    sensors = book.sensors(reactor)
    already = paired_actuators(rows)
    free = [name for name in sorted(book.actuators(reactor)) if name
            not in already]

    if not sensors:
        return

    with ui.row().classes("items-end flex-wrap").style("gap: 0.5rem"):
        sensor_select = ui.select(
            sorted(sensors),
            value=next(iter(sorted(sensors)), None),
            label="Sensor",
        ).style("min-width: 9rem")

        channel_select = ui.select([], label="Channel").style(
            "min-width: 9rem",
        )

        actuator_select = ui.select(
            free,
            value=free[0] if free else None,
            label="Actuator",
        ).style("min-width: 9rem")

        def refresh_channels() -> None:
            """Offer the channels of the selected sensor."""
            options = [
                ref.channel
                for ref in sensors.get(sensor_select.value, [])
            ]
            channel_select.options = options
            channel_select.value = options[0] if options else None
            channel_select.update()

        sensor_select.on_value_change(lambda _: refresh_channels())
        refresh_channels()

        async def pair() -> None:
            await _pair(
                reactor,
                sensor_select.value,
                channel_select.value,
                actuator_select.value,
                reconcile,
            )

        button = ui.button("Pair", on_click=pair).props("color=primary")
        if not free:
            button.disable()
            button.tooltip("Every actuator on this reactor is already paired")
        else:
            disable_when_read_only(button)


async def _pair(
    reactor: str,
    sensor: str | None,
    channel: str | None,
    actuator: str | None,
    reconcile,
) -> None:
    """Validate and call set_pairing."""
    _logger.info(
        "Operator pairing %s:%s -> %s on %s",
        sensor,
        channel,
        actuator,
        reactor,
    )
    if not sensor or not channel or not actuator:
        ui.notify("Pick a sensor, a channel and an actuator", type="warning")
        return

    index = await channel_index(reactor, sensor, channel)
    if index is None:
        ui.notify(
            f"Could not read the channel index of {sensor}:{channel}",
            type="negative",
        )
        return

    try:
        ok = await STATE.call(
            reactor,
            None,
            "set_pairing",
            device_id(reactor, sensor),
            device_id(reactor, actuator),
            index,
        )
    except (LookupError, OSError) as err:
        ui.notify(f"Pairing failed: {err}", type="negative")
        return

    if not ok:
        # Everything the server checks was checked above, so this is
        # genuinely unexpected rather than the normal refusal path.
        ui.notify(
            "The server refused the pairing; check record.log",
            type="negative",
        )
    else:
        ui.notify(f"{actuator} now follows {sensor}:{channel}", type="positive")
    await reconcile()


async def _unpair(reactor: str, row: dict, reconcile) -> None:
    """Call unpair for one table row."""
    _logger.info("Operator unpairing %s on %s", row["actuator"], reactor)
    try:
        ok = await STATE.call(
            reactor,
            None,
            "unpair",
            row["sensor"],
            row["actuator"],
            row["channel"],
        )
    except (LookupError, OSError) as err:
        ui.notify(f"Unpairing failed: {err}", type="negative")
        return

    if not ok:
        ui.notify(
            "The server refused the unpair; check record.log",
            type="negative",
        )
    else:
        ui.notify(f"{row['actuator']} unpaired", type="positive")
    await reconcile()
