"""Reactor dashboard routes.

Assembly only: no decisions are made here. Anything that had to be
decided is in gui/address.py, gui/format.py or gui/control.py, where a
test can reach it.
"""

from __future__ import annotations

from nicegui import ui

from reactors_czlab.gui.components.pairing import pairing_panel
from reactors_czlab.gui.components.values import (
    actuator_panel,
    sensor_panel,
)
from reactors_czlab.gui.state import STATE

#: How often the panels re-read the in-memory values, in seconds. The
#: server publishes on its sampling period; this only has to be fast
#: enough to feel live.
REFRESH_SECONDS = 1.0


def header(reactor: str | None = None) -> None:
    """The bar every page carries: connection, recording, reactor."""
    with ui.header().classes("items-center justify-between"):
        with ui.row().classes("items-center gap-4"):
            ui.link("Bioreactors", "/").classes(
                "text-lg font-semibold text-white no-underline",
            )
            if reactor is not None:
                ui.label(reactor).classes("text-white")
        with ui.row().classes("items-center gap-3"):
            if STATE.connected:
                ui.badge("connected", color="green")
            elif STATE.reconnecting:
                ui.badge("reconnecting", color="orange")
            else:
                ui.badge("disconnected", color="red")
            if not STATE.database_available:
                ui.badge("no database", color="orange")
            elif STATE.recording:
                ui.badge("recording", color="blue")
            else:
                ui.badge("not recording", color="grey")


@ui.page("/")
def index() -> None:
    """List the reactors, or say why there are none."""
    header()
    with ui.column().classes("w-full p-4 gap-4"):
        if not STATE.connected:
            ui.label(
                STATE.connection_error
                or f"Connecting to {STATE.endpoint}...",
            ).classes("text-red-600")
            ui.button("Retry", on_click=STATE.connect)
            return

        ui.label("Reactors").classes("text-xl font-semibold")
        with ui.row().classes("gap-4 flex-wrap"):
            for reactor in STATE.book.reactors:
                with ui.card().classes("w-64"):
                    ui.label(reactor).classes("text-lg font-semibold")
                    ui.label(
                        f"{len(STATE.book.sensors(reactor))} sensors, "
                        f"{len(STATE.book.actuators(reactor))} actuators",
                    ).classes("text-sm text-gray-500")
                    ui.button(
                        "Open",
                        on_click=lambda r=reactor: ui.navigate.to(
                            f"/reactor/{r}",
                        ),
                    )


@ui.page("/reactor/{reactor}")
async def reactor_page(reactor: str) -> None:
    """Live values for one reactor."""
    header(reactor)
    with ui.column().classes("w-full p-4 gap-4"):
        if not STATE.connected:
            ui.label("Not connected").classes("text-red-600")
            return

        ui.label("Sensors").classes("text-lg font-semibold")
        sensor_panel(reactor)

        ui.label("Actuators").classes("text-lg font-semibold")
        actuator_panel(reactor)

        ui.label("Pairings").classes("text-lg font-semibold")
        # pairing_panel is async - its initial row list depends on a
        # network read (read_pairings) that cannot run synchronously
        # inside a plain @ui.refreshable render. Awaiting it here means
        # the first render already carries the published table, rather
        # than depending on a deferred ui.timer that can outlive both
        # this request and, in tests, the client that created it.
        await pairing_panel(reactor)

    def refresh() -> None:
        """Re-read the in-memory values."""
        sensor_panel.refresh()
        actuator_panel.refresh()

    ui.timer(REFRESH_SECONDS, refresh)
