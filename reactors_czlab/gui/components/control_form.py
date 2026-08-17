"""The dialog that atomically reconfigures an actuator's controller."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from asyncua import ua
from nicegui import ui

from reactors_czlab.core.data import OutputUnit
from reactors_czlab.gui.components.shell import disable_when_read_only
from reactors_czlab.gui.control import (
    CONFIG_FIELDS,
    build_config_args,
    fields_for,
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


async def read_control_config(
    reactor: str,
    actuator: str,
) -> dict | None:
    """Fetch the running config and enum options from the server."""
    try:
        payload = await STATE.call(
            reactor,
            actuator,
            "get_control_config",
        )
        config = json.loads(payload)
    except (LookupError, OSError, TypeError, json.JSONDecodeError, ua.UaError) as err:
        _logger.warning("Could not read %s control config: %s", actuator, err)
        return None
    if not isinstance(config, dict):
        _logger.warning("Unreadable control config payload for %s", actuator)
        return None
    return config


async def open_control_dialog(
    reactor: str,
    actuator: str,
    on_change: Callable[[dict], object] | None = None,
) -> None:
    """Open the configuration dialog for one actuator.

    Awaited directly by the button rather than deferred onto a timer:
    elements built from a timer callback are rendered but their event
    handlers never fire, so the dialog's own Apply and Cancel did
    nothing at all.
    """
    _logger.info("Operator opened control config for %s", actuator)
    if not await _build_dialog(reactor, actuator, on_change):
        ui.notify(
            f"Could not read the running configuration for {actuator}",
            type="negative",
        )


async def _build_dialog(
    reactor: str,
    actuator: str,
    on_change: Callable[[dict], object] | None = None,
) -> bool:
    """Build and show the dialog, prefilled from the running config."""
    state = await read_control_config(reactor, actuator)
    if state is None:
        return False
    methods = list(state["methods"])
    units = list(state["output_units"])

    # Explicit height, not max-height: a percentage-height child inside
    # a max-height parent collapses to nothing.
    with ui.dialog() as dialog, ui.card().style(
        "width: 32rem; max-width: 95vw; height: 80vh; "
        "display: flex; flex-direction: column",
    ):
        ui.label(f"Configure {actuator}").classes("text-lg font-semibold")

        method_select = ui.select(
            methods,
            value=state["method"],
            label="Control method",
        ).classes("w-full")

        unit_select = ui.select(
            units,
            value=state["output_unit"],
            label="Output unit",
        ).classes("w-full")

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
                    current = state[channel]
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
            # The demand field is relabelled: the same number means
            # counts, mL/min or mL depending on the unit.
            render_fields()

        method_select.on_value_change(lambda _: on_method_change())
        unit_select.on_value_change(lambda _: on_unit_change())

        async def apply() -> None:
            """Validate, then make one atomic server call."""
            edited = {
                channel: _field_value(widget)
                for channel, widget in inputs.items()
            }
            missing = [
                channel
                for channel, value in edited.items()
                if value is None
            ]
            if missing:
                ui.notify(
                    f"Fill in: {', '.join(missing)}",
                    type="negative",
                )
                return

            try:
                values = {
                    channel: state[channel] for channel in CONFIG_FIELDS
                }
                values.update(edited)
                args = build_config_args(
                    methods.index(method_select.value),
                    units.index(unit_select.value),
                    values,
                )
            except (KeyError, ValueError) as err:
                ui.notify(str(err), type="negative")
                return

            controls = [
                method_select,
                unit_select,
                cancel_button,
                apply_button,
                *inputs.values(),
            ]
            for control in controls:
                control.disable()
            try:
                result = await STATE.call(
                    reactor,
                    actuator,
                    "apply_control_config",
                    *args,
                )
                accepted, message = result
                latest = await read_control_config(reactor, actuator)
                if latest is not None:
                    state.clear()
                    state.update(latest)
                    method_select.set_value(state["method"])
                    unit_select.set_value(state["output_unit"])
                    render_fields()
                    if on_change is not None:
                        on_change(latest)
            except (LookupError, OSError, ua.UaError) as err:
                accepted = False
                message = f"Could not apply configuration: {err}"
            finally:
                for control in controls:
                    control.enable()

            _logger.info(
                "Control config response for %s: %s - %s",
                actuator,
                accepted,
                message,
            )
            ui.notify(message, type="positive" if accepted else "negative")

        # Docked, so Save is reachable without scrolling the form.
        with ui.row().classes("w-full justify-end").style(
            "gap: 0.5rem; padding-top: 0.5rem",
        ):
            cancel_button = ui.button(
                "Cancel",
                on_click=dialog.close,
            ).props("flat")
            apply_button = ui.button("Apply", on_click=apply).props(
                "color=primary",
            )
            disable_when_read_only(apply_button)

        render_fields()

    dialog.open()
    return True


def _field_value(widget: ui.element) -> object:
    """Read a form field, distinguishing 'blank' from 'zero'."""
    value = widget.value
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    return float(value)
