"""Persist and reload pump calibrations safely."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from os import environ
from pathlib import Path
from typing import TYPE_CHECKING

from reactors_czlab.core.calibration.models import Calibration

if TYPE_CHECKING:
    from reactors_czlab.core.data import Channel

_logger = logging.getLogger("server.calibration")

#: Environment variable overriding where calibrations are stored.
CALIBRATION_ENV = "REACTORS_CALIBRATION_DIR"


def calibration_dir() -> Path:
    """Directory holding the calibration files, created if missing."""
    override = environ.get(CALIBRATION_ENV)
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

def save_calibration(cal: Calibration) -> None:
    """Write a calibration to its file atomically.

    The temp-file-then-replace dance means a power cut during the write
    leaves either the old calibration or the new one, never a half file.
    """
    path = calibration_path(cal.file)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(asdict(cal), indent=2, allow_nan=False),
        encoding="utf-8",
    )
    tmp.replace(path)
    _logger.info(
        "Saved %s calibration %s: a=%s b=%s c=%s AIC=%s residual=%s",
        cal.model,
        cal.file,
        cal.a,
        cal.b,
        cal.c,
        cal.aic,
        cal.residual,
    )


def load_calibration(name: str) -> Calibration | None:
    """Read a stored calibration.

    Every step - resolving and creating the calibration directory,
    reading the file, parsing its JSON, and coercing its fields - is
    covered by the same guard, because any of them can fail on a
    hand-edited file or an unfriendly filesystem and none of them may
    take the server down.

    The guard is a bare ``except Exception``, deliberately. This function
    reads a file an operator can hand-edit into anything: earlier passes
    only anticipated ``OSError``/``ValueError``/``TypeError`` and still
    missed ``OverflowError`` from ``float()`` on an oversized JSON
    integer. Enumerating every exception type ``json.loads``,
    ``Calibration(**raw)``, ``float()`` and tuple-unpacking can raise on
    adversarial input is not a task with a stable finish line, and the
    function's one contract - log and return ``None`` - does not depend
    on which exception type it was. ``BaseException`` subclasses that are
    not ``Exception`` (``KeyboardInterrupt``, ``SystemExit``) still
    propagate, which is correct: those are not data problems.

    Returns
    -------
    Calibration or None
        ``None`` when the calibration directory cannot be created, the
        file is absent, unreadable, malformed, has a field of the wrong
        type or value, or holds a line that cannot be inverted. Every
        one of those is logged and left for the operator.

    """
    try:
        path = calibration_path(name)
        if not path.exists():
            _logger.warning("No stored calibration for %s at %s", name, path)
            return None

        raw = json.loads(path.read_text(encoding="utf-8"))
        cal = Calibration(**raw)
        cal.a = float(cal.a)
        cal.b = float(cal.b)
        cal.c = float(cal.c)
        cal.min_duty = float(cal.min_duty)
        cal.max_duty = float(cal.max_duty)
        cal.dispense_duty = float(cal.dispense_duty)
        cal.r2 = float(cal.r2)
        if cal.residual is not None:
            cal.residual = float(cal.residual)
        if cal.aic is not None:
            cal.aic = float(cal.aic)
        if cal.zero_flow_duty is not None:
            cal.zero_flow_duty = float(cal.zero_flow_duty)
        cal.points = [(float(d), float(f)) for d, f in cal.points]
        cal.fit_points = [
            tuple(float(value) for value in point)
            for point in cal.fit_points
        ]
    except Exception:  # see docstring: this function must never raise
        _logger.exception("Unreadable calibration file for %s", name)
        return None

    reason = cal.installable_reason()
    if reason is not None:
        _logger.warning(
            "Calibration %s is not installable: %s",
            name,
            reason,
        )
        return None
    return cal


def replacement_reason(
    current: Calibration,
    stored: Calibration,
) -> str | None:
    """Why ``stored`` may not replace ``current`` on a channel.

    A different question from ``Calibration.installable_reason()``, and
    deliberately kept out of it. That one asks *is this calibration safe
    to drive a pump with at all*, judges nothing but the numbers, and is
    the single authority every install site defers to. This one asks
    *may this replace what is already there*, which only has an answer
    relative to the calibration being replaced - so it lives at the two
    sites that replace one, ``load_into()`` and ``CalibrationRun.reload()``,
    and is written once here so those two cannot drift apart.

    The one case it refuses: an unfitted line landing on top of a fitted
    one. ``Dispenser._accrue()`` is the only consumer that gates on
    ``fitted_at`` - every other one judges the numbers - so an unfitted
    calibration leaves the pump dosing exactly as before while
    ``total_volume`` silently stops counting. The gate cannot simply be
    dropped from ``_accrue`` instead: the placeholder ``server_info.py``
    builds for an uncalibrated pump is ``a=1.0, b=0``, which would report
    ``flow_at(2000) = 2000`` mL/min.

    Returns
    -------
    str or None
        ``None`` when the replacement is allowed, otherwise a
        human-readable reason, safe to show the operator verbatim.

    """
    if current.is_fitted and not stored.is_fitted:
        return (
            f"the stored calibration for {current.file} has never been "
            "fitted, and a fitted one is installed; installing it would "
            "leave the pump dosing while the delivered volume stopped "
            "being counted"
        )
    return None


def load_into(channel: Channel) -> bool:
    """Install the stored calibration for ``channel``, if there is one.

    Returns
    -------
    bool
        True when a stored calibration replaced the channel's placeholder.
        False when there was nothing stored, or what was stored is not
        safe to install - either way the channel keeps its previous
        (placeholder or otherwise) calibration.

    """
    if channel.calibration is None:
        return False

    stored = load_calibration(channel.calibration.file)
    if stored is None:
        return False

    reason = stored.installable_reason() or replacement_reason(
        channel.calibration,
        stored,
    )
    if reason is not None:
        _logger.warning(
            "Stored calibration for %s is unusable, keeping the "
            "existing one: %s",
            channel.calibration.file,
            reason,
        )
        return False

    channel.calibration = stored
    return True
