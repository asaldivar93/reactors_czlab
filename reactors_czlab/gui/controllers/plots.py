"""What the plot panels contain, and how their series are assembled.

Panels are defined by a list of ``(name, channel)`` filters - the same
shape as ``run_plots.PLOT_FILTERS`` - so adding actuator panels later is
an entry in ``PANELS``, not a rewrite. That is a stated future
requirement, which is why the shape is this rather than four hardcoded
charts.

Pure: rows in, series out. The database call and the ECharts option
dictionary live in the page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

#: The biomass sensor's channels, in wavelength order. ``clear`` and
#: ``nir`` last because they are not a wavelength.
BIOMASS_CHANNELS: tuple[str, ...] = (
    "415",
    "445",
    "480",
    "515",
    "555",
    "590",
    "630",
    "680",
    "clear",
    "nir",
)

#: Which biomass channels are plotted before an operator chooses. One,
#: because ten overlapping series is not a readable default - and 445 is
#: what run_plots.py has always shown.
DEFAULT_BIOMASS = ("445",)

#: Selectable windows, as ``(label, amount, units)`` for
#: ``get_date_filter_range``.
WINDOWS: tuple[tuple[str, float, str], ...] = (
    ("15 min", 15.0, "m"),
    ("1 h", 1.0, "h"),
    ("2 h", 2.0, "h"),
    ("6 h", 6.0, "h"),
    ("24 h", 24.0, "h"),
    ("7 d", 7.0, "d"),
    ("All", 0.0, "all"),
)

DEFAULT_WINDOW = "2 h"


@dataclass(frozen=True)
class Panel:
    """One chart: a title, a y-axis label, and the channels it draws."""

    key: str
    title: str
    units: str
    #: ``(sensor_or_actuator_name, channel)`` pairs.
    filters: tuple[tuple[str, str], ...] = ()
    #: True for the panel whose channels the operator picks.
    selectable: bool = False


#: The four panels the requirements name. An actuator panel would be a
#: fifth entry here - for example
#: ``Panel("pumps", "Pump output", "counts",
#:        (("pwm0", "curr_value"), ("pwm1", "curr_value")))`` - and
#: nothing else would have to change.
PANELS: tuple[Panel, ...] = (
    Panel("ph", "pH", "pH", (("ph", "pH"),)),
    Panel("do", "Dissolved oxygen", "ppm", (("do", "ppm"),)),
    # Both probes report temperature, and both call the channel oC, so
    # the pair is what identifies each series - never the channel alone.
    Panel(
        "temperature",
        "Temperature",
        "degC",
        (("ph", "oC"), ("do", "oC")),
    ),
    Panel("biomass", "Biomass", "counts", selectable=True),
)


@dataclass
class Series:
    """One line on a chart."""

    label: str
    points: list[tuple[datetime, float]] = field(default_factory=list)


def window_range(label: str) -> tuple[float, str]:
    """The ``(amount, units)`` for a window label.

    Raises
    ------
    KeyError
        If the label is not one of ``WINDOWS``.

    """
    for name, amount, units in WINDOWS:
        if name == label:
            return (amount, units)
    error_message = f"Unknown window {label!r}"
    raise KeyError(error_message)


def biomass_filters(channels: list[str]) -> tuple[tuple[str, str], ...]:
    """Turn selected biomass channels into panel filters."""
    return tuple(("biomass", channel) for channel in channels)


def panel_filters(
    panel: Panel,
    biomass_channels: list[str],
) -> tuple[tuple[str, str], ...]:
    """The filters to query for a panel, given the current selection."""
    if panel.selectable:
        return biomass_filters(biomass_channels)
    return panel.filters


def series_label(name: str, channel: str) -> str:
    """How a series is named in the legend.

    Always both parts: two sensors on one reactor publish a channel
    called ``oC``, so a legend showing only the channel would have two
    identical entries.
    """
    return f"{name}:{channel}"


def build_series(
    rows: list[tuple],
    filters: tuple[tuple[str, str], ...],
) -> list[Series]:
    """Group database rows into one series per filter.

    Parameters
    ----------
    rows:
        As ``query_series`` returns them: the ``data`` table's columns
        in ``COLUMNS`` order.
    filters:
        The ``(name, channel)`` pairs this panel draws, which fixes the
        series order regardless of what the query returned.

    Returns
    -------
    list[Series]
        One entry per filter, in filter order. A filter with no rows
        still gets an empty series so the legend does not change shape
        as data arrives.

    """
    by_key: dict[tuple[str, str], Series] = {
        (name, channel): Series(series_label(name, channel))
        for name, channel in filters
    }

    for row in rows:
        # COLUMNS order: node_id, date, reactor, name, channel, value,
        # experiment_name.
        _, date, _, name, channel, value = row[:6]
        series = by_key.get((name, channel))
        if series is not None:
            series.points.append((date, value))

    for series in by_key.values():
        series.points.sort(key=lambda point: point[0])

    return list(by_key.values())


def append_live_point(
    series: list[Series],
    name: str,
    channel: str,
    stamp: datetime,
    value: float,
) -> bool:
    """Add a live reading to the matching series, if it is plotted.

    The tail of every chart comes from the OPC subscription rather than
    from re-querying the database on a timer.

    Returns
    -------
    bool
        Whether the point was added. False when the series is not on
        this chart, or when the point is not newer than the last one -
        the subscription re-notifies on every publish, so without that
        check a steady reading would pile up duplicate points.

    """
    label = series_label(name, channel)
    for line in series:
        if line.label != label:
            continue
        if line.points and stamp <= line.points[-1][0]:
            return False
        line.points.append((stamp, value))
        return True
    return False
