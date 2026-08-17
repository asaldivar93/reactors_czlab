"""What the plot panels contain, and how their series are assembled.

Panels are defined by a list of ``(name, channel)`` filters - the same
shape as ``run_plots.PLOT_FILTERS`` - so adding actuator panels later is
an entry in ``PANELS``, not a rewrite. That is a stated future
requirement, which is why the shape is this rather than four hardcoded
charts.

Pure: rows in, series out. Database access and the Plotly figure live in
the page.
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

#: Browser-side ceiling for each Plotly trace. Four charts can otherwise
#: retain an unbounded number of points for an "All" query.
MAX_TRACE_POINTS = 4000


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


def downsample(
    points: list[tuple[datetime, float]],
    limit: int = MAX_TRACE_POINTS,
) -> list[tuple[datetime, float]]:
    """Reduce a time series while retaining spikes and endpoints.

    The time range is split into deterministic equal-width buckets. The
    first, last, minimum and maximum point from every bucket survive, in
    their original order. Consequently no output contains more than
    ``limit`` points.

    Parameters
    ----------
    points:
        Chronological ``(timestamp, value)`` pairs.
    limit:
        Maximum output size. At least four points are needed to preserve
        all four representatives when sampling is required.

    Raises
    ------
    ValueError
        If sampling is required and ``limit`` is smaller than four.

    """
    if len(points) <= limit:
        return list(points)
    if limit < 4:
        error_message = "A downsample limit must be at least four"
        raise ValueError(error_message)

    bucket_count = max(1, limit // 4)
    first_stamp = points[0][0]
    duration = (points[-1][0] - first_stamp).total_seconds()
    buckets: list[list[tuple[int, tuple[datetime, float]]]] = [
        [] for _ in range(bucket_count)
    ]

    for index, point in enumerate(points):
        if duration <= 0:
            bucket_index = min(
                index * bucket_count // len(points),
                bucket_count - 1,
            )
        else:
            elapsed = (point[0] - first_stamp).total_seconds()
            bucket_index = min(
                int(elapsed / duration * bucket_count),
                bucket_count - 1,
            )
        buckets[bucket_index].append((index, point))

    sampled: list[tuple[datetime, float]] = []
    for bucket in buckets:
        if not bucket:
            continue
        representatives = {
            bucket[0][0],
            bucket[-1][0],
            min(bucket, key=lambda item: (item[1][1], item[0]))[0],
            max(bucket, key=lambda item: (item[1][1], -item[0]))[0],
        }
        sampled.extend(
            point for index, point in bucket if index in representatives
        )
    return sampled


def merge_history(db_rows: list[tuple], memory_points: list[tuple]) -> list[tuple]:
    """Merge persisted and recent rows without duplicating their overlap.

    In-memory rows win when a point exists in both sources. That copy is the
    one closest to the live OPC notification and has not passed through a
    database timestamp conversion.
    """
    rows = {(row[1], row[0]): row for row in db_rows}
    rows.update({(row[1], row[0]): row for row in memory_points})
    return sorted(rows.values(), key=lambda row: (row[1], row[0]))


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
