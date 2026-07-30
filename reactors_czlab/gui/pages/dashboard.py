"""Reactor dashboard routes."""

from __future__ import annotations

from nicegui import ui


@ui.page("/")
def index() -> None:
    """Placeholder, replaced in the dashboard task."""
    ui.label("Bioreactors")
