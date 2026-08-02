"""Live sensor and actuator panels.

Every decision worth testing lives in ``gui/format.py`` and
``gui/address.py``; what is here is assembly. The panels are
``ui.refreshable`` and driven by a timer on the page, reading ``STATE``
- which reads ``OpcClient.variables``, the dict the subscription
callback already maintains.
"""

from __future__ import annotations

from datetime import datetime

from nicegui import ui

from reactors_czlab.gui.components.control_form import open_control_dialog
from reactors_czlab.gui.format import is_stale, render_value
from reactors_czlab.gui.state import STATE

#: Actuator state channels shown on a card, in order, with their labels
#: and units.
ACTUATOR_CHANNELS = (
    ("curr_value", "Output", ""),
    ("total_volume", "Delivered", "mL"),
)

#: The fitted pump line, shown small under the state channels.
CALIBRATION_CHANNELS = (("cal_a", "a"), ("cal_b", "b"), ("cal_r2", "r2"))


def value_chip(label: str, text: str, stale: bool) -> None:
    """One labelled reading, greyed out when it has stopped updating."""
    with ui.column().style("gap: 0"):
        ui.label(label).classes("text-xs text-gray-500")
        chip = ui.label(text).classes("text-lg font-mono")
        if stale:
            chip.classes("text-gray-400 line-through")
            chip.tooltip("No update for several sampling periods")


@ui.refreshable
def sensor_panel(reactor: str) -> None:
    """One card per sensor, one chip per channel."""
    if STATE.book is None:
        ui.label("Not connected")
        return

    now = datetime.now()  # noqa: DTZ005 - the client stores naive stamps
    sensors = STATE.book.sensors(reactor)
    if not sensors:
        ui.label("No sensors on this reactor").classes("text-gray-500")
        return

    for name, refs in sorted(sensors.items()):
        with ui.card().classes("w-full"):
            ui.label(name).classes("text-sm font-semibold font-mono")
            with ui.row().classes("flex-wrap").style("gap: 1.5rem"):
                for ref in refs:
                    value, stamp = STATE.reading(reactor, name, ref.channel)
                    value_chip(
                        ref.channel,
                        render_value(value, units=ref.channel),
                        is_stale(stamp, now, STATE.period),
                    )


@ui.refreshable
def actuator_panel(reactor: str) -> None:
    """One card per actuator, with its output, totals and fitted line."""
    if STATE.book is None:
        ui.label("Not connected")
        return

    now = datetime.now()  # noqa: DTZ005 - the client stores naive stamps
    actuators = STATE.book.actuators(reactor)
    if not actuators:
        ui.label("No actuators on this reactor").classes("text-gray-500")
        return

    for name in sorted(actuators):
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(name).classes("text-sm font-semibold font-mono")
                ui.button(
                    "Configure",
                    on_click=lambda r=reactor, n=name: open_control_dialog(
                        r,
                        n,
                    ),
                ).props("outline size=sm")

            with ui.row().classes("flex-wrap").style("gap: 1.5rem"):
                for channel, label, units in ACTUATOR_CHANNELS:
                    value, stamp = STATE.reading(reactor, name, channel)
                    value_chip(
                        label,
                        render_value(value, units=units),
                        is_stale(stamp, now, STATE.period),
                    )

            with ui.row().classes("flex-wrap").style("gap: 1rem"):
                for channel, label in CALIBRATION_CHANNELS:
                    value, _ = STATE.reading(reactor, name, channel)
                    ui.label(
                        f"{label} {render_value(value, digits=4)}",
                    ).classes("text-xs text-gray-500 font-mono")
