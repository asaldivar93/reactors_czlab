"""Store and retrieve reactor readings from PostgreSQL.

Importing this module never requires psycopg or polars. The Pi runs the
server and the GUI from the same install, and neither the archiver nor
any GUI page needs polars; psycopg itself is a pure-Python wheel but
needs libpq present at runtime, which not every machine has. Both are
therefore optional at import and required at the point of use, so a
missing database disables the features that depend on it - recording,
experiments and plot history - with a reason to show an operator,
rather than taking the process down at startup.
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


_logger = logging.getLogger("client.sql")

#: Whether the database features can work at all. False when psycopg is
#: not installed *or* its libpq is missing - psycopg raises ImportError
#: for both, and from a caller's point of view they are the same
#: problem.
PSYCOPG_AVAILABLE = psycopg is not None

#: What to tell an operator when it is not.
NO_PSYCOPG_REASON = (
    "psycopg is not available: install the client extra and make sure "
    "libpq is present (apt install libpq5)"
)

# Connection settings, overridable without touching the code.
DB_NAME = os.environ.get("BIOREACTOR_DB_NAME", "bioreactor_db")
DB_USER = os.environ.get("BIOREACTOR_DB_USER") or getpass.getuser()
DB_HOST = os.environ.get("BIOREACTOR_DB_HOST")
DB_PORT = os.environ.get("BIOREACTOR_DB_PORT")
DB_PASSWORD = os.environ.get("BIOREACTOR_DB_PASSWORD")

#: Latest migration required by this build. Versions are zero-padded so their
#: filename and lexical order are the same.
SCHEMA_VERSION = "0001"

COLUMNS = (
    "node_id",
    "date",
    "reactor",
    "name",
    "channel",
    "value",
    "experiment_name",
)

_COLUMN_LIST = ", ".join(COLUMNS)
_PLACEHOLDERS = ", ".join(["%s"] * len(COLUMNS))

INSERT_DATA = f"INSERT INTO data ({_COLUMN_LIST}) VALUES ({_PLACEHOLDERS})"

SELECT_DATA = f"SELECT {_COLUMN_LIST} FROM data"

#: Advisory lock serialising start_experiment's check-and-claim. An
#: arbitrary but fixed key: PostgreSQL advisory locks are a shared
#: namespace across the database, so it only has to be one nothing else
#: in this application uses.
EXPERIMENT_LOCK_KEY = 0x52454143  # "REAC"


class SqlError(Exception):
    """Custom sql error."""


def polars_schema() -> dict:
    """The dataframe schema for a ``data`` query, built on demand.

    polars is imported here rather than at module scope so that the
    archiver and the GUI - neither of which builds a dataframe - can run
    on a machine that has no polars, which is every Raspberry Pi.

    Raises
    ------
    SqlError
        If polars is not installed.

    """
    try:
        import polars as pl
    except ImportError as err:
        error_message = (
            "polars is not available: install the client extra to use "
            "dataframe exports"
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


def require_psycopg() -> None:
    """Refuse to go further when the database driver is missing.

    Every public function that touches the database calls this first, so
    the failure is a single recognisable SqlError with an actionable
    message instead of a NoneType AttributeError from wherever psycopg
    was first dereferenced.

    Raises
    ------
    SqlError
        If psycopg could not be imported.

    """
    if not PSYCOPG_AVAILABLE:
        raise SqlError(NO_PSYCOPG_REASON)


def connect_to_db() -> Connection:
    """Establish a connection to the PostgreSQL database.

    Raises
    ------
    SqlError
        If psycopg is unavailable or the database is unreachable.

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


def check_schema() -> str | None:
    """Return why the database schema is incompatible, if it is.

    Returns
    -------
    str or None
        ``None`` when the migration table records the version required by
        this build, otherwise an actionable operator-facing reason.

    Raises
    ------
    SqlError
        If psycopg is unavailable or the compatibility query fails.

    """
    require_psycopg()
    versions = _applied_schema_versions()
    if versions is None:
        return (
            f"database has no schema_migrations table; this build needs "
            f"{SCHEMA_VERSION}; run reactors-db-migrate"
        )
    current = max(versions, default="none")
    if current != SCHEMA_VERSION:
        return (
            f"database is at {current}, this build needs {SCHEMA_VERSION}; "
            "run reactors-db-migrate"
        )
    return None


