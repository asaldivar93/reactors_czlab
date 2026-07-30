"""Store and retrieve reactor readings from PostgreSQL.

Both third-party dependencies are optional. ``psycopg`` has no wheel for
32 bit Raspberry Pi OS, and the GUI has to import and run with the
database features disabled rather than failing at import - so the import
is guarded and every public function checks ``require_psycopg()`` first.
``polars`` is needed by one function and is imported inside it.
"""

from __future__ import annotations

import csv
import getpass
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    import psycopg
except ImportError:  # pragma: no cover - depends on the install
    psycopg = None

if TYPE_CHECKING:
    from psycopg import Connection

#: Whether the database features are available in this install.
PSYCOPG_AVAILABLE = psycopg is not None

_logger = logging.getLogger("client.sql")

# Connection settings, overridable without touching the code.
DB_NAME = os.environ.get("BIOREACTOR_DB_NAME", "bioreactor_db")
DB_USER = os.environ.get("BIOREACTOR_DB_USER") or getpass.getuser()
DB_HOST = os.environ.get("BIOREACTOR_DB_HOST")
DB_PORT = os.environ.get("BIOREACTOR_DB_PORT")
DB_PASSWORD = os.environ.get("BIOREACTOR_DB_PASSWORD")

COLUMNS = (
    "node_id",
    "date",
    "reactor",
    "name",
    "channel",
    "value",
    "experiment_name",
)

INSERT_DATA = (
    "INSERT INTO data "
    "(node_id, date, reactor, name, channel, value, experiment_name) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)

SELECT_DATA = (
    "SELECT node_id, date, reactor, name, channel, value, experiment_name "
    "FROM data"
)


class SqlError(Exception):
    """Custom sql error."""


def require_psycopg() -> None:
    """Refuse to go further when psycopg is not installed.

    Raises
    ------
    SqlError
        When the install has no psycopg. The message names the extra to
        install, because it is shown to the operator in the GUI.

    """
    if not PSYCOPG_AVAILABLE:
        error_message = (
            "psycopg is not installed, so the database features are "
            "unavailable; install the 'client' extra"
        )
        raise SqlError(error_message)


def polars_schema() -> dict:
    """Column types of the ``data`` table, for polars.

    A function rather than a module constant so ``polars`` - needed by
    one consumer and absent from a minimal Pi install - is imported only
    when it is actually used.

    Raises
    ------
    SqlError
        When polars is not installed.

    """
    try:
        import polars as pl
    except ImportError as err:
        error_message = (
            "polars is not installed; install the 'client' extra"
        )
        raise SqlError(error_message) from err

    return {
        "node_id": pl.String,
        "date": pl.Datetime("ms"),
        "reactor": pl.String,
        "name": pl.String,
        "channel": pl.String,
        "value": pl.Float64,
        "experiment_name": pl.String,
    }


def connect_to_db() -> Connection:
    """Establish a connection to the PostgreSQL database.

    Raises
    ------
    SqlError
        If psycopg is not installed, or if the database is unreachable.

    """
    require_psycopg()
    params = {"dbname": DB_NAME, "user": DB_USER}
    if DB_HOST:
        params["host"] = DB_HOST
    if DB_PORT:
        params["port"] = DB_PORT
    if DB_PASSWORD:
        params["password"] = DB_PASSWORD

    try:
        return psycopg.connect(**params)
    except psycopg.Error as err:
        error_message = f"Error connecting to database {DB_NAME} as {DB_USER}"
        raise SqlError(error_message) from err


def store_data(
    connection: Connection,
    node_id: str,
    info: dict,
) -> None:
    """Insert one reading into the data table.

    The caller owns the connection so it can be reused across inserts.

    Raises
    ------
    SqlError
        If the insert failed. The transaction is rolled back first.

    """
    values = (
        node_id,
        info["timestamp"].isoformat(timespec="milliseconds"),
        info["reactor"],
        info["name"],
        info["channel"],
        info["value"],
        info.get("experiment_name"),
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(INSERT_DATA, values)
        connection.commit()
    except psycopg.Error as err:
        try:
            connection.rollback()
        except psycopg.Error:
            _logger.debug("Rollback failed", exc_info=True)
        error_message = f"Error inserting {values}"
        raise SqlError(error_message) from err
    else:
        _logger.debug("Commit to db: %s", values)


def get_date_filter_range(time_range: float, units: str) -> datetime | None:
    """Return the cutoff date based on filter option.

    Parameters
    ----------
    time_range:
        A float with the desired time range
    units:
        A time unit ("m": minutes, "h": hours, "d": days, "all": no cutoff)

    Raises
    ------
    ValueError
        If the units are not recognised.

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
            error_message = (
                f"Invalid time units: {units} (valid: 'm', 'h', 'd', 'all')"
            )
            raise ValueError(error_message)


def query_data(time_range: tuple[float, str]) -> list:
    """Query the sql database by date.

    Raises
    ------
    SqlError
        If psycopg is not installed, or if the query failed.

    """
    require_psycopg()
    cutoff = get_date_filter_range(*time_range)

    query = SELECT_DATA
    params: tuple = ()
    if cutoff is not None:
        # "all" has no cutoff at all: adding "date >= NULL" would match
        # nothing instead of everything.
        query += " WHERE date >= %s"
        params = (cutoff,)
    query += " ORDER BY date"

    connection = connect_to_db()
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()
    except psycopg.Error as err:
        error_message = "Error during get operation"
        raise SqlError(error_message) from err
    finally:
        connection.close()


def row_to_csv(out_name: str, rows: list) -> None:
    """Save sql queries to csv."""
    with Path(out_name).open(mode="w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(COLUMNS)
        writer.writerows(rows)


def rows_to_polars(rows: list) -> Any:
    """Export sql queries to a polars dataframe.

    The schema is fixed by the data table, so an empty result set still
    produces a dataframe with the right columns.

    Raises
    ------
    SqlError
        When polars is not installed.

    """
    # polars_schema() first: it is the one that turns a missing polars
    # into an SqlError the GUI can show, rather than an ImportError.
    schema = polars_schema()
    import polars as pl

    return pl.DataFrame(rows, schema=schema, orient="row")
