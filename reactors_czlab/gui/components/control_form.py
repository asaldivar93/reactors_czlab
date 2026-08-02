"""The dialog that reconfigures an actuator's controller.

Thin: the write order lives in ``gui/control.py`` and the unit check in
``unit_rejection_reason``, both pure and both tested. What is here is
the form and the reporting.

Two things about this dialog are not cosmetic:

- The fields shown follow the selected method, because the server reads
  only the fields that method needs.
- A flow or volume unit is checked against the pump's calibration
  *before* anything is written. ``check_unit`` makes the same judgement
  server-side but only logs its refusal - ``set_control_config`` keeps
  the running controller and tells the client nothing - so without this
  the operator sees a configuration that appears to have been accepted
  and has not been.
"""

from __future__ import annotations

import json
import logging

from nicegui import ui

from reactors_czlab.core.data import ControlMethod, OutputUnit
from reactors_czlab.gui.control import (
    build_write_plan,
    fields_for,
    unit_rejection_reason,
)
from reactors_czlab.gui.state import STATE

_logger = logging.getLogger("gui")

#: Label and kind for every control-config channel a form can show.
#: ``bool`` fields are switches; everything else is a number.
FIELD_LABELS: dict[str, tuple[str, str]] = {
    "value": ("Demand", "number"),
    "time_on": ("Time on (s)", "number"),
    "time_off": ("Time off (s)", "number"),
    "lb": ("Lower bound", "number"),
    "ub": ("Upper bound", "number"),
    "setpoint": ("Setpoint", "number"),
    "kp": ("kp", "number"),
    "ki": ("ki", "number"),
    "kd": ("kd", "number"),
    "backwards": ("Reverse acting", "bool"),
    "auto_integral_band": ("Auto anti-windup band", "bool"),
    "min_integral": ("Min integral", "number"),
    "max_integral": ("Max integral", "number"),
}

#: What the demand field means in each output unit. Shown so an
#: operator typing "5" knows whether that is counts, mL/min or mL.
DEMAND_UNITS = {
    OutputUnit.duty: "counts (0-4095)",
    OutputUnit.flow: "mL/min",
    OutputUnit.volume: "mL",
}


async def read_calibration(reactor: str, actuator: str) -> dict | None:
    """Fetch an actuator's calibration state, or None if it has none."""
    if STATE.book is None or not STATE.book.has_method(
        reactor,
        actuator,
        "get_calibration",
    ):
        return None
    try:
        payload = await STATE.call(reactor, actuator, "get_calibration")
    except (LookupError, OSError) as err:
        _logger.warning("Could not read %s calibration: %s", actuator, err)
        return None
    try:
        return json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        _logger.warning("Unreadable calibration payload for %s", actuator)
        return None


def _current(reactor: str, actuator: str, channel: str) -> float | None:
    """The running value of one config channel, for prefilling."""
    value, _ = STATE.reading(reactor, actuator, channel)
    return value


def open_control_dialog(reactor: str, actuator: str) -> None:
    """Open the configuration dialog for one actuator."""
    _logger.info("Operator opened control config for %s", actuator)
    ui.timer(0, lambda: _build_dialog(reactor, actuator), once=True)


