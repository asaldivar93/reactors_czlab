"""Tests for what an operator actually reads off the screen."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from reactors_czlab.core.data import ERROR_VALUE
from reactors_czlab.gui.format import (
    ERROR_TEXT,
    UNKNOWN_TEXT,
    is_error,
    is_stale,
    render_age,
    render_value,
)

NOW = datetime(2026, 8, 2, 12, 0, 0)  # noqa: DTZ001 - client stores naive


class TestRenderValue:
    """Turning a published float into text."""

    def test_the_error_sentinel_is_never_shown_as_a_number(self) -> None:
        """-0.111 is a plausible pH and would be read as a measurement.

        This is the whole reason this module exists.
        """
        assert render_value(ERROR_VALUE) == ERROR_TEXT
        assert "0.111" not in render_value(ERROR_VALUE)

    def test_a_missing_value_says_so(self) -> None:
        """A browsed variable that never published is not zero."""
        assert render_value(None) == UNKNOWN_TEXT

    def test_zero_is_a_real_reading(self) -> None:
        """Regression bait: `if not value` would hide a stopped pump."""
        assert render_value(0.0) == "0.000"

    def test_units_are_appended_when_there_is_a_number(self) -> None:
        """Units belong to the number, not to the failure."""
        assert render_value(7.25, units="pH") == "7.250 pH"
        assert render_value(ERROR_VALUE, units="pH") == ERROR_TEXT

    def test_digits_are_configurable(self) -> None:
        """A fitted slope needs more places than a pH."""
        assert render_value(0.012345, digits=4) == "0.0123"


class TestIsError:
    """Recognising the sentinel."""

    def test_matches_the_constant_not_a_literal(self) -> None:
        """The sentinel is a named constant everywhere in the project."""
        assert is_error(ERROR_VALUE)

    def test_ordinary_values_are_not_errors(self) -> None:
        """Including a legitimately negative reading."""
        assert not is_error(0.0)
        assert not is_error(-0.1)
        assert not is_error(None)


class TestIsStale:
    """Telling a steady reading from a stopped one."""

    def test_a_fresh_reading_is_not_stale(self) -> None:
        """Inside one sample period is current."""
        assert not is_stale(NOW - timedelta(seconds=5), NOW, 10.0)

    def test_an_old_reading_is_stale(self) -> None:
        """A stopped feed looks identical to a steady one otherwise."""
        assert is_stale(NOW - timedelta(seconds=60), NOW, 10.0)

    def test_a_reading_that_never_arrived_is_stale(self) -> None:
        """No timestamp is the most stale a reading can be."""
        assert is_stale(None, NOW, 10.0)

    def test_a_jittery_sample_does_not_flicker(self) -> None:
        """The threshold is two periods, not one, for this reason."""
        assert not is_stale(NOW - timedelta(seconds=11), NOW, 10.0)

    def test_mixing_aware_and_naive_fails_clearly(self) -> None:
        """Subtracting them raises a TypeError from deep inside.

        The client stores naive timestamps; if a caller ever passes an
        aware `now`, the message should say which two values disagreed
        rather than surfacing as "can't subtract offset-naive and
        offset-aware datetimes" from a comparison.
        """
        aware = NOW.replace(tzinfo=UTC)
        with pytest.raises(ValueError, match="timezone-aware"):
            is_stale(NOW - timedelta(seconds=5), aware, 10.0)


class TestRenderAge:
    """How long ago, in words."""

    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (0, "just now"),
            (5, "5s ago"),
            (120, "2m ago"),
            (7200, "2.0h ago"),
        ],
    )
    def test_scales_the_unit(self, seconds: int, expected: str) -> None:
        """Seconds for a live panel, hours for a stalled one."""
        stamp = NOW - timedelta(seconds=seconds)
        assert render_age(stamp, NOW) == expected

    def test_never_when_nothing_arrived(self) -> None:
        """No timestamp is not an age of zero."""
        assert render_age(None, NOW) == "never"
