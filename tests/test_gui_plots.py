"""Tests for how plot series are assembled."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from reactors_czlab.gui.controllers.plots import (
    BIOMASS_CHANNELS,
    MAX_TRACE_POINTS,
    PANELS,
    Series,
    append_live_point,
    build_series,
    downsample,
    merge_history,
    panel_filters,
    series_label,
    window_range,
)

BASE = datetime(2026, 8, 2, 12, 0, 0)  # noqa: DTZ001 - naive, as stored


def _row(name: str, channel: str, value: float, offset: int = 0) -> tuple:
    """A data table row in COLUMNS order."""
    return (
        "ns=2;i=10",
        BASE + timedelta(seconds=offset),
        "R0",
        name,
        channel,
        value,
        None,
    )


class TestPanels:
    """The four panels the requirements name."""

    def test_every_required_panel_exists(self) -> None:
        """pH, dissolved oxygen, temperature and biomass."""
        assert {panel.key for panel in PANELS} == {
            "ph",
            "do",
            "temperature",
            "biomass",
        }

    def test_temperature_draws_both_probes(self) -> None:
        """Both the pH and DO probes report a temperature."""
        temperature = next(p for p in PANELS if p.key == "temperature")
        assert temperature.filters == (("ph", "oC"), ("do", "oC"))

    def test_only_biomass_is_selectable(self) -> None:
        """It is the one panel whose channels the operator picks."""
        assert [p.key for p in PANELS if p.selectable] == ["biomass"]

    def test_panels_are_data_not_code(self) -> None:
        """Adding an actuator panel must be an entry, not a rewrite.

        The requirements call actuator plots out as a likely future
        addition, so the panel list is a filter list of the same shape
        as run_plots.PLOT_FILTERS.
        """
        for panel in PANELS:
            assert isinstance(panel.filters, tuple)


class TestBiomassSelection:
    """Choosing which biomass channels to draw."""

    def test_all_ten_channels_are_offered(self) -> None:
        """The AS7341 publishes ten, not the eleven its docstring says."""
        assert len(BIOMASS_CHANNELS) == 10

    def test_a_single_channel_selection_works(self) -> None:
        """The requirement is single *or* multiple."""
        panel = next(p for p in PANELS if p.selectable)
        assert panel_filters(panel, ["445"]) == (("biomass", "445"),)

    def test_multiple_channels_work(self) -> None:
        """Several wavelengths on one chart."""
        panel = next(p for p in PANELS if p.selectable)
        assert panel_filters(panel, ["415", "nir"]) == (
            ("biomass", "415"),
            ("biomass", "nir"),
        )

    def test_a_fixed_panel_ignores_the_biomass_selection(self) -> None:
        """Picking biomass channels must not alter the pH chart."""
        ph = next(p for p in PANELS if p.key == "ph")
        assert panel_filters(ph, ["415"]) == (("ph", "pH"),)


class TestWindows:
    """The time window selector."""

    @pytest.mark.parametrize(
        ("label", "expected"),
        [("2 h", (2.0, "h")), ("24 h", (24.0, "h")), ("All", (0.0, "all"))],
    )
    def test_labels_map_to_a_range(
        self,
        label: str,
        expected: tuple,
    ) -> None:
        """The requirement names 24h and 2h explicitly."""
        assert window_range(label) == expected

    def test_an_unknown_window_is_refused(self) -> None:
        """Better than silently falling back to a different window."""
        with pytest.raises(KeyError):
            window_range("a fortnight")


class TestBuildSeries:
    """Grouping database rows into lines."""

    def test_one_series_per_filter(self) -> None:
        """Even a filter with no rows gets a line."""
        series = build_series(
            [_row("ph", "oC", 30.1)],
            (("ph", "oC"), ("do", "oC")),
        )
        assert [s.label for s in series] == ["ph:oC", "do:oC"]
        assert series[1].points == []

    def test_channels_with_the_same_name_do_not_merge(self) -> None:
        """Regression bait: both probes call their channel oC.

        Grouping on the channel alone would draw one line holding both
        probes' temperatures interleaved.
        """
        series = build_series(
            [_row("ph", "oC", 30.1), _row("do", "oC", 29.4, offset=1)],
            (("ph", "oC"), ("do", "oC")),
        )
        assert series[0].points == [(BASE, 30.1)]
        assert series[1].points == [(BASE + timedelta(seconds=1), 29.4)]

    def test_rows_outside_the_filters_are_dropped(self) -> None:
        """A shared query must not leak into the wrong panel."""
        series = build_series(
            [_row("ph", "pH", 7.0), _row("biomass", "445", 120.0)],
            (("ph", "pH"),),
        )
        assert len(series) == 1
        assert len(series[0].points) == 1

    def test_points_are_sorted_by_time(self) -> None:
        """A scatter on a time axis still needs ordered points."""
        series = build_series(
            [
                _row("ph", "pH", 7.2, offset=10),
                _row("ph", "pH", 7.0, offset=0),
            ],
            (("ph", "pH"),),
        )
        stamps = [stamp for stamp, _ in series[0].points]
        assert stamps == sorted(stamps)

    def test_an_empty_result_still_has_the_legend(self) -> None:
        """The chart must not change shape as data arrives."""
        series = build_series([], (("ph", "pH"),))
        assert [s.label for s in series] == ["ph:pH"]


class TestLiveTail:
    """Appending the OPC subscription's readings."""

    def test_appends_to_the_matching_series(self) -> None:
        """History from the database, tail from the subscription."""
        series = [Series("ph:pH", [(BASE, 7.0)])]

        added = append_live_point(
            series,
            "ph",
            "pH",
            BASE + timedelta(seconds=10),
            7.1,
        )

        assert added
        assert series[0].points[-1] == (BASE + timedelta(seconds=10), 7.1)

    def test_a_repeated_timestamp_is_not_added_twice(self) -> None:
        """The subscription re-notifies on every publish.

        Without this a steady reading piles up duplicate points at the
        same instant until the chart is rebuilt.
        """
        series = [Series("ph:pH", [(BASE, 7.0)])]

        assert not append_live_point(series, "ph", "pH", BASE, 7.0)
        assert len(series[0].points) == 1

    def test_an_older_point_is_not_added(self) -> None:
        """Out-of-order arrivals would draw a line going backwards."""
        series = [Series("ph:pH", [(BASE, 7.0)])]

        added = append_live_point(
            series,
            "ph",
            "pH",
            BASE - timedelta(seconds=10),
            6.9,
        )

        assert not added

    def test_a_channel_that_is_not_plotted_is_ignored(self) -> None:
        """Every subscription notification reaches this function."""
        series = [Series("ph:pH")]
        assert not append_live_point(series, "biomass", "445", BASE, 120.0)


