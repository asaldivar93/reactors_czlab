"""Tests for the optional-dependency guards in the sql module.

The Pi may have no psycopg at all (32 bit Pi OS has no wheel), and the
GUI must still import and run with the database features disabled rather
than failing at import. These tests must pass whether or not psycopg is
installed, so they assert the guard's contract, not a particular answer.
"""

from __future__ import annotations

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