def _applied_schema_versions() -> list[str] | None:
    """Read applied versions, or None when the version table is absent."""
    connection = connect_to_db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.schema_migrations')")
            row = cursor.fetchone()
            if row is None or row[0] is None:
                return None
            cursor.execute("SELECT version FROM schema_migrations")
            return [row[0] for row in cursor.fetchall()]
    except psycopg.Error as err:
        error_message = "Could not check the database schema version"
        raise SqlError(error_message) from err
    finally:
        connection.close()


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
        # Absent for a client that predates experiment tagging, and
        # None for a reading taken outside any experiment. Both record.
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
        If psycopg is unavailable or the query failed.

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

    return _fetch(query, params)


def query_series(
    reactor: str,
    channels: list[tuple[str, str]],
    time_range: tuple[float, str],
) -> list:
    """Fetch the history of some channels of one reactor.

    What the plots need: the readings behind a set of
    ``(name, channel)`` filters over a window, rather than the whole
    table for a date range.

    Parameters
    ----------
    reactor:
        The reactor id, e.g. ``R0``.
    channels:
        ``(name, channel)`` pairs, e.g. ``[("ph", "pH")]``. Both parts
        are needed: two sensors on one reactor both have a channel
        called ``oC``.
    time_range:
        ``(amount, units)`` as understood by ``get_date_filter_range``.

    Raises
    ------
    SqlError
        If psycopg is unavailable or the query failed.

    """
    require_psycopg()
    if not channels:
        return []

    cutoff = get_date_filter_range(*time_range)

    pairs = " OR ".join(["(name = %s AND channel = %s)"] * len(channels))
    query = f"{SELECT_DATA} WHERE reactor = %s AND ({pairs})"
    params: list = [reactor]
    for name, channel in channels:
        params.extend((name, channel))
    if cutoff is not None:
        query += " AND date >= %s"
        params.append(cutoff)
    query += " ORDER BY date"

    return _fetch(query, tuple(params))


def query_experiment_data(name: str) -> list:
    """Every reading tagged with an experiment, oldest first.

    Raises
    ------
    SqlError
        If psycopg is unavailable or the query failed.

    """
    require_psycopg()
    return _fetch(
        f"{SELECT_DATA} WHERE experiment_name = %s ORDER BY date",
        (name,),
    )


def create_experiment(name: str, reactors: list[str]) -> None:
    """Record a new experiment, not yet started.

    Raises
    ------
    SqlError
        If psycopg is unavailable, the name is already taken, the name
        is blank or no reactors were given.

    """
    require_psycopg()
    if not name.strip():
        error_message = "An experiment needs a name"
        raise SqlError(error_message)
    if not reactors:
        error_message = f"Experiment {name} needs at least one reactor"
        raise SqlError(error_message)

    _execute(
        "INSERT INTO experiments (name, reactors) VALUES (%s, %s)",
        (name, list(reactors)),
        conflict_message=f"An experiment called {name} already exists",
    )


def start_experiment(name: str) -> list[str]:
    """Start an experiment and claim its reactors.

    A reactor belongs to at most one running experiment, but several
    experiments may run at once over disjoint reactor sets.

    The check and the update share one transaction held under an
    advisory lock. Without it two operators starting experiments at the
    same moment can both read "no overlap" before either writes, and
    both claim the same reactor - the readings would then be tagged with
    whichever experiment the archiver happened to see.

    Returns
    -------
    list[str]
        The reactors now claimed by this experiment.

    Raises
    ------
    SqlError
        If psycopg is unavailable, the experiment is unknown, it is
        already running, or one of its reactors is busy.

    """
    require_psycopg()
    connection = connect_to_db()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                (EXPERIMENT_LOCK_KEY,),
            )
            cursor.execute(
                "SELECT reactors, start_date, end_date FROM experiments "
                "WHERE name = %s",
                (name,),
            )
            row = cursor.fetchone()
            if row is None:
                error_message = f"No experiment called {name}"
                raise SqlError(error_message)

            reactors, start_date, end_date = row
            if start_date is not None and end_date is None:
                error_message = f"Experiment {name} is already running"
                raise SqlError(error_message)

            cursor.execute(
                "SELECT name, reactors FROM experiments "
                "WHERE start_date IS NOT NULL AND end_date IS NULL",
            )
            for other, other_reactors in cursor.fetchall():
                clash = sorted(set(reactors) & set(other_reactors))
                if clash:
                    error_message = (
                        f"{', '.join(clash)} already running in "
                        f"experiment {other}"
                    )
                    raise SqlError(error_message)

            cursor.execute(
                "UPDATE experiments SET start_date = %s, end_date = NULL "
                "WHERE name = %s",
                (datetime.now(), name),
            )
        connection.commit()
    except psycopg.Error as err:
        connection.rollback()
        error_message = f"Error starting experiment {name}"
        raise SqlError(error_message) from err
    except SqlError:
        connection.rollback()
        raise
    else:
        return list(reactors)
    finally:
        connection.close()