class TestSeriesLabel:
    """Legend entries."""

    def test_carries_both_parts(self) -> None:
        """Two identical legend entries would otherwise be possible."""
        assert series_label("ph", "oC") == "ph:oC"
        assert series_label("do", "oC") != series_label("ph", "oC")


class TestDownsample:
    """Bounding browser data without hiding important measurements."""

    def test_never_exceeds_the_cap_and_preserves_extrema(self) -> None:
        """Regression: a long window made every Plotly trace unbounded."""
        points = [
            (BASE + timedelta(seconds=index), float(index % 37))
            for index in range(MAX_TRACE_POINTS * 2)
        ]
        points[1777] = (points[1777][0], -1000.0)
        points[6333] = (points[6333][0], 2000.0)

        sampled = downsample(points)

        assert len(sampled) <= MAX_TRACE_POINTS
        assert sampled[0] == points[0]
        assert sampled[-1] == points[-1]
        assert points[1777] in sampled
        assert points[6333] in sampled

    def test_is_deterministic(self) -> None:
        """The same query produces the same points and visual shape."""
        points = [
            (BASE + timedelta(seconds=index), float(index % 11))
            for index in range(100)
        ]
        assert downsample(points, 20) == downsample(points, 20)

    def test_small_series_is_unchanged(self) -> None:
        """There is no reason to alter data already below the cap."""
        points = [(BASE, 1.0), (BASE + timedelta(seconds=1), 2.0)]
        assert downsample(points, 4) == points


class TestMergeHistory:
    """Joining PostgreSQL history to the recent OPC buffer."""

    def test_deduplicates_timestamp_and_nodeid(self) -> None:
        """The overlap between the two sources is rendered once."""
        persisted = _row("ph", "pH", 7.0)
        recent = (*persisted[:5], 7.1, None)

        merged = merge_history([persisted], [recent])

        assert merged == [recent]

    def test_same_timestamp_from_two_nodes_survives(self) -> None:
        """Two probe channels can legitimately share one timestamp."""
        first = _row("ph", "oC", 30.0)
        second = ("ns=2;i=11", *first[1:3], "do", "oC", 29.0, None)
        assert len(merge_history([first], [second])) == 2


