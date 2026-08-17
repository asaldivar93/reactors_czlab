"""Live sensor and actuator panels.

Every decision worth testing lives in ``gui/format.py`` and
``gui/address.py``; what is here is assembly. The readings are read from
``STATE`` - which reads ``OpcClient.variables``, the dict the
subscription callback already maintains - on a timer the page owns.

**Only the readings are refreshable.** The cards and their buttons are
built once. Putting them inside the refreshable rebuilt every control
on the page once a second: a Configure button an operator was reaching
for was destroyed and replaced underneath the pointer, so a click that
landed on the old element did nothing. Measured in a browser - the
button element was gone from the document within 2.5 s.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from nicegui import ui

from reactors_czlab.gui.components.control_form import open_control_dialog
from reactors_czlab.gui.components.shell import disable_when_read_only
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


def value_chip(
    label: str,
    text: str,
    stale: bool,
    description: str = "",
) -> None:
    """One labelled reading, greyed out when it has stopped updating."""
    with ui.column().style("gap: 0"):
        label_element = ui.label(label).classes("text-xs text-gray-500")
        if description:
            label_element.tooltip(description)
        chip = ui.label(text).classes("text-lg font-mono")
        if stale:
            chip.classes("text-gray-400 line-through")
            chip.tooltip("No update for several sampling periods")


def sensor_panel(reactor: str) -> None:
    """One card per sensor. Built once; only the readings refresh."""
    if STATE.book is None:
        ui.label("Not connected")
        return

    sensors = STATE.book.sensors(reactor)
    if not sensors:
        ui.label("No sensors on this reactor").classes("text-gray-500")
        return

    for name in sorted(sensors):
        with ui.card().classes("w-full"):
            ui.label(name).classes("text-sm font-semibold font-mono")
            sensor_readings(reactor, name)


@ui.refreshable
def sensor_readings(reactor: str, name: str) -> None:
    """One chip per channel of one sensor."""
    if STATE.book is None:
        return
    now = datetime.now()  # noqa: DTZ005 - the client stores naive stamps
    with ui.row().classes("flex-wrap").style("gap: 1.5rem"):
        for ref in STATE.book.sensors(reactor).get(name, []):
            value, stamp = STATE.reading(reactor, name, ref.channel)
            # The chip's label is already the channel, and for the
            # biomass sensor the channel is a wavelength rather than a
            # unit - repeating it beside the number read "34.950 445".
            value_chip(
                ref.channel,
                render_value(value),
                is_stale(stamp, now, STATE.period),
                ref.description,
            )


def control_summary(config: dict | None) -> str:
    """Render the method, unit and current demand from server read-back."""
    if config is None:
        return "Control configuration unavailable"
    units = {
        "duty": "counts",
        "flow": "mL/min",
        "volume": "mL",
    }
    unit = str(config["output_unit"])
    demand = render_value(config.get("demand"), units=units.get(unit, unit))
    return f"{config['method']} · {unit} · demand {demand}"


def pairing_summary(actuator: str, rows: list[dict]) -> str:
    """Render what an actuator follows, or that it is unpaired."""
    for row in rows:
        full_actuator = str(row.get("actuator", ""))
        actuator_name = (
            row.get("actuator_name")
            or full_actuator.partition(":")[2]
            or full_actuator
        )
        if actuator_name != actuator:
            continue
        full_sensor = str(row.get("sensor", ""))
        sensor = (
            row.get("sensor_name")
            or full_sensor.partition(":")[2]
            or full_sensor
        )
        channel = row.get("channel_name", row.get("channel", ""))
        return f"Paired to {sensor}:{channel}"
    return "Unpaired"


def actuator_panel(
    reactor: str,
    configs: dict[str, dict | None],
    pairings: list[dict],
    calibrating: set[str],
) -> Callable[[list[dict]], None]:
    """One card per actuator. Built once; only the readings refresh."""
    if STATE.book is None:
        ui.label("Not connected")
        return

    actuators = STATE.book.actuators(reactor)
    if not actuators:
        ui.label("No actuators on this reactor").classes("text-gray-500")
        return lambda rows: None

    pairing_labels: dict[str, ui.element] = {}
    for name in sorted(actuators):
        pairing_labels[name] = _actuator_card(
            reactor,
            name,
            configs.get(name),
            pairings,
            name in calibrating,
        )

    def update_pairings(rows: list[dict]) -> None:
        """Update pairing labels without rebuilding cards or buttons."""
        for actuator, label in pairing_labels.items():
            label.set_text(pairing_summary(actuator, rows))

    return update_pairings


def _actuator_card(
    reactor: str,
    name: str,
    config: dict | None,
    pairings: list[dict],
    calibrating: bool,
) -> ui.element:
    """Build one actuator card and return its pairing label."""
    with ui.card().classes("w-full"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(name).classes("text-sm font-semibold font-mono")
            config_label = ui.label(control_summary(config)).classes(
                "text-xs text-gray-600",
            )

            def update_config(latest: dict) -> None:
                config_label.set_text(control_summary(latest))

            configure = ui.button(
                "Configure",
                on_click=lambda: open_control_dialog(
                    reactor,
                    name,
                    update_config,
                ),
            ).props("outline size=sm")
            if calibrating:
                configure.disable()
                configure.tooltip("Calibration currently owns this actuator")
            else:
                disable_when_read_only(configure)
        pairing_label = ui.label(pairing_summary(name, pairings)).classes(
            "text-xs text-gray-500",
        )
        actuator_readings(reactor, name)
    return pairing_label


@ui.refreshable
def actuator_readings(reactor: str, name: str) -> None:
    """One actuator's output, totals and fitted line."""
    now = datetime.now()  # noqa: DTZ005 - the client stores naive stamps

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
