from __future__ import annotations

from typing import Any

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import polars as pl
from matplotlib.animation import FuncAnimation
from matplotlib.figure import Figure

from reactors_czlab.sql.operations import get_data, get_reactors, rows_to_polars


class Plotter:
    def __init__(
        self,
        experiment_name: str,
        time_filter: tuple[float, str],
    ) -> None:
        self.experiment_name = experiment_name
        self.time_filter = time_filter
        self.reactors = get_reactors(experiment_name)
        figure, plots = self.setup_plots()
        self.figure = figure
        self.plots = plots

    def setup_plots(self) -> tuple[Figure, dict[str, Any]]:
        """Initialize plots."""
        fig, axs = plt.subplots(2, 2, figsize=(12, 8))
        fig.suptitle("Live Sensor Data", fontsize=16)
        axs = axs.flatten()

        titles = [
            "Visiferm",
            "Arcph",
            "Biomass",
            "Temperature",
        ]
        plots = {}

        for ax, title in zip(axs, titles):
            lines = {}
            # Date Formatter
            locator = mdates.AutoDateLocator(minticks=3, maxticks=7)
            formatter = mdates.ConciseDateFormatter(locator)
            ax.xaxis.set_major_locator(locator)
            ax.xaxis.set_major_formatter(formatter)

            # Plot labels
            ax.set_title(title)
            ax.set_xlabel("Date")
            ax.set_ylabel("Value")

            for reactor in self.reactors:
                (line,) = ax.plot([], [], label=reactor, marker=".")
                lines[reactor] = line
            ax.legend(loc="upper left")
            plots[title.lower()] = (ax, lines)

        plt.tight_layout(rect=[0, 0, 1, 0.95])
        return fig, plots

    def get_data(self) -> pl.DataFrame:
        """Export data to polars.DataFrame."""
        all_rows = get_data(self.experiment_name, self.time_filter)
        return rows_to_polars(all_rows)


def filter_df(all_df: pl.DataFrame, table: str, reactor: str) -> pl.DataFrame:
    """Filter the dataframe by model and reactor."""
    units_map = {"arcph": "pH", "visiferm": "ppm", "biomass": "445"}
    if table != "temperature":
        units = units_map.get(table)
        table_df = all_df.filter(
            pl.col("model") == table,
            pl.col("reactor") == reactor,
            pl.col("units") == units,
        )
    else:
        table_df = all_df.filter(
            pl.col("model").is_in(["visiferm", "arcph"]),
            pl.col("units") == "oC",
        )
    return table_df.sort("date")


def update(frame, plotter: Plotter):
    all_df = plotter.get_data()
    for table, (ax, lines) in plotter.plots.items():
        for reactor in plotter.reactors:
            line = lines[reactor]
            table_df = filter_df(all_df, table, reactor)
            dates = table_df["date"].to_numpy()
            values = table_df["value"].to_numpy()

            # Update plot
            line.set_data(dates, values)
            ax.relim()
            ax.autoscale_view()


if __name__ == "__main__":
    experiment_name = "test"
    time_filter = (24, "h")
    plotter = Plotter(experiment_name, time_filter)

    ani = FuncAnimation(plotter.figure, update, fargs=(plotter,), interval=1000)
    plt.show()
