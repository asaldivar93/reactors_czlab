"""Tests for the sql module that need no database.

Two things are covered: that every public entry point refuses cleanly
when psycopg is missing, and that the statements and row shapes are
built the way the schema expects. Nothing here connects - a live
database is not part of the test environment, and the archiver's real
behaviour against one is not something a unit test can claim.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pytest

from reactors_czlab.sql import operations

if TYPE_CHECKING:
    from typing import Self


@pytest.fixture
def without_psycopg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the driver is not installed, whatever this machine has."""
    monkeypatch.setattr(operations, "PSYCOPG_AVAILABLE", False)


@pytest.fixture
def with_psycopg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Get past the driver guard to reach the validation behind it.

    The guard comes first in every entry point deliberately, so the
    checks that sit behind it are only reachable in a test with the
    guard satisfied.
    """
    monkeypatch.setattr(operations, "PSYCOPG_AVAILABLE", True)


class TestImportGuard:
    """The module loads with no psycopg and no polars."""

    def test_importing_never_needs_the_driver(self) -> None:
        """The Pi runs the server and GUI from one install.

        A module-scope `import psycopg` would make every entry point on
        that machine fail at startup rather than at the point of use.
        """
        assert hasattr(operations, "PSYCOPG_AVAILABLE")

    def test_the_reason_is_actionable(self) -> None:
        """The message is shown to an operator, so it says what to do."""
        assert "libpq" in operations.NO_PSYCOPG_REASON

    @pytest.mark.parametrize(
        ("function", "args"),
        [
            ("connect_to_db", ()),
            ("query_data", ((24.0, "h"),)),
            ("query_experiment_data", ("run-1",)),
            ("query_series", ("R0", [("ph", "pH")], (24.0, "h"))),
            ("create_experiment", ("run-1", ["R0"])),
            ("start_experiment", ("run-1",)),
            ("stop_experiment", ("run-1",)),
            ("list_experiments", ()),
            ("active_experiments", ()),
        ],
    )
    def test_every_entry_point_refuses_without_psycopg(
        self,
        without_psycopg: None,
        function: str,
        args: tuple,
    ) -> None:
        """One recognisable error, not a NoneType AttributeError.

        Without require_psycopg() the failure surfaces wherever psycopg
        was first dereferenced, which differs per function and tells an
        operator nothing.
        """
        with pytest.raises(operations.SqlError, match="psycopg"):
            getattr(operations, function)(*args)


class TestDataStatements:
    """What the data table's statements look like."""

    def test_experiment_name_is_the_last_column(self) -> None:
        """store_data builds its values tuple in COLUMNS order."""
        assert operations.COLUMNS[-1] == "experiment_name"

    def test_insert_has_one_placeholder_per_column(self) -> None:
        """Regression: adding a column by hand is how these drift apart.

        The insert, the select and the CSV header are all derived from
        COLUMNS for this reason.
        """
        assert operations.INSERT_DATA.count("%s") == len(operations.COLUMNS)

    def test_select_projects_every_column(self) -> None:
        """A query feeding rows_to_polars must match its schema width."""
        for column in operations.COLUMNS:
            assert column in operations.SELECT_DATA


class TestStoreData:
    """The row handed to the insert."""

    def test_tags_the_row_with_its_experiment(self) -> None:
        """The value tuple carries what the client stamped on it."""
        captured = _capture_store(
            {
                "timestamp": datetime(2026, 8, 2, 12, 0),  # noqa: DTZ001
                "reactor": "R0",
                "name": "ph",
                "channel": "pH",
                "value": 7.0,
                "experiment_name": "fed-batch-3",
            },
        )
        assert captured[-1] == "fed-batch-3"

    def test_an_untagged_row_records_null(self) -> None:
        """Recording outside an experiment is still recording."""
        captured = _capture_store(
            {
                "timestamp": datetime(2026, 8, 2, 12, 0),  # noqa: DTZ001
                "reactor": "R0",
                "name": "ph",
                "channel": "pH",
                "value": 7.0,
            },
        )
        assert captured[-1] is None

    def test_the_row_is_as_wide_as_the_table(self) -> None:
        """A short tuple would raise from the driver, not from here."""
        captured = _capture_store(
            {
                "timestamp": datetime(2026, 8, 2, 12, 0),  # noqa: DTZ001
                "reactor": "R0",
                "name": "ph",
                "channel": "pH",
                "value": 7.0,
            },
        )
        assert len(captured) == len(operations.COLUMNS)


