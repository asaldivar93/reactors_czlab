"""Pair and unpair actuators from the dashboard.

``set_pairing`` and ``unpair`` answer with a bare bool and log the reason
server-side, so this panel pre-checks everything ``_validate_pair`` and
``set_pairing`` check - reactor membership, and that the actuator is not
already paired. A False from the server is therefore genuinely
unexpected and is reported as such rather than being the normal failure
path.

A successful pair or unpair updates the panel's own row list in place
rather than re-reading ``read_pairings`` a second time: the published
``R{n}:pairings`` variable only catches up to a call this panel itself
just made after the server's own write completes, so a second read
immediately afterwards is not guaranteed to see it yet. ``read_pairings``
is still what populates the panel on first load - and is the only thing
that can recover the picture after a page reload or pick up a change
made by another OPC client - it just is not re-consulted for a mutation
this panel caused itself and already knows the outcome of.
"""

from __future__ import annotations

import json
import logging

from nicegui import ui

from reactors_czlab.gui.state import STATE

_logger = logging.getLogger("gui")

#: reactor id -> the node id of its pairings variable, found by browsing
#: once. Node ids are stable for the life of a server process, and the
#: address book cannot hold these (see read_pairings).
_PAIRINGS_NODES: dict[str, str] = {}


async def read_pairings(reactor: str) -> list[dict]:
    """The reactor's published pairing table.

    Returns
    -------
    list of dict
        ``{"sensor", "actuator", "channel"}`` rows, or an empty list if
        the variable is missing or unreadable.

    """
    if STATE.client is None or STATE.book is None:
        return []
    # The pairings variable hangs off the reactor node, above
    # R{n}:sensors / R{n}:actuators, so match_tree never indexes it and
    # the address book cannot resolve it. Browse for it once and cache.
    #
    # The whole lookup - not just the final read - is one try/except:
    # STATE.client is not always the real OpcClient. Several page tests
    # stub it with a bare object that only carries what *they* need
    # (e.g. ``.recording``), so ``.client`` (the underlying asyncua
    # client) can be missing entirely; that must degrade the same way a
    # genuine read failure does, not raise out of a page render.
    try:
        nodeid = _PAIRINGS_NODES.get(reactor)
        if nodeid is None:
            nodeid = await _find_pairings_node(reactor)
            if nodeid is None:
                return []
            _PAIRINGS_NODES[reactor] = nodeid
        raw = await STATE.client.client.get_node(nodeid).get_value()
        return json.loads(raw)
    except Exception:  # a stale picture, not a crash
        _logger.warning("Could not read %s pairings", reactor, exc_info=True)
        return []


async def _find_pairings_node(reactor: str) -> str | None:
    """Browse the objects folder for ``R{n}:pairings``."""
    if STATE.client is None:
        return None
    objects = STATE.client.client.nodes.objects
    for node in await objects.get_children():
        name = (await node.read_browse_name()).Name
        if name != reactor:
            continue
        for child in await node.get_children():
            child_name = (await child.read_browse_name()).Name
            if child_name == f"{reactor}:pairings":
                return child.nodeid.to_string()
    return None


async def read_channel_indices(reactor: str, sensor: str) -> dict[str, int]:
    """Channel name -> the index ``set_pairing`` expects.

    Read from the ``ChannelIndex`` property on each channel variable,
    not from browse order: asyncua does not guarantee ``get_children()``
    returns children in the order they were added.
    """
    if STATE.client is None or STATE.book is None:
        return {}
    indices: dict[str, int] = {}
    for ref in STATE.book.sensors(reactor).get(sensor, ()):
        node = STATE.client.client.get_node(ref.nodeid)
        try:
            for prop in await node.get_properties():
                name = (await prop.read_browse_name()).Name
                if name == "ChannelIndex":
                    indices[ref.channel] = int(await prop.get_value())
        except Exception:  # reported as a missing channel
            _logger.warning(
                "No channel index on %s:%s",
                sensor,
                ref.channel,
                exc_info=True,
            )
    return indices


