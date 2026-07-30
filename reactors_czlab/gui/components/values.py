"""Live sensor and actuator panels.

Every decision worth testing already lives in ``gui/format.py`` and
``gui/address.py``; what is here is assembly. The panels are
``ui.refreshable`` and are driven by a ``ui.timer`` on the page, reading
``STATE`` - which reads ``OpcClient.variables``, the dict the
subscription callback maintains.
"""

from __future__ import annotations

from datetime import datetime

from nicegui import ui

from reactors_czlab.gui.format import is_stale, render_value
from reactors_czlab.gui.state import STATE

#: Actuator channels shown on the card, in order, with their labels.
ACTUATOR_CHANNELS = (
    ("curr_value", "Output", ""),
    ("total_volume", "Delivered", "mL"),
)

#: Calibration channels shown under the fitted line.
CALIBRATION_CHANNELS = (
    ("cal_a", "a"),
    ("cal_b", "b"),
    ("cal_r2", "r2"),
)


def _value_chip(label: str, text: str, stale: bool) -> None:
    """One labelled reading, greyed out when it is stale."""
    with ui.column().classes("gap-0 items-start"):
        ui.label(label).classes("text-xs text-gray-500")
        chip = ui.label(text).classes("text-lg font-mono")
        if stale:
            chip.classes("text-gray-400 line-through")


@ui.refreshable
def sensor_panel(reactor: str) -> None:
    """One row per sensor, one chip per channel."""
    if STATE.book is None:
        ui.label("Not connected")
        return

    now = datetime.now()  # noqa: DTZ005 - client stores naive timestamps
    sensors = STATE.book.sensors(reactor)
    if not sensors:
        ui.label("No sensors on this reactor")
        return

    for name, refs in sorted(sensors.items()):
        with ui.card().classes("w-full"):
            ui.label(name).classes("text-sm font-semibold")
            with ui.row().classes("gap-6 flex-wrap"):
                for ref in refs:
                    value, stamp = STATE.reading(reactor, name, ref.channel)
                    _value_chip(
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

    now = datetime.now()  # noqa: DTZ005 - client stores naive timestamps
    actuators = STATE.book.actuators(reactor)
    if not actuators:
        ui.label("No actuators on this reactor")
        return

    for name in sorted(actuators):
        with ui.card().classes("w-full"):
            ui.label(name).classes("text-sm font-semibold")
            with ui.row().classes("gap-6 flex-wrap"):
                for channel, label, units in ACTUATOR_CHANNELS:
                    value, stamp = STATE.reading(reactor, name, channel)
                    _value_chip(
                        label,
                        render_value(value, units=units),
                        is_stale(stamp, now, STATE.period),
                    )
            with ui.row().classes("gap-4 flex-wrap"):
                for channel, label in CALIBRATION_CHANNELS:
                    value, _ = STATE.reading(reactor, name, channel)
                    ui.label(
                        f"{label} {render_value(value, digits=4)}",
                    ).classes("text-xs text-gray-500 font-mono")
