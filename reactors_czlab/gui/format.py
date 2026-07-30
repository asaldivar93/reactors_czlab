"""Turn a published reading into something safe to show an operator.

Pure, and separate from the pages for that reason: the one rule worth
testing here is that ``ERROR_VALUE`` never reaches a screen looking like
a measurement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from reactors_czlab.core.data import ERROR_VALUE

if TYPE_CHECKING:
    from datetime import datetime

#: Shown in place of a reading the server could not take.
ERROR_TEXT = "read failed"

#: Shown for a variable that has published nothing yet.
MISSING_TEXT = "-"

#: How many sample periods a reading may be old before it is called
#: stale. Three, so one missed sample does not flag the whole dashboard.
STALE_FACTOR = 3.0


def is_error(value: float | None) -> bool:
    """Whether a value is the failed-read sentinel."""
    return value == ERROR_VALUE


def render_value(
    value: float | None,
    units: str = "",
    digits: int = 3,
) -> str:
    """Format a reading for display.

    Parameters
    ----------
    value:
        The published value, or ``None`` if nothing has arrived.
    units:
        The channel's units, appended when there are any.
    digits:
        Decimal places.

    Returns
    -------
    str
        ``ERROR_TEXT`` for the sentinel, ``MISSING_TEXT`` for ``None``,
        otherwise the number and its units. The sentinel is never shown
        as ``-0.111``: an operator reading that as a pH would act on a
        dead probe.

    """
    if value is None:
        return MISSING_TEXT
    if is_error(value):
        return ERROR_TEXT
    rendered = f"{value:.{digits}f}"
    return f"{rendered} {units}" if units else rendered


def is_stale(
    timestamp: datetime | None,
    now: datetime,
    period: float,
) -> bool:
    """Whether a reading is too old to be shown as current.

    A subscription that has quietly died leaves the last value in place
    forever, which looks exactly like a steady process. Age is the only
    thing that distinguishes them.
    """
    if timestamp is None:
        return True
    return (now - timestamp).total_seconds() > period * STALE_FACTOR