class TestChartOptions:
    """The Plotly figure dictionary the page builds."""

    def test_the_x_axis_is_a_real_time_axis(self) -> None:
        """The requirement is dates and times, not elapsed minutes."""
        from reactors_czlab.gui.pages.plots import _figure

        figure = _figure(PANELS[0], [])
        assert figure["layout"]["xaxis"]["type"] == "date"

    def test_the_window_pins_the_left_edge_of_the_axis(self) -> None:
        """Regression: the window selector appeared to do nothing.

        Without a min, ECharts scales the axis to whatever points
        exist, so a freshly opened page holding two live readings drew a
        multi-day axis while the selector said "2 h".
        """
        from reactors_czlab.gui.pages.plots import _figure

        cutoff = BASE - timedelta(hours=2)
        figure = _figure(PANELS[0], [], cutoff)

        assert figure["layout"]["xaxis"]["range"][0] == cutoff.isoformat()
        assert figure["layout"]["xaxis"]["range"][1] is None
        assert figure["layout"]["xaxis"]["autorange"] == "max"

    def test_the_all_window_leaves_the_axis_free(self) -> None:
        """"All" has no cutoff, so the axis must not be pinned."""
        from reactors_czlab.gui.pages.plots import _figure

        assert "range" not in _figure(PANELS[0], [], None)["layout"]["xaxis"]

    def test_series_are_scatter_on_the_time_axis(self) -> None:
        """The requirement names scatter plots."""
        from reactors_czlab.gui.pages.plots import _figure

        series = build_series([_row("ph", "pH", 7.0)], (("ph", "pH"),))
        figure = _figure(PANELS[0], series)

        assert figure["data"][0]["type"] == "scattergl"
        assert figure["data"][0]["x"] == [BASE.isoformat()]
        assert figure["data"][0]["y"] == [7.0]

    def test_every_series_is_in_the_legend(self) -> None:
        """Two probes on the temperature chart need two legend entries."""
        from reactors_czlab.gui.pages.plots import _figure

        temperature = next(p for p in PANELS if p.key == "temperature")
        series = build_series([], temperature.filters)
        figure = _figure(temperature, series)

        assert [trace["name"] for trace in figure["data"]] == [
            "ph:oC",
            "do:oC",
        ]


class TestIncrementalRendering:
    """Whole figures rebuild only when their query shape changes."""

    async def test_idle_poll_does_not_update_the_chart(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression: an idle plot page redrew all four charts every poll."""
        from reactors_czlab.gui.pages import plots

        class Chart:
            def __init__(self) -> None:
                self.calls: list[tuple] = []

            def run_plot_method(self, *args: object) -> None:
                self.calls.append(args)

        chart = Chart()
        state = {
            "window": "2 h",
            "biomass": ["445"],
            "revision": ("R0", "2 h", ("445",), 4),
            "series": {"ph": [Series("ph:pH", [(BASE, 7.0)])]},
            "charts": {"ph": chart},
        }
        fake_state = SimpleNamespace(
            connected=True,
            generation=4,
            reading=lambda reactor, name, channel: (7.0, BASE),
        )
        monkeypatch.setattr(plots, "STATE", fake_state)

        await plots._poll_tail("R0", state)

        assert chart.calls == []

    async def test_new_point_extends_one_trace_with_the_cap(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A sample appears within one poll without a figure rebuild."""
        from reactors_czlab.gui.pages import plots

        class Chart:
            def __init__(self) -> None:
                self.calls: list[tuple] = []

            def run_plot_method(self, *args: object) -> None:
                self.calls.append(args)

        chart = Chart()
        state = {
            "window": "2 h",
            "biomass": ["445"],
            "revision": ("R0", "2 h", ("445",), 4),
            "series": {"ph": [Series("ph:pH", [(BASE, 7.0)])]},
            "charts": {"ph": chart},
        }
        new_stamp = BASE + timedelta(seconds=10)
        fake_state = SimpleNamespace(
            connected=True,
            generation=4,
            reading=lambda reactor, name, channel: (7.1, new_stamp),
        )
        monkeypatch.setattr(plots, "STATE", fake_state)

        await plots._poll_tail("R0", state)

        [call] = chart.calls
        assert call[0] == "extendTraces"
        assert call[-1] == MAX_TRACE_POINTS
        assert state["series"]["ph"][0].points[-1] == (new_stamp, 7.1)

    def test_revision_names_every_full_rebuild_trigger(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Only reactor, window, channel selection and generation matter."""
        from reactors_czlab.gui.pages import plots

        monkeypatch.setattr(plots.STATE, "generation", 8)
        state = {"window": "2 h", "biomass": ["445"], "unrelated": 1}
        baseline = plots._revision("R0", state)
        state["unrelated"] = 2
        assert plots._revision("R0", state) == baseline
        assert plots._revision("R1", state) != baseline
        state["window"] = "6 h"
        assert plots._revision("R0", state) != baseline
