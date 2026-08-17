"""Live scatter plots per reactor.

Hybrid data: the database supplies the history for the selected window,
the OPC subscription supplies the live tail. Re-querying on a timer
would put a full table scan behind every refresh; the subscription is
already delivering the new points to this process.

Without a database the charts still use the GUI's bounded OPC history and
say that persisted history is unavailable.
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
    MAX_TRACE_POINTS,
    PANELS,
    WINDOWS,
    append_live_point,
    build_series,
    downsample,
    merge_history,
    panel_filters,
    window_range,
)
from reactors_czlab.gui.state import STATE
from reactors_czlab.sql import operations
from reactors_czlab.sql.operations import get_date_filter_range

_logger = logging.getLogger("gui")

#: How often the page checks whether the subscription has a new point.
TAIL_SECONDS = 2.0


@ui.page("/reactor/{reactor}/plots")
async def plots_page(reactor: str) -> None:
    """Four charts, a window selector and a biomass channel picker."""
    header(reactor)

    state: dict = {
        "window": DEFAULT_WINDOW,
        "biomass": list(DEFAULT_BIOMASS),
        "series": {},
        "charts": {},
        "revision": None,
    }

    with ui.column().classes("w-full").style("padding: 1rem; gap: 1rem"):
        reactor_tabs(reactor, "Plots")

        if not STATE.connected:
            not_connected_notice()
            return

        _controls(reactor, state)

        # One standing notice rather than a toast per panel: a missing
        # database is a condition, not an event, and it is the same
        # condition for all four charts. Its text is owned by
        # _report_history_state, which runs on every load.
        notice = ui.label("").classes("text-sm text-orange-700")
        notice.set_visibility(False)
        state["notice"] = notice

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
                    chart = ui.plotly(_figure(panel, [])).style(
                        "height: 18rem; width: 100%",
                    )
                    state["charts"][panel.key] = chart

    async def reload() -> None:
        """Re-read the history for every panel."""
        await _load_history(reactor, state)

    state["reload"] = reload
    # Awaited, not deferred onto a once-timer: that can fire after the
    # client is gone and raise against a page nobody is looking at.
    await reload()
    ui.timer(TAIL_SECONDS, lambda: _poll_tail(reactor, state))


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

        window.on_value_change(lambda _: on_window())
        biomass.on_value_change(lambda _: on_biomass())


async def _load_history(reactor: str, state: dict) -> None:
    """Fill every panel from persisted and recent in-memory history."""
    revision = _revision(reactor, state)
    if state.get("revision") == revision:
        return

    time_range = window_range(state["window"])
    state["cutoff"] = get_date_filter_range(*time_range)
    memory_points: list[tuple] = []
    if STATE.client is not None and hasattr(STATE.client, "history_points"):
        memory_points = STATE.client.history_points(
            reactor=reactor,
            since=state["cutoff"],
        )

    # One failure, reported once. Every panel queries the same database,
    # so a database that is not there produced one toast per panel -
    # four identical warnings stacked over the charts, and four more on
    # every window change.
    failure: str | None = None

    for panel in PANELS:
        filters = panel_filters(panel, state["biomass"])
        rows: list = []
        if STATE.database_available and filters and failure is None:
            try:
                rows = await asyncio.to_thread(
                    operations.query_series,
                    reactor,
                    list(filters),
                    time_range,
                )
            except operations.SqlError as err:
                _logger.warning("Could not load history: %s", err)
                failure = str(err)

        rows = merge_history(rows, memory_points)
        state["series"][panel.key] = build_series(rows, filters)
        _rebuild(panel, state)

    state["revision"] = revision
    _report_history_state(state, failure)


def _report_history_state(state: dict, failure: str | None) -> None:
    """Say once whether the charts show history or only a live tail.

    The only place that decides this. It ran after the page had already
    set the notice for a missing psycopg and, seeing no query failure -
    there had been no query to fail - cleared it again.
    """
    notice = state.get("notice")
    if notice is None:
        return

    reason = failure
    if reason is None and not STATE.database_available:
        reason = STATE.database_reason

    if reason is None:
        notice.set_text("")
        notice.set_visibility(False)
        return

    notice.set_text(
        f"No database: {reason}. The charts show only what has arrived "
        f"in the GUI's recent OPC history.",
    )
    notice.set_visibility(True)


async def _poll_tail(reactor: str, state: dict) -> None:
    """Rebuild after a relevant state change, otherwise append new data."""
    if state.get("revision") != _revision(reactor, state):
        await _load_history(reactor, state)
        return
    _append_tail(reactor, state)


def _append_tail(reactor: str, state: dict) -> None:
    """Append only genuinely new subscription readings to Plotly."""
    if not STATE.connected:
        return

    for panel in PANELS:
        series = state["series"].get(panel.key)
        if not series:
            continue

        for trace_index, (name, channel) in enumerate(
            panel_filters(panel, state["biomass"]),
        ):
            value, stamp = STATE.reading(reactor, name, channel)
            if value is None or stamp is None:
                continue
            # The sentinel is not a measurement and must not be plotted;
            # the gap in the line is the honest rendering of a failed
            # read.
            if _is_sentinel(value):
                continue
            changed = append_live_point(
                series,
                name,
                channel,
                stamp,
                value,
            )
            if not changed:
                continue
            if len(series[trace_index].points) > MAX_TRACE_POINTS:
                del series[trace_index].points[:-MAX_TRACE_POINTS]
            _extend_trace(
                panel,
                state,
                trace_index,
                stamp,
                value,
            )


def _is_sentinel(value: float) -> bool:
    """Whether a reading is the failed-read sentinel."""
    from reactors_czlab.gui.format import is_error

    return is_error(value)


def _revision(reactor: str, state: dict) -> tuple:
    """The only state changes that require rebuilding Plotly figures."""
    return (
        reactor,
        state["window"],
        tuple(state["biomass"]),
        STATE.generation,
    )


def _rebuild(panel, state: dict) -> None:
    """Replace a whole figure after a query-shaping state change."""
    chart = state["charts"].get(panel.key)
    if chart is None:
        return
    chart.update_figure(
        _figure(
            panel,
            state["series"].get(panel.key, []),
            state.get("cutoff"),
        ),
    )


def _extend_trace(
    panel,
    state: dict,
    trace_index: int,
    stamp: datetime,
    value: float,
) -> None:
    """Append one point without serialising and replacing the figure."""
    chart = state["charts"].get(panel.key)
    if chart is None:
        return
    chart.run_plot_method(
        "extendTraces",
        {"x": [[stamp.isoformat()]], "y": [[value]]},
        [trace_index],
        MAX_TRACE_POINTS,
    )


def _figure(panel, series: list, cutoff: datetime | None = None) -> dict:
    """The declarative Plotly figure for one panel.

    ``cutoff`` pins the left edge of the time axis to the start of the
    selected window. Without it the chart scales to whatever points exist,
    so a freshly opened page can make the window selector appear inert.
    """
    x_axis: dict = {"type": "date"}
    if cutoff is not None:
        # A partial range fixes the lower bound while autoranging the upper,
        # including after extendTraces appends a newer sample.
        x_axis["range"] = [cutoff.isoformat(), None]
        x_axis["autorange"] = "max"

    return {
        "data": [
            {
                "name": line.label,
                "type": "scattergl",
                "mode": "lines+markers",
                "marker": {"size": 4},
                "x": [stamp.isoformat() for stamp, _ in sampled],
                "y": [value for _, value in sampled],
            }
            for line in series
            for sampled in [downsample(line.points)]
        ],
        "layout": {
            "autosize": True,
            "height": 288,
            "margin": {"l": 55, "r": 20, "t": 20, "b": 55},
            "hovermode": "x unified",
            "legend": {"orientation": "h", "y": -0.2},
            "xaxis": x_axis,
            "yaxis": {"title": {"text": panel.units}, "autorange": True},
        },
        "config": {
            "displaylogo": False,
            "responsive": True,
            "scrollZoom": True,
        },
    }
