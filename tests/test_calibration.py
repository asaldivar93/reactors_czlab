"""Tests for fitting, saving and loading pump calibrations."""

from __future__ import annotations

import json

import pytest

from reactors_czlab.core.calibration import (
    CALIBRATION_ENV,
    calibration_path,
    fit_line,
    load_calibration,
    load_into,
    save_calibration,
)
from reactors_czlab.core.data import Calibration, Channel


@pytest.fixture(autouse=True)
def _cal_dir(tmp_path, monkeypatch) -> None:
    """Keep every test out of the operator's real calibration directory."""
    monkeypatch.setenv(CALIBRATION_ENV, str(tmp_path))


def test_fit_recovers_a_known_line() -> None:
    """Points taken from flow = 0.01 * duty - 2 fit back to it."""
    points = [(500.0, 3.0), (1500.0, 13.0), (2500.0, 23.0)]

    a, b, r2 = fit_line(points)

    assert a == pytest.approx(0.01)
    assert b == pytest.approx(-2.0)
    assert r2 == pytest.approx(1.0)


def test_fit_rejects_too_few_distinct_duties() -> None:
    """Two measurements at the same duty do not define a line."""
    with pytest.raises(ValueError, match="distinct"):
        fit_line([(1000.0, 5.0), (1000.0, 5.2)])


def test_fit_rejects_a_non_positive_slope() -> None:
    """More duty must mean more flow, or the pump is wired backwards."""
    with pytest.raises(ValueError, match="slope"):
        fit_line([(500.0, 20.0), (2500.0, 4.0)])


def test_save_then_load_round_trips() -> None:
    """A saved calibration comes back with its points as tuples."""
    cal = Calibration(
        "R0_pwm0",
        a=0.01,
        b=-2.0,
        min_duty=400.0,
        max_duty=4000.0,
        dispense_duty=2000.0,
        points=[(500.0, 3.0), (2500.0, 23.0)],
        fitted_at="2026-07-27T10:00:00+00:00",
        r2=1.0,
    )

    save_calibration(cal)
    loaded = load_calibration("R0_pwm0")

    assert loaded == cal
    assert loaded.points == [(500.0, 3.0), (2500.0, 23.0)]


def test_load_returns_none_when_there_is_no_file() -> None:
    """A pump that has never been calibrated is not an error."""
    assert load_calibration("R0_pwm0") is None


def test_load_survives_a_corrupt_file() -> None:
    """A truncated file must not take the server down."""
    calibration_path("R0_pwm0").write_text("{not json", encoding="utf-8")

    assert load_calibration("R0_pwm0") is None


def test_load_rejects_a_non_positive_slope_on_disk() -> None:
    """A hand-edited file cannot install a line that cannot be inverted."""
    calibration_path("R0_pwm0").write_text(
        json.dumps({"file": "R0_pwm0", "a": 0.0, "b": 1.0}),
        encoding="utf-8",
    )

    assert load_calibration("R0_pwm0") is None


def test_load_into_installs_the_stored_calibration() -> None:
    """A channel picks up what was saved under its calibration name."""
    save_calibration(
        Calibration("R0_pwm0", a=0.01, fitted_at="2026-07-27T10:00:00+00:00"),
    )
    channel = Channel("pwm0", "pwm", calibration=Calibration("R0_pwm0"))

    assert load_into(channel) is True
    assert channel.calibration.is_fitted is True
    assert channel.calibration.a == 0.01


def test_load_into_keeps_the_unfitted_calibration_when_absent() -> None:
    """With no stored file the channel keeps its placeholder calibration."""
    channel = Channel("pwm0", "pwm", calibration=Calibration("R0_pwm0"))

    assert load_into(channel) is False
    assert channel.calibration.is_fitted is False


def test_load_into_ignores_a_channel_with_no_calibration() -> None:
    """Channels that are not pumps are skipped, not crashed on."""
    assert load_into(Channel("pwm1", "pwm")) is False