@ui.refreshable
async def pairing_panel(reactor: str) -> None:
    """Current pairings, with an add form and per-row unpair.

    Async, unlike ``actuator_panel``/``sensor_panel``: those read
    already-cached values synchronously, but the initial row list here
    needs a network round trip (``read_pairings``). ``@ui.refreshable``
    awaits a coroutine function itself (see
    ``nicegui.functions.refreshable.RefreshableTarget.run``), so the
    first render can carry the published table directly, instead of
    coming back empty and depending on a deferred ``ui.timer`` to fill
    it in later. A real timer here would outlive the render that
    created it - in the test harness in particular, a client can look
    connected for longer than the harness's own teardown window, so an
    orphaned timer for one test's page can fire during a *later* test,
    against whatever that later test's ``STATE`` happens to be stubbed
    to.
    """
    if STATE.book is None:
        ui.label("Not connected")
        return

    rows_container = ui.column().classes("w-full gap-1")
    sensors = sorted(STATE.book.sensors(reactor))
    actuators = sorted(STATE.book.actuators(reactor))

    sensor_select = ui.select(sensors, label="Sensor").classes("w-48")
    channel_select = ui.select([], label="Channel").classes("w-40")
    actuator_select = ui.select([], label="Actuator").classes("w-40")

    #: ``pairings`` mirrors the last-known published table: fetched once
    #: by ``reload`` and then updated in place by a successful pair or
    #: unpair, so the row that call just changed does not wait on a
    #: second round trip to appear or disappear. The published variable
    #: remains the source of truth across a page reload or a restart -
    #: see ``read_pairings`` - this is only the in-session picture.
    state: dict[str, object] = {"pairings": [], "indices": {}, "paired": set()}

    def render_rows() -> None:
        """Rebuild the row list and actuator options from local state."""
        pairings = state["pairings"]
        # Rows carry the full "{reactor}:{name}" id (what unpair needs),
        # but the Actuator select's own options are bare names (what
        # STATE.book.actuators returns) - strip the prefix so an
        # already-paired actuator is actually excluded instead of never
        # matching.
        prefix = f"{reactor}:"
        state["paired"] = {
            row["actuator"].removeprefix(prefix) for row in pairings
        }
        rows_container.clear()
        with rows_container:
            if not pairings:
                ui.label("Nothing paired").classes("text-sm text-gray-500")
            for row in pairings:
                with ui.row().classes("items-center gap-3"):
                    ui.label(
                        f"{row['sensor']} ch{row['channel']} "
                        f"-> {row['actuator']}",
                    ).classes("font-mono text-sm")
                    ui.button(
                        "Unpair",
                        on_click=lambda r=row: do_unpair(r),
                    ).props("flat dense size=sm color=negative")
        # Only actuators that are free can be paired.
        actuator_select.set_options(
            [a for a in actuators if a not in state["paired"]],
        )

    async def reload() -> None:
        """Re-read the published table and rebuild the row list."""
        state["pairings"] = await read_pairings(reactor)
        render_rows()

    async def on_sensor(_: object) -> None:
        """Load the selected sensor's channel indices."""
        if sensor_select.value is None:
            return
        indices = await read_channel_indices(reactor, sensor_select.value)
        state["indices"] = indices
        channel_select.set_options(sorted(indices))

    async def do_pair() -> None:
        """Pair after checking everything the server would check."""
        sensor = sensor_select.value
        channel = channel_select.value
        actuator = actuator_select.value
        if not (sensor and channel is not None and actuator):
            ui.notify("Choose a sensor, a channel and an actuator",
                      type="warning")
            return
        if actuator in state["paired"]:
            ui.notify(f"{actuator} is already paired", type="warning")
            return

        index = state["indices"].get(channel)
        if index is None:
            ui.notify(f"No channel index for {channel}", type="negative")
            return

        sensor_id = f"{reactor}:{sensor}"
        actuator_id = f"{reactor}:{actuator}"
        ok = await STATE.call(
            reactor,
            None,
            "set_pairing",
            sensor_id,
            actuator_id,
            index,
        )
        if ok:
            ui.notify(f"{actuator} follows {sensor}:{channel}",
                      type="positive")
            # The server has already applied the pairing; render the
            # new row locally instead of a second round trip through
            # read_pairings, which is only current once the published
            # table catches up.
            state["pairings"] = [
                *state["pairings"],
                {
                    "sensor": sensor_id,
                    "actuator": actuator_id,
                    "channel": index,
                },
            ]
            render_rows()
        else:
            ui.notify(
                "The server refused the pairing; check record.log",
                type="negative",
            )

    async def do_unpair(row: dict) -> None:
        """Hand an actuator back to the unpaired loop."""
        ok = await STATE.call(
            reactor,
            None,
            "unpair",
            row["sensor"],
            row["actuator"],
            row["channel"],
        )
        if ok:
            state["pairings"] = [
                r for r in state["pairings"] if r != row
            ]
            render_rows()
        else:
            ui.notify(
                "The server refused the unpair; check record.log",
                type="negative",
            )

    sensor_select.on_value_change(on_sensor)
    with ui.row().classes("items-end gap-2"):
        ui.button("Pair", on_click=do_pair)

    await reload()
