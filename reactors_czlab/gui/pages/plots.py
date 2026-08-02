"""Live scatter plots per reactor.

Hybrid data: the database supplies the history for the selected window,
the OPC subscription supplies the live tail. Re-querying on a timer
would put a full table scan behind every refresh; the subscription is
already delivering the new points to this process.

Without a database the charts still run, live-only, and say so.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from nicegui import ui

from reactors_czlab.gui.components.shell import (
    header,
    not_connected_notice,
    reactor_tabs,
)
from reactors_czlab.gui.controllers.plots import (
    BIOMASS_CHANNELS,
    DEFAULT_BIOMASS,
    DEFAULT_WINDOW,
    PANELS,
    WINDOWS,
    append_live_point,
    build_series,
    panel_filters,
    window_range,
)
from reactors_czlab.gui.state import STATE
from reactors_czlab.sql import operations

_logger = logging.getLogger("gui")

#: How often the live tail is appended and the charts redrawn.
TAIL_SECONDS = 2.0


@ui.page("/reactor/{reactor}/plots")
def plots_page(reactor: str) -> None:
    """Four charts, a window selector and a biomass channel picker."""
    header(reactor)

    state: dict = {
        "window": DEFAULT_WINDOW,
        "biomass": list(DEFAULT_BIOMASS),
        "series": {},
        "charts": {},
    }

    with ui.column().classes("w-full").style("padding: 1rem; gap: 1rem"):
        reactor_tabs(reactor, "Plots")

        if not STATE.connected:
            not_connected_notice()
            return

        _controls(reactor, state)

        if not STATE.database_available:
            ui.label(
                "No database: charts show only what has arrived since "
                "this page was opened.",
            ).classes("text-sm text-orange-700")

        # Two charts per row on a wide screen, stacking on a narrow one.
        # min-width: 0 on the children, or the charts' intrinsic width
        # stops them shrinking and they wrap one per row.
        with ui.element("div").style(
            "display: flex; flex-wrap: wrap; gap: 1rem; width: 100%",
        ):
            for panel in PANELS:
                with ui.element("div").style(
                    "flex: 1 1 28rem; min-width: 0",
                ), ui.card().classes("w-full"):
                    ui.label(panel.title).classes(
                        "text-sm font-semibold",
                    )
                    chart = ui.echart(_options(panel, [])).style(
                        "height: 18rem; width: 100%",
                    )
                    state["charts"][panel.key] = chart

    async def reload() -> None:
        """Re-read the history for every panel."""
        await _load_history(reactor, state)

    state["reload"] = reload
    ui.timer(0, reload, once=True)
    ui.timer(TAIL_SECONDS, lambda: _append_tail(reactor, state))


def _controls(reactor: str, state: dict) -> None:
    """The window selector and the biomass channel picker."""
    with ui.row().classes("items-end flex-wrap").style("gap: 1rem"):
        window = ui.select(
            [label for label, _, _ in WINDOWS],
            value=state["window"],
            label="Window",
        ).style("min-width: 8rem")

        biomass = ui.select(
            list(BIOMASS_CHANNELS),
            value=state["biomass"],
            multiple=True,
            label="Biomass channels",
        ).style("min-width: 16rem")

        async def on_window() -> None:
            _logger.info("Operator selected window %s", window.value)
            state["window"] = window.value
            await state["reload"]()

        async def on_biomass() -> None:
            _logger.info(
                "Operator selected biomass channels %s",
                biomass.value,
            )
            state["biomass"] = list(biomass.value or [])
            await state["reload"]()

        window.on_value_change(lambda: ui.timer(0, on_window, once=True))
        biomass.on_value_change(lambda: ui.timer(0, on_biomass, once=True))


async def _load_history(reactor: str, state: dict) -> None:
    """Fill every panel from the database, then redraw."""
    time_range = window_range(state["window"])

    for panel in PANELS:
        filters = panel_filters(panel, state["biomass"])
        rows: list = []
        if STATE.database_available and filters:
            try:
                rows = await asyncio.to_thread(
                    operations.query_series,
                    reactor,
                    list(filters),
                    time_range,
                )
            except operations.SqlError as err:
                _logger.warning(
                    "Could not load %s history: %s",
                    panel.key,
                    err,
                )
                ui.notify(f"{panel.title}: {err}", type="warning")

        state["series"][panel.key] = build_series(rows, filters)
        _redraw(panel, state)


def _append_tail(reactor: str, state: dict) -> None:
    """Append the newest subscription readings to every chart."""
    if not STATE.connected:
        return

    for panel in PANELS:
        series = state["series"].get(panel.key)
        if not series:
            continue

        changed = False
        for name, channel in panel_filters(panel, state["biomass"]):
            value, stamp = STATE.reading(reactor, name, channel)
            if value is None or stamp is None:
                continue
            # The sentinel is not a measurement and must not be plotted;
            # the gap in the line is the honest rendering of a failed
            # read.
            if _is_sentinel(value):
                continue
            changed |= append_live_point(
                series,
                name,
                channel,
                stamp,
                value,
            )

        if changed:
            _redraw(panel, state)


def _is_sentinel(value: float) -> bool:
    """Whether a reading is the failed-read sentinel."""
    from reactors_czlab.gui.format import is_error

    return is_error(value)


def _redraw(panel, state: dict) -> None:
    """Push a panel's series into its chart."""
    chart = state["charts"].get(panel.key)
    if chart is None:
        return
    chart.options.update(_options(panel, state["series"].get(panel.key, [])))
    chart.update()


def _options(panel, series: list) -> dict:
    """The ECharts option dictionary for one panel."""
    return {
        "tooltip": {"trigger": "axis"},
        "legend": {
            "data": [line.label for line in series],
            "type": "scroll",
            "bottom": 0,
        },
        "grid": {"left": 55, "right": 20, "top": 30, "bottom": 60},
        # A real time axis, not category labels: the requirement is that
        # the x axis reads as dates and times rather than elapsed
        # minutes or hours.
        "xAxis": {"type": "time"},
        "yAxis": {"type": "value", "name": panel.units, "scale": True},
        "dataZoom": [
            {"type": "inside"},
            {"type": "slider", "height": 16, "bottom": 26},
        ],
        "series": [
            {
                "name": line.label,
                "type": "scatter",
                "symbolSize": 5,
                "data": [
                    [_millis(stamp), value] for stamp, value in line.points
                ],
            }
            for line in series
        ],
    }


def _millis(stamp: datetime) -> int:
    """ECharts wants epoch milliseconds on a time axis."""
    return int(stamp.timestamp() * 1000)
