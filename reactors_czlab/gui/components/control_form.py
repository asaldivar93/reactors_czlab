"""Configure one actuator's controller.

The write ordering is not this module's decision - ``gui/control.py``
builds the plan and a test pins it. This applies the plan in order,
sequentially. It must never write the plan concurrently: every write
triggers a full config rebuild on the server, and the whole point of the
ordering is that ``method`` lands last.

The dialog is built under ``context.client.layout`` rather than wherever
the triggering button happens to sit. ``ui.dialog`` already re-parents
*itself* there (see ``nicegui.elements.dialog.Dialog.__init__``), but it
also drops a "canary" element in the *caller's current slot* so the
dialog is torn down if that slot ever is. The Configure button lives
inside ``actuator_panel``, a ``@ui.refreshable`` that the dashboard's
``ui.timer`` clears and rebuilds - immediately, since ``ui.timer``
defaults to ``immediate=True`` and fires before its first interval, not
after. Building the dialog while that panel's slot is still on the stack
ties the canary to it, so the very first refresh tick deletes the dialog
out from under the operator. Entering ``context.client.layout`` first
moves the canary to the stable page root, which no refreshable panel
ever clears.
"""

from __future__ import annotations

import json
import logging

from nicegui import ui
from nicegui.context import context

from reactors_czlab.core.data import ControlMethod, OutputUnit
from reactors_czlab.gui.control import (
    METHOD_FIELDS,
    build_write_plan,
    unit_rejection_reason,
)
from reactors_czlab.gui.state import STATE
from reactors_czlab.opcua.actuator import control_method, output_unit_map

_logger = logging.getLogger("gui")

#: Numeric fields and their labels, keyed by the channel segment.
FIELD_LABELS = {
    "value": "Demand",
    "time_on": "Time on (s)",
    "time_off": "Time off (s)",
    "lb": "Lower bound",
    "ub": "Upper bound",
    "setpoint": "Setpoint",
    "kp": "kp",
    "ki": "ki",
    "kd": "kd",
    "min_integral": "Min integral",
    "max_integral": "Max integral",
}

#: Fields rendered as switches rather than number inputs.
BOOLEAN_FIELDS = ("backwards", "auto_integral_band")


async def _read_calibration(reactor: str, name: str) -> dict | None:
    """The actuator's calibration payload, or None if unreadable."""
    try:
        raw = await STATE.call(reactor, name, "get_calibration")
    except Exception:
        _logger.warning(
            "Could not read the calibration of %s:%s",
            reactor,
            name,
            exc_info=True,
        )
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        _logger.warning("Unreadable calibration payload for %s", name)
        return None


def _current_method(reactor: str, name: str) -> ControlMethod:
    """The method actually running on the actuator.

    Falls back to ``manual`` when nothing has been published yet
    (``STATE.reading`` returns ``None``) or the index is not one the
    server recognises.
    """
    raw = STATE.reading(reactor, name, "method")[0]
    if raw is None:
        return ControlMethod.manual
    return control_method.get(int(raw), ControlMethod.manual)


def _current_unit(reactor: str, name: str) -> OutputUnit:
    """The output unit actually running on the actuator.

    Falls back to ``duty`` when nothing has been published yet
    (``STATE.reading`` returns ``None``) or the index is not one the
    server recognises.
    """
    raw = STATE.reading(reactor, name, "output_unit")[0]
    if raw is None:
        return OutputUnit.duty
    return output_unit_map.get(int(raw), OutputUnit.duty)


async def open_control_dialog(reactor: str, name: str) -> None:
    """Open the configuration dialog for one actuator."""
    calibration = await _read_calibration(reactor, name)

    current = {
        field: STATE.reading(reactor, name, field)[0]
        for field in (*FIELD_LABELS, *BOOLEAN_FIELDS)
    }

    # Regression: building this dialog under whatever slot the
    # triggering button happens to sit in ties its lifetime to that
    # slot - see the module docstring. Entering the stable page root
    # first keeps the dialog alive across actuator_panel's refreshes.
    with context.client.layout:
        with ui.dialog() as dialog, ui.card().classes("w-[32rem]"):
            # Regression: ui.dialog is only hidden by close(), never
            # removed - left alone, every Configure click piles up
            # another one under context.client.layout for the life of
            # the page session. Deleting on the value-change event
            # (rather than only from the Apply/Cancel handlers) also
            # catches a backdrop click or Escape, which close the
            # dialog without running either.
            dialog.on_value_change(
                lambda e: dialog.delete() if not e.value else None,
            )

            ui.label(f"{name} control").classes("text-lg font-semibold")

            method_select = ui.select(
                {m: m.value for m in ControlMethod},
                value=_current_method(reactor, name),
                label="Method",
            ).classes("w-full")
            unit_select = ui.select(
                {u: u.value for u in OutputUnit},
                value=_current_unit(reactor, name),
                label="Output unit",
            ).classes("w-full")

            warning = ui.label("").classes("text-orange-600 text-sm")
            inputs: dict[str, object] = {}

            @ui.refreshable
            def fields() -> None:
                """Show only what the selected method consumes."""
                inputs.clear()
                shown = ("value", *METHOD_FIELDS[method_select.value])
                for field in shown:
                    if field in BOOLEAN_FIELDS:
                        inputs[field] = ui.switch(
                            field.replace("_", " "),
                            value=bool(current.get(field) or False),
                        )
                    else:
                        inputs[field] = ui.number(
                            FIELD_LABELS[field],
                            value=float(current.get(field) or 0.0),
                        ).classes("w-full")

            def on_unit_change() -> None:
                """Warn before an unusable unit is written, not after."""
                reason = unit_rejection_reason(
                    unit_select.value,
                    calibration,
                )
                warning.set_text(reason or "")

            method_select.on_value_change(lambda _: fields.refresh())
            unit_select.on_value_change(lambda _: on_unit_change())
            fields()
            on_unit_change()

            async def apply() -> None:
                """Write the plan in order, stopping at the first
                failure.
                """
                reason = unit_rejection_reason(
                    unit_select.value,
                    calibration,
                )
                if reason is not None:
                    ui.notify(reason, type="negative")
                    return

                # Regression: a ui.number the operator clears yields
                # None, which the server's _as_float raises on inside
                # datachange_notification - the whole config is then
                # silently dropped while this dialog still says
                # "configured". Refuse before writing anything rather
                # than coerce to 0.0, which on a dosing pump is its
                # own hazard.
                empty = [
                    field
                    for field, widget in inputs.items()
                    if field not in BOOLEAN_FIELDS and widget.value is None
                ]
                if empty:
                    label = FIELD_LABELS[empty[0]]
                    ui.notify(
                        f"{label} is empty; fill it in before applying",
                        type="negative",
                    )
                    return

                plan = build_write_plan(
                    method_select.value,
                    unit_select.value,
                    {
                        field: widget.value
                        for field, widget in inputs.items()
                    },
                )
                for write in plan:
                    ok = await STATE.write_variable(
                        reactor,
                        name,
                        write.channel,
                        write.value,
                    )
                    if not ok:
                        ui.notify(
                            f"Failed writing {write.channel}; the "
                            "actuator is part configured - check "
                            "record.log",
                            type="negative",
                        )
                        return
                ui.notify(f"{name} configured", type="positive")
                dialog.close()

            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Apply", on_click=apply)

        dialog.open()