def stop_experiment(name: str) -> None:
    """Mark an experiment finished and release its reactors.

    Raises
    ------
    SqlError
        If psycopg is unavailable, the experiment is unknown or it was
        not running.

    """
    require_psycopg()
    updated = _execute(
        "UPDATE experiments SET end_date = %s WHERE name = %s "
        "AND start_date IS NOT NULL AND end_date IS NULL",
        (datetime.now(), name),
    )
    if not updated:
        error_message = f"Experiment {name} is not running"
        raise SqlError(error_message)


def list_experiments() -> list[dict]:
    """Every experiment, newest first, with its state.

    Raises
    ------
    SqlError
        If psycopg is unavailable or the query failed.

    """
    require_psycopg()
    rows = _fetch(
        "SELECT name, reactors, start_date, end_date FROM experiments "
        "ORDER BY COALESCE(start_date, TO_TIMESTAMP(0)) DESC, name",
    )
    return [
        {
            "name": name,
            "reactors": list(reactors),
            "start_date": start_date,
            "end_date": end_date,
            "state": _experiment_state(start_date, end_date),
        }
        for name, reactors, start_date, end_date in rows
    ]


def active_experiments() -> dict[str, str]:
    """Which experiment each reactor is currently running, if any.

    Shaped for ``OpcClient.experiment_tags``, so a client reconnecting
    to a database with experiments already running picks the tags back
    up rather than recording untagged rows.

    Raises
    ------
    SqlError
        If psycopg is unavailable or the query failed.

    """
    require_psycopg()
    rows = _fetch(
        "SELECT name, reactors FROM experiments "
        "WHERE start_date IS NOT NULL AND end_date IS NULL",
    )
    return {
        reactor: name for name, reactors in rows for reactor in reactors
    }


def _experiment_state(start_date: object, end_date: object) -> str:
    """Name the three states an experiment row can be in."""
    if start_date is None:
        return "created"
    if end_date is None:
        return "running"
    return "finished"


def _fetch(query: str, params: tuple = ()) -> list:
    """Run a read query on its own connection and return every row.

    Raises
    ------
    SqlError
        If the query failed.

    """
    connection = connect_to_db()
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()
    except psycopg.Error as err:
        error_message = f"Error during query: {query}"
        raise SqlError(error_message) from err
    finally:
        connection.close()


def _execute(
    query: str,
    params: tuple = (),
    conflict_message: str | None = None,
) -> int:
    """Run a write query on its own connection and commit it.

    Parameters
    ----------
    query, params:
        The statement to run.
    conflict_message:
        What to report if the write violated a unique constraint. Given
        when the collision is an ordinary thing an operator can cause -
        reusing an experiment name - so they get that sentence rather
        than a driver error.

    Returns
    -------
    int
        How many rows the statement changed.

    Raises
    ------
    SqlError
        If the write failed.

    """
    connection = connect_to_db()
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            changed = cursor.rowcount
        connection.commit()
    except psycopg.errors.UniqueViolation as err:
        connection.rollback()
        error_message = conflict_message or f"Conflict during: {query}"
        raise SqlError(error_message) from err
    except psycopg.Error as err:
        connection.rollback()
        error_message = f"Error during: {query}"
        raise SqlError(error_message) from err
    else:
        return changed
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
        If polars is not installed.

    """
    # Schema first: it raises the SqlError this function documents,
    # where a bare import here would raise ImportError instead.
    schema = polars_schema()
    import polars as pl

    return pl.DataFrame(rows, schema=schema, orient="row")
