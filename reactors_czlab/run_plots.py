"""Live plots of the archived reactor data."""

from __future__ import annotations

import argparse
from typing import Any

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import polars as pl
from matplotlib.animation import FuncAnimation
from matplotlib.figure import Figure

from reactors_czlab.sql.operations import query_data, rows_to_polars

# Each subplot selects rows by the (name, channel) pair the client stored,
# which comes from the OPC browse name "<reactor>:<name>:<channel>".
PLOT_FILTERS: dict[str, tuple[str, str]] = {
    "visiferm": ("do", "ppm"),
    "arcph": ("ph", "pH"),
    "biomass": ("biomass", "445"),
}
TEMPERATURE_SOURCES = ["ph", "do"]
TITLES = ["Visiferm", "Arcph", "Biomass", "Temperature"]

DEFAULT_REACTORS = ["R0", "R1", "R2"]


class Plotter:
    """Hold the figure and the axes for the live plots."""

    def __init__(
        self,
        time_filter: tuple[float, str],
        reactors: list[str],
    ) -> None:
        """Build the figure.

        Parameters
        ----------
        time_filter:
            (value, units) passed to query_data
        reactors:
            Reactor ids to draw one line each for

        """
        self.time_filter = time_filter
        self.reactors = reactors
        self.figure, self.plots = self.setup_plots()

    def setup_plots(self) -> tuple[Figure, dict[str, Any]]:
        """Initialize plots."""
        fig, axs = plt.subplots(2, 2, figsize=(12, 8))
        fig.suptitle("Live Sensor Data", fontsize=16)
        axs = axs.flatten()

        plots = {}
        for ax, title in zip(axs, TITLES, strict=True):
            locator = mdates.AutoDateLocator(minticks=3, maxticks=7)
            ax.xaxis.set_major_locator(locator)
            ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))

            ax.set_title(title)
            ax.set_xlabel("Date")
            ax.set_ylabel("Value")

            lines = {}
            for reactor in self.reactors:
                (line,) = ax.plot([], [], label=reactor, marker=".")
                lines[reactor] = line
            ax.legend(loc="upper left")
            plots[title.lower()] = (ax, lines)

        plt.tight_layout(rect=[0, 0, 1, 0.95])
        return fig, plots

    def get_data(self) -> pl.DataFrame:
        """Export the archived data to a polars.DataFrame."""
        return rows_to_polars(query_data(self.time_filter))


def filter_df(all_df: pl.DataFrame, table: str, reactor: str) -> pl.DataFrame:
    """Filter the dataframe down to one line of one subplot."""
    if table == "temperature":
        table_df = all_df.filter(
            pl.col("reactor") == reactor,
            pl.col("name").is_in(TEMPERATURE_SOURCES),
            pl.col("channel") == "oC",
        )
    else:
        name, channel = PLOT_FILTERS[table]
        table_df = all_df.filter(
            pl.col("reactor") == reactor,
            pl.col("name") == name,
            pl.col("channel") == channel,
        )
    return table_df.sort("date")


def update(frame: int, plotter: Plotter) -> None:
    """Redraw every line from a fresh query."""
    all_df = plotter.get_data()
    for table, (ax, lines) in plotter.plots.items():
        for reactor in plotter.reactors:
            table_df = filter_df(all_df, table, reactor)
            lines[reactor].set_data(
                table_df["date"].to_numpy(),
                table_df["value"].to_numpy(),
            )
        ax.relim()
        ax.autoscale_view()


def cli() -> None:
    """Parse the command line and show the live plots."""
    parser = argparse.ArgumentParser(description="Plot the archived data")
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--reactors", nargs="+", default=DEFAULT_REACTORS)
    parser.add_argument("--interval-ms", type=int, default=1000)
    args = parser.parse_args()

    plotter = Plotter((args.hours, "h"), args.reactors)
    # Keep a reference: FuncAnimation is garbage collected without one.
    animation = FuncAnimation(
        plotter.figure,
        update,
        fargs=(plotter,),
        interval=args.interval_ms,
        cache_frame_data=False,
    )
    plt.show()
    del animation


if __name__ == "__main__":
    cli()
