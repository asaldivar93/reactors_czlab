"""Tests for ordered, explicit database migrations."""

from __future__ import annotations

from pathlib import Path

import pytest

from reactors_czlab import run_migrate
from reactors_czlab.sql import operations


def test_migration_files_are_ordered_and_well_named() -> None:
    """Lexical filename order is the order applied to production."""
    files = run_migrate.migration_files()
    assert [path.name for path in files] == [
        "0001-experiments.sql",
        "0002-recording-state.sql",
    ]


def test_every_migration_owns_a_transaction_and_stamp() -> None:
    """A partially applied file can never look current."""
    for path in run_migrate.migration_files():
        sql = path.read_text()
        version = path.name[:4]
        assert "BEGIN;" in sql
        assert "COMMIT;" in sql
        assert "INSERT INTO schema_migrations" in sql
        assert f"('{version}', CURRENT_TIMESTAMP)" in sql


def test_fresh_schema_is_stamped_current() -> None:
    """A new database is not immediately asked to migrate itself."""
    schema = (run_migrate.MIGRATIONS_DIR.parent / "Bioreactor.sql").read_text()
    assert "CREATE TABLE schema_migrations" in schema
    for path in run_migrate.migration_files():
        assert f"('{path.name[:4]}', CURRENT_TIMESTAMP)" in schema


def test_invalid_migration_filename_is_rejected(tmp_path: Path) -> None:
    """Unordered ad-hoc SQL cannot enter the migration stream."""
    (tmp_path / "add-column.sql").write_text("SELECT 1;")
    with pytest.raises(operations.SqlError, match="Invalid migration"):
        run_migrate.migration_files(tmp_path)


def test_runner_uses_the_psycopg_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No driver produces the same recognisable SqlError as SQL operations."""
    monkeypatch.setattr(operations, "PSYCOPG_AVAILABLE", False)
    with pytest.raises(operations.SqlError, match="psycopg"):
        run_migrate.apply_migrations()
