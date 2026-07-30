"""Tests for the optional-dependency guards in the sql module.

The Pi may have no psycopg at all (32 bit Pi OS has no wheel), and the
GUI must still import and run with the database features disabled rather
than failing at import. These tests must pass whether or not psycopg is
installed, so they assert the guard's contract, not a particular answer.
"""

from __future__ import annotations

from typing import Self

import pytest

from reactors_czlab.sql import operations


def test_the_module_imports_without_its_optional_dependencies() -> None:
    """Regression: importing this module used to need psycopg AND polars.

    opcua/client.py imports it at module scope, so the archiver could
    not even be loaded on a machine that had neither.
    """
    assert hasattr(operations, "PSYCOPG_AVAILABLE")
    assert isinstance(operations.PSYCOPG_AVAILABLE, bool)


def test_require_psycopg_matches_the_flag() -> None:
    """The guard raises exactly when the flag says it should."""
    if operations.PSYCOPG_AVAILABLE:
        assert operations.require_psycopg() is None
    else:
        with pytest.raises(operations.SqlError, match="psycopg"):
            operations.require_psycopg()


def test_insert_names_the_experiment_column() -> None:
    """The archiver tags every row with its reactor's experiment."""
    assert "experiment_name" in operations.INSERT_DATA
    assert operations.INSERT_DATA.count("%s") == len(operations.COLUMNS)


def test_columns_and_select_agree() -> None:
    """A column added to one and not the other misaligns every row."""
    for column in operations.COLUMNS:
        assert column in operations.SELECT_DATA


def test_experiment_statements_use_the_array_column() -> None:
    """reactors is TEXT[], so overlap is an array operator, not LIKE."""
    assert "&&" in operations.SELECT_ACTIVE_OVERLAP


def test_a_running_experiment_has_no_end_date() -> None:
    """end_date NULL is what marks an experiment as active."""
    assert "end_date IS NULL" in operations.SELECT_ACTIVE


def test_every_experiment_function_is_guarded() -> None:
    """None of them may reach psycopg when it is absent."""
    if operations.PSYCOPG_AVAILABLE:
        pytest.skip("psycopg is installed; the guard cannot be observed")

    for call in (
        lambda: operations.create_experiment("e", ["R0"]),
        lambda: operations.start_experiment("e"),
        lambda: operations.stop_experiment("e"),
        operations.list_experiments,
        operations.active_experiments,
        lambda: operations.query_experiment_data("e"),
    ):
        with pytest.raises(operations.SqlError, match="psycopg"):
            call()


class _FakeCursor:
    """A cursor that records statements and answers canned queries."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, statement: str, params: tuple = ()) -> None:
        self.executed.append((statement, params))

    def fetchall(self) -> list:
        statement, _params = self.executed[-1]
        if statement == operations.SELECT_EXPERIMENTS:
            return [("exp", ["R0"], None, None)]
        if statement == operations.SELECT_ACTIVE_OVERLAP:
            return []
        return []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


class _FakeConnection:
    """A connection whose cursor is always the same ``_FakeCursor``."""

    def __init__(self) -> None:
        self.cur = _FakeCursor()
        self.committed = False
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self.cur

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


def test_start_experiment_uses_one_connection_and_locks_the_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: check and write used to be separate transactions.

    ``start_experiment`` used to run ``SELECT_EXPERIMENTS``,
    ``SELECT_ACTIVE_OVERLAP`` and ``UPDATE_START`` each on its own
    connection/transaction (via ``_fetch``/``_execute``). Two concurrent
    calls with overlapping reactor sets could both pass the overlap
    check before either committed its update, so both would succeed and
    one reactor would end up in two active experiments at once. The fix
    is to run all three statements on a single connection inside one
    transaction, with the overlap SELECT taking a row lock.
    """
    connections: list[_FakeConnection] = []

    def _fake_connect() -> _FakeConnection:
        connection = _FakeConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(operations, "PSYCOPG_AVAILABLE", True)
    monkeypatch.setattr(operations, "connect_to_db", _fake_connect)

    operations.start_experiment("exp")

    assert len(connections) == 1
    executed = [stmt for stmt, _params in connections[0].cur.executed]
    assert executed == [
        operations.SELECT_EXPERIMENTS,
        operations.SELECT_ACTIVE_OVERLAP,
        operations.UPDATE_START,
    ]
    assert connections[0].committed
    assert connections[0].closed


def test_overlap_select_locks_the_matching_rows() -> None:
    """FOR UPDATE blocks a concurrent start until this one commits.

    Under PostgreSQL's default READ COMMITTED isolation, a plain SELECT
    re-reads a snapshot that predates a concurrent commit; only a
    locking read forces the second transaction to wait and then see the
    first transaction's result.
    """
    assert "FOR UPDATE" in operations.SELECT_ACTIVE_OVERLAP
