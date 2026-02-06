from __future__ import annotations

import csv
import logging
import os
import pwd
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import psycopg

from reactors_czlab.server_info import VERBOSE

if TYPE_CHECKING:
    from psycopg import Connection, Cursor

    from reactors_czlab.core.data import PhysicalInfo

_logger = logging.getLogger("client.sql")

username = pwd.getpwuid(os.getuid())[0]
dbname = "bioreactor_db"


class SqlError(Exception):
    """Custom sql error."""


def connect_to_db() -> Connection:
    """Establish a connection to the PostgreSQL database."""
    return psycopg.connect(
        dbname=dbname,
        user=username,
    )


def create_experiment(
    name: str,
    date: datetime,
    reactors: list[str],
    volume: float,
) -> None:
    """Create a record for the experiment."""
    try:
        connection = connect_to_db()
    except psycopg.Error as err:
        error_message = "Error connecting to database"
        raise SqlError(error_message) from err
    date_str = date.isoformat(timespec="milliseconds")
    query = "INSERT INTO experiment (name, date, reactors, volume) \
             VALUES (%s, %s, %s, %s) RETURNING id"
    values = (name, date_str, ",".join(reactors), volume)
    cursor = connection.cursor()
    if not experiment_exist(cursor, name):
        _logger.info(f"Creating experiment {name}")
        cursor.execute(query, values)
        connection.commit()


def experiment_exist(cursor: Cursor, name: str) -> bool:
    """Evaluate if there is a record for the experiment."""
    cursor.execute("SELECT name FROM experiment WHERE name = %s", (name,))
    return cursor.fetchone() is not None


def get_experiment_id(cursor: Cursor, name: str) -> str:
    """Get experiment id from name."""
    cursor.execute(
        "SELECT id FROM experiment WHERE name = %s",
        (name,),
    )
    row = cursor.fetchone()
    if row is None:
        raise SqlError("Experiment does not exist")
    return row[0]


def get_reactors(name: str) -> list[str]:
    """Return reactors in an experiment."""
    try:
        connection = connect_to_db()
    except psycopg.Error as err:
        error_message = "Error connecting to database"
        raise SqlError(error_message) from err
    cursor = connection.cursor()
    cursor.execute(
        "SELECT reactors FROM experiment WHERE name = %s",
        (name,),
    )
    row = cursor.fetchone()
    if row is None:
        error_message = "Experiment has no reactors?"
        raise SqlError(error_message)
    return row[0].split(",")


def store_data(
    data: PhysicalInfo,
    reactor_id: str,
    experiment_name: str,
    timestamp: datetime,
) -> None:
    """Insert data into respective PostgreSQL tables based on the sensor."""
    try:
        connection = connect_to_db()
    except psycopg.Error as err:
        error_message = "Error connecting to database"
        raise SqlError(error_message) from err

    cursor = connection.cursor()
    exp_id = get_experiment_id(cursor, experiment_name)

    # Extract sensor data
    model = data.model.lower()
    channel = data.channels[0]
    value = channel.value
    units = channel.units
    calibration = channel.calibration.file if channel.calibration else None
    datetime = timestamp.isoformat(timespec="milliseconds")

    try:
        insert_map = (
            "INSERT INTO data \
            (experiment_id, date, reactor, model, calibration, units, value) \
            VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (exp_id, datetime, reactor_id, model, calibration, units, value),
        )
        query, values = insert_map
        if VERBOSE:
            _logger.debug(data)
            _logger.debug(query)
            _logger.debug(values)
        cursor.execute(query, values)
        connection.commit()

    except psycopg.Error as err:
        error_message = "Error during insert operation."
        raise SqlError(error_message) from err
    except KeyError as err:
        error_message = f"Unknown model type: {model}"
        raise SqlError(error_message) from err
    finally:
        cursor.close()
        connection.close()


def get_date_filter_range(time_range: float, units: str) -> datetime | None:
    """Return the cutoff date based on filter option.

    Parameters
    ----------
    time_range:
        A float with the desired time range
    units:
        A time unit ("m":minutes, "h":hours, "d":days)

    """
    now = datetime.now()
    units = units.strip().lower()

    match units:
        case "m":
            return now - timedelta(minutes=time_range)
        case "h":
            return now - timedelta(hours=time_range)
        case "d":
            return now - timedelta(days=time_range)
        case "all":
            return None
        case _:
            error_message = f"Invalid time units: {units} \
                (valid: 'm', 'h', 'd', 'all')"
            raise ValueError(error_message)


def get_data(experiment_name: str, time_filter: tuple[float, str]) -> list:
    """Query the SQL database by date."""
    try:
        connection = connect_to_db()
    except psycopg.Error as err:
        error_message = "Error connecting to database"
        raise SqlError(error_message) from err

    cursor = connection.cursor()

    experiment_id = get_experiment_id(cursor, experiment_name)

    # Determine date filter
    time, unit = time_filter
    cutoff_date = get_date_filter_range(time, unit)
    base_conditions = "experiment_id = %s"
    params = [experiment_id]

    if cutoff_date:
        base_conditions += " AND date >= %s"
        params.append(cutoff_date.isoformat())

    # Queries (with optional date filter)
    try:
        query = f"SELECT 'data' \
            AS source_table, date, reactor, model, calibration, units, value \
            FROM data \
            WHERE {base_conditions}"
        cursor.execute(query, tuple(params))
        all_rows = cursor.fetchall()
    except psycopg.Error as err:
        error_message = "Error during get operation"
        raise SqlError(error_message) from err
    else:
        return all_rows


def row_to_csv(out_name: str, rows: list) -> None:
    """Save sql queries to csv."""
    with Path(out_name).open(mode="w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(
            [
                "source_table",
                "date",
                "reactor",
                "model",
                "calibration",
                "units",
                "value",
            ],
        )  # Header
        writer.writerows(rows)


def rows_to_polars(rows: list) -> pl.DataFrame:
    """Export sql queries to polars dataframe."""
    columns = [
        "source_table",
        "date",
        "reactor",
        "model",
        "calibration",
        "units",
        "value",
    ]
    schema = {col: type(rows[0][i]) for i, col in enumerate(columns)}
    schema["calibration"] = str
    return pl.DataFrame(rows, schema=schema)
