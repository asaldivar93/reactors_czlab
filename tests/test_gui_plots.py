"""Tests for how plot series are assembled."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from reactors_czlab.gui.controllers.plots import (
    BIOMASS_CHANNELS,
    PANELS,
    Series,
    append_live_point,
    build_series,
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
