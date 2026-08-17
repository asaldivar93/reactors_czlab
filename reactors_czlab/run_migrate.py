"""Apply pending PostgreSQL schema migrations explicitly."""

from __future__ import annotations

import re
from pathlib import Path

from reactors_czlab.sql import operations

MIGRATIONS_DIR = Path(__file__).parent / "sql" / "migrations"
MIGRATION_NAME = re.compile(r"^(\d{4})-[a-z0-9-]+\.sql$")


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    """Return migration files in version order.

    Raises
    ------
    SqlError
        If a SQL file does not follow ``NNNN-name.sql`` naming.

    """
    files = sorted(directory.glob("*.sql"))
    invalid = [path.name for path in files if not MIGRATION_NAME.match(path.name)]
    if invalid:
        error_message = f"Invalid migration filenames: {', '.join(invalid)}"
        raise operations.SqlError(error_message)
    return files


def apply_migrations(directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every migration not already recorded by the database.

    Returns
    -------
    list[str]
        Versions applied by this invocation, in order.

    Raises
    ------
    SqlError
        If the driver, database, migration files or SQL execution fail.

    """
    operations.require_psycopg()
    files = migration_files(directory)
    connection = operations.connect_to_db()
    try:
        # Each migration owns its BEGIN/COMMIT transaction. Autocommit keeps
        # the runner from wrapping those explicit transactions in another.
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version TEXT PRIMARY KEY, applied_at TIMESTAMP NOT NULL)",
            )
            cursor.execute("SELECT version FROM schema_migrations")
            applied = {row[0] for row in cursor.fetchall()}
            completed: list[str] = []
            for path in files:
                match = MIGRATION_NAME.match(path.name)
                if match is None:  # guarded by migration_files
                    continue
                version = match.group(1)
                if version in applied:
                    continue
                try:
                    sql = path.read_text()
                except OSError as err:
                    error_message = f"Could not read migration {path.name}"
                    raise operations.SqlError(error_message) from err
                cursor.execute(sql)
                completed.append(version)
        return completed
    except operations.psycopg.Error as err:
        error_message = "Database migration failed"
        raise operations.SqlError(error_message) from err
    finally:
        connection.close()


def cli() -> None:
    """Apply migrations and print the versions changed."""
    try:
        completed = apply_migrations()
    except operations.SqlError as err:
        raise SystemExit(str(err)) from err
    if completed:
        print(f"Applied migrations: {', '.join(completed)}")
    else:
        print(f"Database is current at {operations.SCHEMA_VERSION}")


if __name__ == "__main__":
    cli()