async def _build_dialog(reactor: str, actuator: str) -> None:
    """Build and show the dialog, prefilled from the running config."""
    calibration = await read_calibration(reactor, actuator)
    state = await _current_selection(reactor, actuator)

    # Explicit height, not max-height: a percentage-height child inside
    # a max-height parent collapses to nothing.
    with ui.dialog() as dialog, ui.card().style(
        "width: 32rem; max-width: 95vw; height: 80vh; "
        "display: flex; flex-direction: column",
    ):
        ui.label(f"Configure {actuator}").classes("text-lg font-semibold")

        method_select = ui.select(
            [m.value for m in ControlMethod],
            value=state["method"],
            label="Control method",
        ).classes("w-full")

        unit_select = ui.select(
            [u.value for u in OutputUnit],
            value=state["output_unit"],
            label="Output unit",
        ).classes("w-full")

        unit_warning = ui.label("").classes("text-sm text-orange-700")

        with ui.scroll_area().style("flex: 1; min-height: 0"):
            fields_container = ui.column().classes("w-full").style(
                "gap: 0.5rem",
            )

        inputs: dict[str, ui.element] = {}

        def render_fields() -> None:
            """Show only the fields the selected method reads."""
            fields_container.clear()
            inputs.clear()
            with fields_container:
                for channel in fields_for(method_select.value):
                    label, kind = FIELD_LABELS[channel]
                    if channel == "value":
                        label = (
                            f"{label} - {DEMAND_UNITS[unit_select.value]}"
                        )
                    current = _current(reactor, actuator, channel)
                    if kind == "bool":
                        inputs[channel] = ui.switch(
                            label,
                            value=bool(current),
                        )
                    else:
                        inputs[channel] = ui.number(
                            label,
                            value=current if current is not None else 0.0,
                            format="%.4f",
                        ).classes("w-full")

        def check_unit() -> None:
            """Report a unit the server would refuse, before writing."""
            reason = unit_rejection_reason(unit_select.value, calibration)
            unit_warning.set_text(reason or "")

        def on_method_change() -> None:
            _logger.info(
                "Operator selected method %s for %s",
                method_select.value,
                actuator,
            )
            render_fields()

        def on_unit_change() -> None:
            _logger.info(
                "Operator selected unit %s for %s",
                unit_select.value,
                actuator,
            )
            check_unit()
            # The demand field is relabelled: the same number means
            # counts, mL/min or mL depending on the unit.
            render_fields()

        method_select.on_value_change(on_method_change)
        unit_select.on_value_change(on_unit_change)

        async def apply() -> None:
            """Validate, then write the plan in order."""
            reason = unit_rejection_reason(unit_select.value, calibration)
            if reason is not None:
                ui.notify(reason, type="negative")
                return

            values = {
                channel: _field_value(widget)
                for channel, widget in inputs.items()
            }
            missing = [
                channel
                for channel, value in values.items()
                if value is None
            ]
            if missing:
                ui.notify(
                    f"Fill in: {', '.join(missing)}",
                    type="negative",
                )
                return

            try:
                plan = build_write_plan(
                    method_select.value,
                    unit_select.value,
                    values,
                )
            except (KeyError, ValueError) as err:
                ui.notify(str(err), type="negative")
                return

            _logger.info(
                "Writing control config for %s: %s",
                actuator,
                [(w.channel, w.value) for w in plan],
            )
            for write in plan:
                ok = await STATE.write_variable(
                    reactor,
                    actuator,
                    write.channel,
                    write.value,
                )
                if not ok:
                    ui.notify(
                        f"Could not write {write.channel}; "
                        "the rest of the change was not applied",
                        type="negative",
                    )
                    return

            ui.notify(f"{actuator} reconfigured", type="positive")
            dialog.close()

        # Docked, so Save is reachable without scrolling the form.
        with ui.row().classes("w-full justify-end").style(
            "gap: 0.5rem; padding-top: 0.5rem",
        ):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Apply", on_click=apply).props("color=primary")

        render_fields()
        check_unit()

    dialog.open()


async def _current_selection(reactor: str, actuator: str) -> dict:
    """The method and unit the actuator is running, for prefilling.

    The server publishes both as UInt32 indices, so they are mapped back
    to names here. An actuator whose variables have not been published
    yet falls back to the server's own defaults.
    """
    methods = list(ControlMethod)
    units = list(OutputUnit)

    method_code = _current(reactor, actuator, "method")
    unit_code = _current(reactor, actuator, "output_unit")

    return {
        "method": _decode(method_code, methods, ControlMethod.manual),
        "output_unit": _decode(unit_code, units, OutputUnit.duty),
    }


def _decode(code: float | None, options: list, fallback: str) -> str:
    """Map a published enum index back to its name."""
    if code is None:
        return fallback.value
    index = int(code)
    if 0 <= index < len(options):
        return options[index].value
    return fallback.value


def _field_value(widget: ui.element) -> object:
    """Read a form field, distinguishing 'blank' from 'zero'."""
    value = widget.value
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    return float(value)
