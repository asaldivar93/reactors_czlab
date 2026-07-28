"""Fit, store and reload the linear calibration of a pump.

Standard library only: this module runs on the Pi, which carries neither
numpy nor psycopg. The fit is an ordinary least squares of ``flow`` on
``duty`` and needs no more than that.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from reactors_czlab.core.data import Calibration

if TYPE_CHECKING:
    from reactors_czlab.core.data import Channel

_logger = logging.getLogger("server.calibration")

#: Environment variable overriding where calibrations are stored.
CALIBRATION_ENV = "REACTORS_CALIBRATION_DIR"

#: Fewest distinct duty points a fit will accept.
MIN_POINTS = 2


def calibration_dir() -> Path:
    """Directory holding the calibration files, created if missing."""
    override = os.environ.get(CALIBRATION_ENV)
    path = (
        Path(override)
        if override
        else Path.home() / ".reactors_czlab" / "calibrations"
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def calibration_path(name: str) -> Path:
    """Path of the calibration file for ``name``."""
    return calibration_dir() / f"{name}.json"


def fit_line(
    points: list[tuple[float, float]],
) -> tuple[float, float, float]:
    """Fit ``flow = a * duty + b`` by ordinary least squares.

    Parameters
    ----------
    points:
        Measured ``(duty, flow)`` pairs.

    Returns
    -------
    tuple
        ``(a, b, r2)``.

    Raises
    ------
    ValueError
        If fewer than ``MIN_POINTS`` distinct duty values were measured, or
        if the fitted slope is not positive - a pump that delivers less at a
        higher duty is wired backwards or was measured wrongly, and its line
        cannot be safely inverted.

    """
    if len({duty for duty, _ in points}) < MIN_POINTS:
        error_message = (
            f"need at least {MIN_POINTS} distinct duty points, got "
            f"{len(points)} measurements"
        )
        raise ValueError(error_message)

    n = len(points)
    mean_x = sum(duty for duty, _ in points) / n
    mean_y = sum(flow for _, flow in points) / n
    sxx = sum((duty - mean_x) ** 2 for duty, _ in points)
    sxy = sum((duty - mean_x) * (flow - mean_y) for duty, flow in points)

    a = sxy / sxx
    if a <= 0:
        error_message = (
            f"fitted slope {a:.6g} is not positive; the pump delivers less "
            "at a higher duty"
        )
        raise ValueError(error_message)
    b = mean_y - a * mean_x

    syy = sum((flow - mean_y) ** 2 for _, flow in points)
    r2 = 0.0 if syy == 0 else (sxy**2) / (sxx * syy)

    return a, b, r2


def save_calibration(cal: Calibration) -> None:
    """Write a calibration to its file atomically.

    The temp-file-then-replace dance means a power cut during the write
    leaves either the old calibration or the new one, never a half file.
    """
    path = calibration_path(cal.file)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(cal), indent=2), encoding="utf-8")
    os.replace(tmp, path)
    _logger.info("Saved calibration %s: a=%s b=%s", cal.file, cal.a, cal.b)


def load_calibration(name: str) -> Calibration | None:
    """Read a stored calibration.

    Returns
    -------
    Calibration or None
        ``None`` when the file is absent, unreadable, malformed, or holds a
        line that cannot be inverted. Every one of those is logged and left
        for the operator; none of them may take the server down.

    """
    path = calibration_path(name)
    if not path.exists():
        _logger.warning("No stored calibration for %s at %s", name, path)
        return None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        cal = Calibration(**raw)
        cal.points = [(float(d), float(f)) for d, f in cal.points]
    except (OSError, ValueError, TypeError):
        _logger.exception("Unreadable calibration file %s", path)
        return None

    if cal.a <= 0:
        _logger.warning(
            "Calibration %s has a non-positive slope %s, ignoring",
            name,
            cal.a,
        )
        return None
    return cal


def load_into(channel: Channel) -> bool:
    """Install the stored calibration for ``channel``, if there is one.

    Returns
    -------
    bool
        True when a stored calibration replaced the channel's placeholder.

    """
    if channel.calibration is None:
        return False

    stored = load_calibration(channel.calibration.file)
    if stored is None:
        return False

    channel.calibration = stored
    return True
