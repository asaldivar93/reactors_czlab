"""Export the archived reactor data to a csv file."""

from __future__ import annotations

import argparse

from reactors_czlab.sql.operations import (
    query_data,
    query_experiment_data,
    row_to_csv,
)


def cli() -> None:
    """Parse the command line and write the csv."""
    parser = argparse.ArgumentParser(description="Export the archived data")
    parser.add_argument("--out", default="data.csv")
    parser.add_argument(
        "--range",
        type=float,
        default=24.0,
        help="How far back to export (ignored when --units is 'all')",
    )
    parser.add_argument(
        "--units",
        default="h",
        choices=["m", "h", "d", "all"],
    )
    parser.add_argument(
        "--experiment",
        help="Export one experiment's readings instead of a time range",
    )
    args = parser.parse_args()

    if args.experiment:
        rows = query_experiment_data(args.experiment)
    else:
        rows = query_data((args.range, args.units))
    row_to_csv(args.out, rows)
    print(f"Wrote {len(rows)} rows to {args.out}")


if __name__ == "__main__":
    cli()
