"""The frame every page sits in: header, status badges, navigation.

Assembly only. The one thing here that is a decision rather than layout
is the recording toggle, and what it does lives in ``AppState``.

Note on spacing: gaps are inline styles, never Tailwind ``gap-*``
classes. NiceGUI issue #2171 makes those render with excessive vertical
spacing on Ubuntu, which is both the development machine and the Pi.
"""

from __future__ import annotations

import logging

from nicegui import ui

from reactors_czlab.gui.state import STATE
from reactors_czlab.sql.operations import SqlError

_logger = logging.getLogger("gui")

#: How often the header re-reads the connection and recording state.
STATUS_SECONDS = 2.0


def disable_when_read_only(control: ui.element) -> ui.element:
    """Disable an OPC command control while the connection cannot write."""
    control.bind_enabled_from(STATE, "writable")
    control.tooltip("Requires a writable OPC connection")
    return control


@ui.refreshable
def status_badges() -> None:
    """Connection, database and recording state, at a glance."""
    if STATE.connected:
        ui.badge("connected", color="green")
    elif STATE.reconnecting:
        # Distinct from disconnected on purpose: an operator answers
        # "disconnected" by hitting Retry, which is the one thing that
        # must not happen while asyncua is recovering on its own.
        ui.badge("reconnecting", color="orange")
    else:
        ui.badge("disconnected", color="red")

    if not STATE.database_available:
        ui.badge("no database", color="orange").tooltip(STATE.database_reason)
    else:
        reactors = STATE.book.reactors if STATE.book is not None else []
        recording = sum(STATE.is_recording(reactor) for reactor in reactors)
        ui.badge(
            f"{recording} of {len(reactors)} recording",
            color="blue" if recording else "grey",
        )


@ui.refreshable
def reactor_recording_toggle(reactor: str) -> None:
    """Start or pause archiving for one reactor."""

    async def start() -> None:
        _logger.info("Operator started recording %s", reactor)
        try:
            await STATE.start_recording(reactor)
        except SqlError as err:
            ui.notify(str(err), type="negative")
            return
        ui.notify(f"Recording started for {reactor}", type="positive")
        reactor_recording_toggle.refresh()
        status_badges.refresh()

    async def stop() -> None:
        _logger.info("Operator stopped recording %s", reactor)
        try:
            await STATE.stop_recording(reactor)
        except SqlError as err:
            ui.notify(str(err), type="negative")
            return
        ui.notify(f"Recording stopped for {reactor}", type="warning")
        reactor_recording_toggle.refresh()
        status_badges.refresh()

    recording = STATE.is_recording(reactor)
    ui.badge(
        "recording" if recording else "paused",
        color="blue" if recording else "grey",
    )
    if recording:
        disable_when_read_only(
            ui.button(
                "Stop recording",
                on_click=stop,
                color="warning",
            ).props("size=sm"),
        )
    else:
        button = ui.button("Record", on_click=start).props(
            "outline size=sm",
        )
        if not STATE.writable:
            disable_when_read_only(button)
        elif not STATE.database_available:
            button.disable()
            button.tooltip(STATE.database_reason)


def header(reactor: str | None = None) -> None:
    """The bar every page carries."""
    with ui.header().classes("items-center justify-between").style(
        "padding: 0.5rem 1rem",
    ):
        with ui.row().classes("items-center").style("gap: 1rem"):
            ui.link("Bioreactors", "/").classes(
                "text-lg font-semibold text-white no-underline",
            )
            if reactor is not None:
                ui.label(reactor).classes("text-white font-mono")
            ui.link("Experiments", "/experiments").classes(
                "text-white no-underline text-sm",
            )

        with ui.row().classes("items-center").style("gap: 0.75rem"):
            status_badges()

    ui.timer(STATUS_SECONDS, _refresh_status)


def _refresh_status() -> None:
    """Re-read the state the header shows."""
    status_badges.refresh()


def reactor_tabs(reactor: str, active: str) -> None:
    """Links to the four screens that exist per reactor."""
    with ui.row().classes("items-center").style("gap: 0.5rem"):
        for label, suffix in (
            ("Dashboard", ""),
            ("Plots", "/plots"),
            ("Sensor calibration", "/calibration/sensors"),
            ("Pump calibration", "/calibration/pumps"),
        ):
            target = f"/reactor/{reactor}{suffix}"
            button = ui.button(
                label,
                on_click=lambda t=target: ui.navigate.to(t),
            ).props("flat size=sm")
            if label == active:
                button.props("color=primary")
            else:
                button.props("color=grey-7")


def not_connected_notice() -> None:
    """What a page shows instead of its content when there is no link."""
    with ui.column().classes("w-full items-start").style("gap: 0.5rem"):
        if STATE.reconnecting:
            ui.label("Connection lost - reconnecting...").classes(
                "text-orange-600",
            )
            ui.label(
                "asyncua is retrying on its own; no action needed.",
            ).classes("text-sm text-gray-500")
            return

        ui.label(
            STATE.connection_error or f"Connecting to {STATE.endpoint}...",
        ).classes("text-red-600")
        ui.button("Retry", on_click=STATE.connect).props("outline")
