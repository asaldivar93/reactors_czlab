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
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    import psycopg
except ImportError:  # pragma: no cover - depends on the install
    psycopg = None

if TYPE_CHECKING:
    from collections.abc import Iterator

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


INSERT_EXPERIMENT = (
    "INSERT INTO experiments (name, reactors) VALUES (%s, %s)"
)

UPDATE_START = (
    "UPDATE experiments SET start_date = %s "
    "WHERE name = %s AND start_date IS NULL"
)

UPDATE_STOP = (
    "UPDATE experiments SET end_date = %s "
    "WHERE name = %s AND end_date IS NULL"
)

SELECT_EXPERIMENTS = (
    "SELECT name, reactors, start_date, end_date FROM experiments "
    "ORDER BY id"
)

#: Running experiments: started and not yet stopped.
SELECT_ACTIVE = (
    "SELECT name, reactors FROM experiments "
    "WHERE start_date IS NOT NULL AND end_date IS NULL"
)

#: Whether any running experiment already owns one of these reactors.
#: ``&&`` is the array-overlap operator; the reactor set is a TEXT[], so
#: this is one indexable comparison rather than a LIKE over a joined
#: string, which would match R1 inside R10.
#:
#: This is a plain SELECT, not ``... FOR UPDATE``, on purpose: a row
#: lock only covers rows that already exist and already match the
#: WHERE clause. The case that actually matters is two brand-new
#: experiments, neither yet started, sharing a reactor, started
#: concurrently - at the moment each transaction runs this query,
#: *neither* row has been updated yet, so both selects match zero rows,
#: there is nothing for ``FOR UPDATE`` to lock, and both go on to
#: update different rows with no conflict. PostgreSQL row locks do not
#: block phantom rows under READ COMMITTED. The invariant is instead
#: enforced by serialising the whole check-and-write with
#: ``EXPERIMENT_LOCK_KEY`` below, which exists whether or not any
#: experiment is currently active.
SELECT_ACTIVE_OVERLAP = (
    "SELECT name FROM experiments "
    "WHERE start_date IS NOT NULL AND end_date IS NULL "
    "AND reactors && %s"
)

#: Arbitrary fixed integer identifying the "starting an experiment"
#: critical section for ``pg_advisory_xact_lock``. Advisory lock keys
#: are just agreed-upon numbers with no relation to any table or row;
#: this one only has to be unique among advisory-lock users of this
#: database. It serialises concurrent ``start_experiment`` calls
#: against each other and nothing else.
EXPERIMENT_LOCK_KEY = 726_193_845

#: Held for the rest of the transaction and released automatically on
#: COMMIT or ROLLBACK - unlike ``pg_advisory_lock``, there is no unlock
#: statement to remember or to leak if the transaction raises.
LOCK_EXPERIMENT_STARTS = "SELECT pg_advisory_xact_lock(%s)"

SELECT_EXPERIMENT_DATA = SELECT_DATA + (
    " WHERE experiment_name = %s ORDER BY date"
)


def _execute(statement: str, params: tuple) -> None:
    """Run one statement in its own connection and commit it.

    Raises
    ------
    SqlError
        If psycopg is missing or the statement failed.

    """
    require_psycopg()
    connection = connect_to_db()
    try:
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
        connection.commit()
    except psycopg.Error as err:
        error_message = f"Error running {statement} with {params}"
        raise SqlError(error_message) from err
    finally:
        connection.close()


def _fetch(statement: str, params: tuple = ()) -> list:
    """Run one query in its own connection and return every row.

    Raises
    ------
    SqlError
        If psycopg is missing or the query failed.

    """
    require_psycopg()
    connection = connect_to_db()
    try:
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            return cursor.fetchall()
    except psycopg.Error as err:
        error_message = f"Error running {statement} with {params}"
        raise SqlError(error_message) from err
    finally:
        connection.close()


