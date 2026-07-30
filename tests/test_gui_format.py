"""Tests for how the GUI renders a reading."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from reactors_czlab.core.data import ERROR_VALUE
from reactors_czlab.gui.format import (
    ERROR_TEXT,
    MISSING_TEXT,
    is_error,
    is_stale,
    render_value,
)


def test_the_error_sentinel_is_recognised() -> None:
    """A failed read is -0.111, compared against the constant."""
    assert is_error(ERROR_VALUE) is True
    assert is_error(7.0) is False


def test_the_sentinel_never_renders_as_a_number() -> None:
    """An operator must not read -0.111 as a measurement."""
    assert render_value(ERROR_VALUE, units="pH") == ERROR_TEXT


def test_a_reading_renders_with_its_units() -> None:
    """Units come from the channel, not from the value."""
    assert render_value(7.1234, units="pH") == "7.123 pH"


def test_a_reading_without_units_renders_bare() -> None:
    """Biomass channels are dimensionless."""
    assert render_value(1234.5, units="") == "1234.500"


def test_a_missing_value_is_distinct_from_a_failed_read() -> None:
    """Nothing published yet is not the same as a probe that failed."""
    assert render_value(None) == MISSING_TEXT


def test_a_fresh_reading_is_not_stale() -> None:
    """One sample period old is still current."""
    now = datetime(2026, 7, 30, 12, 0, 0)  # noqa: DTZ001
    assert is_stale(now - timedelta(seconds=5), now, period=10.0) is False


def test_an_old_reading_is_stale() -> None:
    """Past the grace factor the UI must say so, not show a number."""
    now = datetime(2026, 7, 30, 12, 0, 0)  # noqa: DTZ001
    assert is_stale(now - timedelta(seconds=60), now, period=10.0) is True


def test_a_reading_that_never_arrived_is_stale() -> None:
    """No timestamp means nothing has been published."""
    now = datetime(2026, 7, 30, 12, 0, 0)  # noqa: DTZ001
    assert is_stale(None, now, period=10.0) is True


def test_aware_now_with_naive_timestamp_raises() -> None:
    """Mixing aware and naive datetimes is a programming error.

    The client stores naive timestamps (``datetime.now()`` with no tzinfo),
    so a dashboard passing ``datetime.now(UTC)`` would hit this mismatch.
    """
    now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
    naive_timestamp = datetime(2026, 7, 30, 12, 0, 0)  # noqa: DTZ001
    with pytest.raises(ValueError, match="tzinfo mismatch"):
        is_stale(naive_timestamp, now, period=10.0)


def test_naive_now_with_aware_timestamp_raises() -> None:
    """Mixing aware and naive datetimes is a programming error.

    The client stores naive timestamps, so an aware timestamp would also
    be a programming error (wrong construction upstream).
    """
    now = datetime(2026, 7, 30, 12, 0, 0)  # noqa: DTZ001
    aware_timestamp = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="tzinfo mismatch"):
        is_stale(aware_timestamp, now, period=10.0)
