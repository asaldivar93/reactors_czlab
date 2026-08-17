"""Turning values into what an operator should see.

Small and pure on purpose. The one rule worth a module of its own is
that ``ERROR_VALUE`` must never reach a screen as a number: -0.111 is a
plausible-looking pH and an operator would read it as a measurement.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from reactors_czlab.core.data import ERROR_VALUE

#: Shown in place of the error sentinel.
ERROR_TEXT = "read failed"

#: Shown when a variable has no value yet - browsed but never published.
UNKNOWN_TEXT = "--"

#: How many sample periods a reading may be old before it is stale. Two
#: rather than one so an ordinary jittery sample does not flicker.
STALE_PERIODS = 2.0


def render_value(
    value: float | None,
    units: str = "",
    digits: int = 3,
) -> str:
    """Render one reading for display.

    Parameters
    ----------
    value:
        The published value, or None if nothing has arrived.
    units:
        Appended when there is a number to append it to.
    digits:
        Decimal places.

    Returns
    -------
    str
        The formatted number, or a phrase saying why there is no number.
        Never the raw sentinel.

    """
    if value is None:
        return UNKNOWN_TEXT
    if is_error(value):
        return ERROR_TEXT
    text = f"{value:.{digits}f}"
    return f"{text} {units}".strip() if units else text


def is_error(value: float | None) -> bool:
    """Whether a value is the failed-read sentinel."""
    return value is not None and value == ERROR_VALUE


def is_stale(
    timestamp: datetime | None,
    now: datetime,
    period: float,
) -> bool:
    """Whether a reading is too old to be believed.

    A reading that stopped updating looks exactly like a steady one, so
    the age is what tells an operator the difference.

    Parameters
    ----------
    timestamp:
        When the value last changed, or None if it never has.
    now:
        The current time, passed in so a caller can stamp a whole panel
        with one reading of the clock.
    period:
        The server's sampling period, in seconds.

    Returns
    -------
    bool
        True when there is no timestamp at all, or the reading is older
        than ``STALE_PERIODS`` sample periods.

    Raises
    ------
    ValueError
        If one of the two datetimes is timezone-aware and the other is
        not. Subtracting those raises a TypeError deep inside the
        comparison; failing here says which two values disagreed.

    """
    if timestamp is None:
        return True
    if (timestamp.tzinfo is None) != (now.tzinfo is None):
        error_message = (
            f"Cannot compare {timestamp!r} with {now!r}: one is "
            "timezone-aware and the other is naive"
        )
        raise ValueError(error_message)
    return now - timestamp > timedelta(seconds=period * STALE_PERIODS)


def render_age(timestamp: datetime | None, now: datetime) -> str:
    """How long ago a reading arrived, in words."""
    if timestamp is None:
        return "never"
    seconds = (now - timestamp).total_seconds()
    if seconds < 1:
        return "just now"
    if seconds < 60:
        return f"{seconds:.0f}s ago"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m ago"
    return f"{seconds / 3600:.1f}h ago"


def render_status(status: str | None) -> str:
    """Render a status string returned by an OPC method call."""
    if not status:
        return UNKNOWN_TEXT
    return status