@contextmanager
def _transaction() -> Iterator[Any]:
    """Run several statements on one connection, in one transaction.

    ``_fetch``/``_execute`` each open their own connection, so a
    check-then-act sequence built out of them is not atomic: a
    concurrent caller can run its own check between this caller's check
    and its write. Use this instead whenever a decision (a SELECT) and
    the write that follows from it must be seen as a single unit by any
    other transaction.

    Yields
    ------
    Cursor
        A cursor bound to one connection, for the caller to run every
        statement of the transaction on.

    Raises
    ------
    SqlError
        If psycopg is missing, or any statement failed - in which case
        the transaction is rolled back first. A caller that raises its
        own ``SqlError`` from inside the ``with`` block (e.g. to refuse
        an invalid request) is not caught here; the connection is still
        closed, which implicitly rolls back an uncommitted transaction.

    """
    require_psycopg()
    connection = connect_to_db()
    try:
        with connection.cursor() as cursor:
            yield cursor
        connection.commit()
    except psycopg.Error as err:
        try:
            connection.rollback()
        except psycopg.Error:
            _logger.debug("Rollback failed", exc_info=True)
        error_message = "Error during transaction"
        raise SqlError(error_message) from err
    finally:
        connection.close()


def create_experiment(name: str, reactors: list[str]) -> None:
    """Record a new experiment, not yet started.

    Raises
    ------
    SqlError
        If psycopg is missing, the name is already taken, or the insert
        failed.

    """
    require_psycopg()
    _execute(INSERT_EXPERIMENT, (name, list(reactors)))
    _logger.info("Created experiment %s on %s", name, reactors)


def start_experiment(name: str) -> None:
    """Mark an experiment as running from now.

    The existence check, the overlap check and the write happen on a
    single connection inside one transaction (see ``_transaction``).
    That alone is not enough to close the race: two brand-new
    experiments sharing a reactor, started concurrently, both see an
    empty overlap result (neither has been written yet) no matter how
    the reads and writes are grouped into transactions. So the first
    statement inside the transaction takes ``pg_advisory_xact_lock``
    on ``EXPERIMENT_LOCK_KEY``, which serialises every
    ``start_experiment`` call against every other one: the second
    caller blocks at the lock until the first transaction commits or
    rolls back, and only then runs its own overlap check - by which
    point the first transaction's write, if any, is visible to it.

    Raises
    ------
    SqlError
        If psycopg is missing, the experiment does not exist, it has
        already been started, or one of its reactors already belongs to
        a running experiment. Overlapping reactor sets are refused
        because a row can carry only one experiment name.

    """
    require_psycopg()
    with _transaction() as cursor:
        cursor.execute(LOCK_EXPERIMENT_STARTS, (EXPERIMENT_LOCK_KEY,))
        cursor.fetchall()

        cursor.execute(SELECT_EXPERIMENTS)
        rows = cursor.fetchall()
        matched = [row for row in rows if row[0] == name]
        if not matched:
            error_message = f"No experiment named {name}"
            raise SqlError(error_message)

        _name, reactors, start_date, end_date = matched[0]
        if start_date is not None and end_date is None:
            error_message = f"{name} is already running"
            raise SqlError(error_message)

        reactors = list(reactors)
        cursor.execute(SELECT_ACTIVE_OVERLAP, (reactors,))
        clashes = cursor.fetchall()
        if clashes:
            error_message = (
                f"Cannot start {name}: {clashes[0][0]} is already "
                f"running on one of {reactors}"
            )
            raise SqlError(error_message)

        cursor.execute(UPDATE_START, (datetime.now(), name))

    _logger.info("Started experiment %s on %s", name, reactors)


def stop_experiment(name: str) -> None:
    """Mark a running experiment as finished.

    Raises
    ------
    SqlError
        If psycopg is missing or the update failed.

    """
    require_psycopg()
    _execute(UPDATE_STOP, (datetime.now(), name))
    _logger.info("Stopped experiment %s", name)


def list_experiments() -> list:
    """Every experiment as ``(name, reactors, start_date, end_date)``.

    Raises
    ------
    SqlError
        If psycopg is missing or the query failed.

    """
    return _fetch(SELECT_EXPERIMENTS)


def active_experiments() -> dict[str, str]:
    """Map each reactor to the running experiment that owns it.

    This is what the archiver tags rows with.

    Raises
    ------
    SqlError
        If psycopg is missing or the query failed.

    """
    tags: dict[str, str] = {}
    for name, reactors in _fetch(SELECT_ACTIVE):
        for reactor in reactors:
            tags[reactor] = name
    return tags


def query_experiment_data(name: str) -> list:
    """Every archived row tagged with this experiment.

    Raises
    ------
    SqlError
        If psycopg is missing or the query failed.

    """
    return _fetch(SELECT_EXPERIMENT_DATA, (name,))
