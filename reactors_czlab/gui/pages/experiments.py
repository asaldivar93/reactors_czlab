"""Create, start, stop and export experiments.

Starting an experiment does two things that have to stay together: it
claims the reactors in the database, and it tags the archived rows with
the experiment's name. Recording is started if it is not already
running, because an experiment that records nothing is not one an
operator would recognise - but the two states are shown separately so
it is visible which is which.
"""

from __future__ import annotations

import asyncio
import logging

from nicegui import ui

from reactors_czlab.gui.components.shell import (
    disable_when_read_only,
    header,
    status_badges,
)
from reactors_czlab.gui.state import STATE
from reactors_czlab.sql import operations

_logger = logging.getLogger("gui")

#: Where an export is written, relative to the working directory.
DEFAULT_EXPORT = "experiment.csv"


@ui.page("/experiments")
async def experiments_page() -> None:
    """The experiment interface."""
    header()
    with ui.column().classes("w-full").style("padding: 1rem; gap: 1rem"):
        ui.label("Experiments").classes("text-xl font-semibold")

        if not STATE.database_available:
            with ui.card().classes("w-full"):
                ui.label("Experiments need a database").classes(
                    "text-orange-700 font-semibold",
                )
                ui.label(STATE.database_reason).classes(
                    "text-sm text-gray-600",
                )
            return

        table = ui.column().classes("w-full").style("gap: 0.5rem")

        async def reload() -> None:
            """Re-read the experiment list."""
            table.clear()
            with table:
                await _experiment_table(reload)

        _create_form(reload)
        ui.separator()
        # Awaited, not deferred onto a once-timer: that can fire after
        # the client is gone. Awaiting also means the first paint
        # carries the list rather than a "Loading..." placeholder.
        await reload()


def _create_form(reload) -> None:
    """Name a new experiment and pick its reactors."""
    reactors = STATE.book.reactors if STATE.book is not None else []

    with ui.card().classes("w-full"):
        ui.label("New experiment").classes("text-sm font-semibold")
        with ui.row().classes("items-end flex-wrap").style("gap: 0.5rem"):
            name = ui.input("Name").style("min-width: 14rem")
            picked = ui.select(
                reactors,
                multiple=True,
                label="Reactors",
            ).style("min-width: 14rem")
            if not reactors:
                picked.disable()
                picked.tooltip("Connect to the server to list its reactors")

            async def create() -> None:
                _logger.info(
                    "Operator creating experiment %s on %s",
                    name.value,
                    picked.value,
                )
                try:
                    await asyncio.to_thread(
                        operations.create_experiment,
                        name.value or "",
                        list(picked.value or []),
                    )
                except operations.SqlError as err:
                    ui.notify(str(err), type="negative")
                    return
                ui.notify(f"Created {name.value}", type="positive")
                name.set_value("")
                picked.set_value([])
                await reload()

            ui.button("Create", on_click=create).props("color=primary")


async def _experiment_table(reload) -> None:
    """Every experiment, with the actions its state allows."""
    try:
        rows = await asyncio.to_thread(operations.list_experiments)
    except operations.SqlError as err:
        ui.label(f"Could not list experiments: {err}").classes(
            "text-red-600",
        )
        return

    if not rows:
        ui.label("No experiments yet").classes("text-gray-500")
        return

    for row in rows:
        _experiment_card(row, reload)


def _experiment_card(row: dict, reload) -> None:
    """One experiment, and what may be done to it."""
    colours = {
        "created": "text-gray-600",
        "running": "text-blue-700",
        "finished": "text-gray-500",
    }

    with ui.card().classes("w-full"):
        with ui.row().classes("w-full items-center justify-between"):
            with ui.column().style("gap: 0"):
                ui.label(row["name"]).classes("font-semibold")
                ui.label(", ".join(row["reactors"])).classes(
                    "text-xs text-gray-500 font-mono",
                )
            ui.label(row["state"]).classes(
                f"text-sm {colours[row['state']]}",
            )

        if row["start_date"] is not None:
            ui.label(
                f"started {row['start_date']:%Y-%m-%d %H:%M}"
                + (
                    f", ended {row['end_date']:%Y-%m-%d %H:%M}"
                    if row["end_date"] is not None
                    else ""
                ),
            ).classes("text-xs text-gray-500")

        with ui.row().classes("flex-wrap").style("gap: 0.5rem"):
            if row["state"] == "created":
                disable_when_read_only(
                    ui.button(
                        "Start",
                        on_click=lambda r=row: _start(r, reload),
                    ).props("size=sm color=primary"),
                )
            elif row["state"] == "running":
                ui.button(
                    "Stop",
                    on_click=lambda r=row: _stop(r, reload),
                ).props("size=sm color=warning")
            else:
                restart = ui.button(
                    "Start again",
                    on_click=lambda r=row: _start(r, reload),
                ).props("size=sm outline")
                restart.tooltip(
                    "Recording resumes under the same experiment name",
                )
                disable_when_read_only(restart)

            ui.button(
                "Export CSV",
                on_click=lambda r=row: _export(r),
            ).props("size=sm outline")


async def _start(row: dict, reload) -> None:
    """Claim the reactors, tag them, and make sure recording is on."""
    _logger.info("Operator starting experiment %s", row["name"])
    try:
        reactors = await asyncio.to_thread(
            operations.start_experiment,
            row["name"],
        )
    except operations.SqlError as err:
        ui.notify(str(err), type="negative")
        return

    if STATE.client is not None:
        for reactor in reactors:
            STATE.client.experiment_tags[reactor] = row["name"]

    for reactor in reactors:
        if STATE.is_recording(reactor):
            continue
        try:
            await STATE.start_recording(reactor)
        except operations.SqlError as err:
            ui.notify(
                f"Experiment started but {reactor} did not record: {err}",
                type="warning",
            )
            await reload()
            return

    ui.notify(
        f"{row['name']} running on {', '.join(reactors)}",
        type="positive",
    )
    status_badges.refresh()
    await reload()


async def _stop(row: dict, reload) -> None:
    """Finish an experiment and release its reactors.

    Recording is deliberately left running: it is independent of any
    experiment, and stopping it here would surprise an operator who
    started it before the experiment.
    """
    _logger.info("Operator stopping experiment %s", row["name"])
    try:
        await asyncio.to_thread(operations.stop_experiment, row["name"])
    except operations.SqlError as err:
        ui.notify(str(err), type="negative")
        return

    if STATE.client is not None:
        for reactor in row["reactors"]:
            STATE.client.experiment_tags.pop(reactor, None)

    ui.notify(f"{row['name']} stopped", type="warning")
    status_badges.refresh()
    await reload()


async def _export(row: dict) -> None:
    """Write every reading tagged with this experiment to a csv."""
    out_name = f"{row['name']}.csv"
    _logger.info("Operator exporting experiment %s", row["name"])
    try:
        rows = await asyncio.to_thread(
            operations.query_experiment_data,
            row["name"],
        )
        await asyncio.to_thread(operations.row_to_csv, out_name, rows)
    except operations.SqlError as err:
        ui.notify(str(err), type="negative")
        return

    if not rows:
        ui.notify(
            f"{row['name']} has no recorded readings yet",
            type="warning",
        )
        return

    ui.notify(f"Wrote {len(rows)} rows to {out_name}", type="positive")
    ui.download(out_name)
