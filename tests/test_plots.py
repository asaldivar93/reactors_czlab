from __future__ import annotations

from datetime import datetime
from typing import Any

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import polars as pl
from matplotlib.animation import FuncAnimation
from matplotlib.figure import Figure

from reactors_czlab.sql.operations import get_data, rows_to_polars


# Live plotting using matplotlib
class Plotter:
    def __init__(
        self,
        experiment_name: str,
        time_filter: tuple[float, str],
    ) -> None:
        self.experiment_name = experiment_name
        self.time_filter = time_filter
        figure, plots = self.setup_plots()
        self.figure = figure
        self.plots = plots

    def setup_plots(self) -> tuple[Figure, dict[str, Any]]:
        """Initialize plots."""
        fig, axs = plt.subplots(2, 3, figsize=(12, 8))
        fig.suptitle("Live Sensor Data", fontsize=16)
        axs = axs.flatten()

        titles = [
            "Visiferm",
            "Arcph",
            "Analog",
            "Actuator",
            "Digital",
            "Temperature",
        ]
        plots = {}

        for ax, title in zip(axs, titles):
            # Date Formatter
            locator = mdates.AutoDateLocator(minticks=3, maxticks=7)
            formatter = mdates.ConciseDateFormatter(locator)
            ax.xaxis.set_major_locator(locator)
            ax.xaxis.set_major_formatter(formatter)

            # Plot labels
            ax.set_title(title)
            ax.set_xlabel("Date")
            ax.set_ylabel("Value")

            (line,) = ax.plot([], [], label=title, marker="o")
            ax.legend(loc="upper left")
            plots[title.lower()] = (ax, line)

        plt.tight_layout(rect=[0, 0, 1, 0.95])
        return fig, plots

    def get_data(self):
        all_rows = get_data(self.experiment_name, self.time_filter)
        return rows_to_polars(all_rows)


def update(frame, plotter: Plotter):
    all_df = plotter.get_data()
    for table, (ax, line) in plotter.plots.items():
        if table != "Temperature":
            table_df = all_df.filter(pl.col("source_table") == table)
        else:
            table_df = all_df.filter(
                pl.col("source_table") == "visiferm",
                pl.col("units") == "oC",
            )
        if table_df.is_empty():
            error_message = "Dataframe is empty"
            raise ValueError(error_message)

        # Ensuring date column is sorted
        table_df = table_df.sort("date")
        dates = table_df["date"].to_numpy()
        values = table_df["value"].to_numpy()

        # Converting Polars Date to matplotlib date numbers
        dates = [datetime.fromisoformat(d) for d in dates]

        # Update plot
        line.set_data(dates, values)
        ax.relim()
        ax.autoscale_view()


if __name__ == "__main__":
    experiment_name = "test"
    time_filter = (24, "h")
    plotter = Plotter(experiment_name, time_filter)

    ani = FuncAnimation(plotter.figure, update, fargs=(plotter,), interval=3000)
    plt.show()
