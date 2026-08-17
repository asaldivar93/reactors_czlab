"""Reactor list and per-reactor dashboard.

Assembly only: anything that had to be decided lives in
``gui/address.py``, ``gui/format.py`` or ``gui/control.py``, where a
test can reach it without a browser.
"""

from __future__ import annotations

import asyncio

from nicegui import ui

from reactors_czlab.gui.components.control_form import read_control_config
from reactors_czlab.gui.components.pairing import (
    label_channels,
    pairing_panel,
    read_pairings,
)
from reactors_czlab.gui.components.shell import (
    header,
    not_connected_notice,
    reactor_recording_toggle,
    reactor_tabs,
)
from reactors_czlab.gui.components.values import (
    actuator_panel,
    actuator_readings,
    sensor_panel,
    sensor_readings,
)
from reactors_czlab.gui.state import STATE

#: How often the panels re-read the in-memory values. The server
#: publishes on its sampling period; this only has to feel live.
REFRESH_SECONDS = 1.0


@ui.page("/")
def index() -> None:
    """List the reactors, or say why there are none."""
    header()
    with ui.column().classes("w-full").style("padding: 1rem; gap: 1rem"):
        if not STATE.connected:
            not_connected_notice()
            return

        ui.label("Reactors").classes("text-xl font-semibold")
        with ui.row().classes("flex-wrap").style("gap: 1rem"):
            for reactor in STATE.book.reactors:
                _reactor_card(reactor)


def _reactor_card(reactor: str) -> None:
    """One card in the reactor list."""
    with ui.card().style("width: 16rem"):
        ui.label(reactor).classes("text-lg font-semibold font-mono")
        ui.label(
            f"{len(STATE.book.sensors(reactor))} sensors, "
            f"{len(STATE.book.actuators(reactor))} actuators",
        ).classes("text-sm text-gray-500")
        with ui.row().style("gap: 0.5rem"):
            ui.button(
                "Open",
                on_click=lambda r=reactor: ui.navigate.to(f"/reactor/{r}"),
            ).props("size=sm")
            ui.button(
                "Plots",
                on_click=lambda r=reactor: ui.navigate.to(
                    f"/reactor/{r}/plots",
                ),
            ).props("outline size=sm")


@ui.page("/reactor/{reactor}")
async def reactor_page(reactor: str) -> None:
    """Live values, control configuration and pairing for one reactor."""
    header(reactor)
    with ui.column().classes("w-full").style("padding: 1rem; gap: 1rem"):
        reactor_tabs(reactor, "Dashboard")

        if not STATE.connected:
            not_connected_notice()
            return

        actuators = sorted(STATE.book.actuators(reactor))
        configs = dict(
            zip(
                actuators,
                await asyncio.gather(
                    *(read_control_config(reactor, name) for name in actuators),
                ),
                strict=True,
            ),
        )
        pairings = await label_channels(
            reactor,
            await read_pairings(reactor),
        )

        with ui.row().classes("items-center").style("gap: 0.75rem"):
            active = (
                STATE.client.experiment_tags.get(reactor)
                if STATE.client is not None
                else None
            )
            ui.badge(
                f"Experiment: {active}" if active else "No active experiment",
                color="blue" if active else "grey",
            )
            reactor_recording_toggle(reactor)

        ui.label("Sensors").classes("text-lg font-semibold")
        sensor_panel(reactor)

        ui.label("Actuators").classes("text-lg font-semibold")
        update_pairings = actuator_panel(reactor, configs, pairings)

        ui.label("Pairings").classes("text-lg font-semibold")
        # Awaited here rather than deferred to a timer: its first row
        # list depends on a network read, and awaiting means the first
        # paint already carries the published table.
        await pairing_panel(reactor, pairings, update_pairings)

    def refresh() -> None:
        """Re-read the in-memory values.

        Only the readings, not the cards that hold them: rebuilding the
        cards would destroy the Configure buttons once a second,
        underneath whatever the operator was reaching for.
        """
        sensor_readings.refresh()
        actuator_readings.refresh()

    ui.timer(REFRESH_SECONDS, refresh)