class _FakeCursor:
    """Captures the statement and parameters it was executed with."""

    def __init__(self, store: list) -> None:
        self.store = store

    def __enter__(self) -> Self:
        """Used as a context manager, the way psycopg cursors are."""
        return self

    def __exit__(self, *args: object) -> None:
        """Nothing to release in a fake."""

    def execute(self, query: str, params: tuple = ()) -> None:
        """Record instead of talking to a server."""
        self.store.append((query, params))


class _FakeConnection:
    """Enough of a psycopg connection for store_data."""

    def __init__(self) -> None:
        self.statements: list = []
        self.committed = False

    def cursor(self) -> _FakeCursor:
        """Hand back the recording cursor."""
        return _FakeCursor(self.statements)

    def commit(self) -> None:
        """Record that the insert was committed."""
        self.committed = True


def _capture_store(info: dict) -> tuple:
    """Run store_data against a fake connection and return its values."""
    connection = _FakeConnection()
    operations.store_data(connection, "ns=2;i=5", info)
    assert connection.committed
    (_, params) = connection.statements[0]
    return params


class TestDateFilter:
    """The window selector's cutoff, reused from the existing code."""

    def test_all_means_no_cutoff(self) -> None:
        """A null cutoff has to mean everything, not nothing."""
        assert operations.get_date_filter_range(24.0, "all") is None

    @pytest.mark.parametrize("units", ["m", "h", "d"])
    def test_a_window_is_in_the_past(self, units: str) -> None:
        """Every supported unit produces a cutoff behind now."""
        cutoff = operations.get_date_filter_range(2.0, units)
        assert cutoff is not None
        assert cutoff < datetime.now()  # noqa: DTZ005

    def test_an_unknown_unit_is_rejected(self) -> None:
        """A typo in a window must not silently mean 'everything'."""
        with pytest.raises(ValueError, match="Invalid time units"):
            operations.get_date_filter_range(2.0, "weeks")


class TestExperimentState:
    """Naming the three states a row can be in."""

    @pytest.mark.parametrize(
        ("start", "end", "expected"),
        [
            (None, None, "created"),
            (datetime(2026, 8, 2, 9, 0), None, "running"),  # noqa: DTZ001
            (
                datetime(2026, 8, 2, 9, 0),  # noqa: DTZ001
                datetime(2026, 8, 2, 17, 0),  # noqa: DTZ001
                "finished",
            ),
        ],
    )
    def test_states(
        self,
        start: datetime | None,
        end: datetime | None,
        expected: str,
    ) -> None:
        """A created experiment is not a running one."""
        assert operations._experiment_state(start, end) == expected


class TestCreateExperimentValidation:
    """Checks that happen before the database is touched."""

    def test_a_blank_name_is_refused(self, with_psycopg: None) -> None:
        """The name is what rows are tagged with; it cannot be empty."""
        with pytest.raises(operations.SqlError, match="needs a name"):
            operations.create_experiment("   ", ["R0"])

    def test_an_empty_reactor_set_is_refused(
        self,
        with_psycopg: None,
    ) -> None:
        """An experiment over no reactors would tag nothing."""
        with pytest.raises(operations.SqlError, match="at least one"):
            operations.create_experiment("run-1", [])


class TestQuerySeries:
    """The plot history query."""

    def test_no_channels_is_an_empty_result_not_a_query(
        self,
        with_psycopg: None,
    ) -> None:
        """An empty filter list must not select the whole table.

        Without the guard the WHERE clause collapses to `reactor = %s`
        and the plot would load every channel of the reactor.
        """
        assert operations.query_series("R0", [], (24.0, "h")) == []
