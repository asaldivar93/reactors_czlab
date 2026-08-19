"""Data models used while fitting pump calibrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lmfit.model import ModelResult


@dataclass(frozen=True)
class CalibrationFit:
    """Selected LMFit result and its persisted uncertainty samples."""

    model: str
    a: float
    b: float
    r2: float
    residual: float
    max_duty: float
    fit_points: list[tuple[float, float, float, float]]


@dataclass(frozen=True)
class _Candidate:
    """One valid fitted model before cross-model selection."""

    name: str
    result: ModelResult
    a: float
    b: float
    r2: float
