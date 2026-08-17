"""Confirmation and in-flight state for consequential GUI actions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from nicegui import ui

from reactors_czlab.gui.state import STATE


async def confirm(title: str, message: str, danger: bool = False) -> bool:
    """Ask the operator to confirm an action.

    Parameters
    ----------
    title, message:
        The action and its concrete consequence.
    danger:
        Whether to render the confirmation as destructive.

    Returns
    -------
    bool
        True only when the operator explicitly confirms.

    """
    with ui.dialog().props("persistent") as dialog, ui.card().style(
        "width: 28rem; max-width: 92vw; min-height: 12rem; "
        "display: flex; flex-direction: column",
    ):
        ui.label(title).classes("text-lg font-semibold")
        ui.label(message).classes("text-sm").style("flex: 1")
        with ui.row().classes("w-full justify-end").style("gap: 0.5rem"):
            ui.button(
                "Cancel",
                on_click=lambda: dialog.submit(False),
            ).props("flat")
            ui.button(
                "Confirm",
                on_click=lambda: dialog.submit(True),
                color="negative" if danger else "primary",
            )
    result = await dialog
    return bool(result)


@contextmanager
def in_flight(control: ui.element) -> Iterator[None]:
    """Block repeated activation while one async action is outstanding."""
    control.disable()
    # Quasar's loading state blocks pointer activation even if a reactive
    # writable binding updates ``disable`` while the request is outstanding.
    control.props("loading")
    try:
        yield
    finally:
        control.props(remove="loading")
        if STATE.writable:
            control.enable()
