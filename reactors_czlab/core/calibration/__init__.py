"""Fit, store and reload safe monotone pump calibrations."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from reactors_czlab.core.calibration.fitting import (
        MAX_RELATIVE_UNCERTAINTY,
        MIN_POINTS,
        PLOT_SAMPLES,
        fit_models,
    )
    from reactors_czlab.core.calibration.models import Calibration, CalibrationFit
    from reactors_czlab.core.calibration.run import (
        MAX_RUN_SECONDS,
        MIN_RUN_SECONDS,
        CalibrationRun,
    )
    from reactors_czlab.core.calibration.storage import (
        CALIBRATION_ENV,
        calibration_dir,
        calibration_path,
        load_calibration,
        load_into,
        replacement_reason,
        save_calibration,
    )

__all__ = [
    "CALIBRATION_ENV",
    "MAX_RELATIVE_UNCERTAINTY",
    "MAX_RUN_SECONDS",
    "MIN_POINTS",
    "MIN_RUN_SECONDS",
    "PLOT_SAMPLES",
    "Calibration",
    "CalibrationFit",
    "CalibrationRun",
    "calibration_dir",
    "calibration_path",
    "fit_models",
    "load_calibration",
    "load_into",
    "replacement_reason",
    "save_calibration",
]

_EXPORT_MODULES = {
    "Calibration": "models",
    "CalibrationFit": "models",
    "MAX_RELATIVE_UNCERTAINTY": "fitting",
    "MIN_POINTS": "fitting",
    "PLOT_SAMPLES": "fitting",
    "fit_models": "fitting",
    "MAX_RUN_SECONDS": "run",
    "MIN_RUN_SECONDS": "run",
    "CalibrationRun": "run",
    "CALIBRATION_ENV": "storage",
    "calibration_dir": "storage",
    "calibration_path": "storage",
    "load_calibration": "storage",
    "load_into": "storage",
    "replacement_reason": "storage",
    "save_calibration": "storage",
}


def __getattr__(name: str) -> Any:
    """Resolve and cache public calibration attributes on first access."""
    try:
        module_name = _EXPORT_MODULES[name]
    except KeyError:
        error_message = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(error_message) from None
    module = import_module(f"{__name__}.{module_name}")
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Return module globals together with lazily exported names."""
    return sorted(set(globals()) | set(__all__))
