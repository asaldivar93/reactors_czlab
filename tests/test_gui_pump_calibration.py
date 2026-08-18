"""Tests for what a pump calibration screen may offer, and when."""

from __future__ import annotations

import pytest

from reactors_czlab.core.data import MAX_OUTPUT
from reactors_czlab.gui.controllers.pump_calibration import (
    RunState,
    calibration_chart,
    duty_error,
    seconds_error,
    view_from_payload,
    volume_error,
)


def _payload(**overrides: object) -> dict:
    """A get_calibration payload for an idle, unfitted pump."""
    payload = {
        "actuator": "R0:pwm0",
        "running": False,
        "pending": None,
        "run_points": [],
        "calibration": {
            "file": "R0_pwm0",
            "a": 1.0,
            "b": 0.0,
            "r2": 0.0,
            "min_duty": 0.0,
            "max_duty": MAX_OUTPUT,
            "dispense_duty": MAX_OUTPUT,
            "fitted_at": "",
            "is_fitted": False,
            "points": [],
            "installable_reason": None,
        },
    }
    payload.update(overrides)
    return payload


class TestRunState:
    """Reading the run's state off the payload."""

    def test_idle_when_nothing_is_in_flight(self) -> None:
        """The ordinary starting point."""
        assert view_from_payload(_payload()).state is RunState.idle

    def test_running_while_the_pump_turns(self) -> None:
        """A second run must not start on top of the first."""
        view = view_from_payload(_payload(running=True))
        assert view.state is RunState.running
        assert not view.can_run_point

    def test_awaiting_when_a_measurement_is_owed(self) -> None:
        """record_point is the only useful thing to do here."""
        view = view_from_payload(_payload(pending=[1000.0, 60.0]))
        assert view.state is RunState.awaiting
        assert view.can_record
        assert view.pending_duty == 1000.0
        assert view.pending_seconds == 60.0

    def test_running_wins_over_a_leftover_pending(self) -> None:
        """A new run started; the old pending point is not what to show."""
        view = view_from_payload(
            _payload(running=True, pending=[1000.0, 60.0]),
        )
        assert view.state is RunState.running


class TestFitAvailability:
    """When Fit may be offered."""

    def test_four_distinct_duties_are_enough(self) -> None:
        """The minimum that leaves uncertainty degrees of freedom."""
        view = view_from_payload(
            _payload(
                run_points=[
                    [1000.0, 10.0],
                    [1500.0, 15.0],
                    [2000.0, 20.0],
                    [3000.0, 30.0],
                ],
            ),
        )
        assert view.can_fit

    def test_one_point_is_not(self) -> None:
        """Offering Fit here wastes a rejected call."""
        view = view_from_payload(_payload(run_points=[[1000.0, 10.0]]))
        assert not view.can_fit

    def test_two_points_at_one_duty_are_not(self) -> None:
        """The fitter needs four distinct duties, not four points.

        Discovering that through a refused fit means the operator has
        run the pump twice for nothing.
        """
        view = view_from_payload(
            _payload(run_points=[[1000.0, 10.0], [1000.0, 10.2]]),
        )
        assert not view.can_fit

    def test_not_while_a_measurement_is_owed(self) -> None:
        """Fitting now would quietly drop the pending point."""
        view = view_from_payload(
            _payload(
                run_points=[[1000.0, 10.0], [3000.0, 30.0]],
                pending=[2000.0, 60.0],
            ),
        )
        assert not view.can_fit


class TestNoCalibrationSlot:
    """The MFCs, which cannot be calibrated at all."""

    def test_everything_is_refused_without_a_slot(self) -> None:
        """Every CalibrationRun method answers "has no calibration slot".

        The screen hides rather than offering controls that cannot work.
        """
        view = view_from_payload(_payload(calibration=None))

        assert not view.has_slot
        assert not view.can_run_point
        assert not view.can_fit
        assert not view.can_edit


class TestEditAvailability:
    """Clear, reload and set-duties."""

    def test_allowed_when_idle(self) -> None:
        """The normal case."""
        assert view_from_payload(_payload()).can_edit

    def test_refused_while_running(self) -> None:
        """Changing the installed line mid-run is not coherent."""
        assert not view_from_payload(_payload(running=True)).can_edit

    def test_refused_while_a_measurement_is_owed(self) -> None:
        """clear_points would silently throw the pending point away."""
        view = view_from_payload(_payload(pending=[1000.0, 60.0]))
        assert not view.can_edit


class TestFieldValidation:
    """Checks made before the pump is asked to do anything."""

    @pytest.mark.parametrize("duty", [-1.0, MAX_OUTPUT + 1])
    def test_duty_is_bounded_by_the_output_range(self, duty: float) -> None:
        """The bound comes from MAX_OUTPUT, not a re-typed literal."""
        assert duty_error(duty) is not None

    def test_a_valid_duty_passes(self) -> None:
        """Both ends of the range are usable."""
        assert duty_error(0.0) is None
        assert duty_error(MAX_OUTPUT) is None

    def test_a_blank_duty_is_reported(self) -> None:
        """An empty field is not zero."""
        assert duty_error(None) is not None

    @pytest.mark.parametrize("seconds", [0.5, 601.0])
    def test_run_time_is_bounded_by_the_module_constants(
        self,
        seconds: float,
    ) -> None:
        """MIN_RUN_SECONDS and MAX_RUN_SECONDS, not re-typed numbers."""
        assert seconds_error(seconds) is not None

    def test_a_valid_run_time_passes(self) -> None:
        """A minute is an ordinary calibration run."""
        assert seconds_error(60.0) is None

    def test_zero_volume_is_allowed(self) -> None:
        """It is how a stalled duty is recorded, and the fit uses it.

        Rejecting it would lose the evidence that sets the stall floor.
        """
        assert volume_error(0.0) is None

    def test_negative_volume_is_refused(self) -> None:
        """A pump cannot deliver a negative volume."""
        assert volume_error(-1.0) is not None


def test_calibration_chart_contains_fit_band_and_duty_markers() -> None:
    """The pure chart model carries every server-calculated series."""
    cal = _payload()["calibration"] | {
        "model": "linear",
        "points": [[1000.0, 10.0], [2000.0, 20.0]],
        "fit_series": {
            "duty": [1000.0, 2000.0],
            "flow": [10.0, 20.0],
            "lower": [9.0, 19.0],
            "upper": [11.0, 21.0],
        },
    }

    figure, has_band = calibration_chart(cal)

    assert has_band is True
    assert [trace["name"] for trace in figure["data"]] == [
        "Measurements",
        "95% lower",
        "95% prediction band",
        "linear fit",
    ]
    assert len(figure["layout"]["shapes"]) == 3


def test_legacy_calibration_chart_has_no_synthetic_band() -> None:
    """Old linear files remain visible and explicitly lack uncertainty."""
    figure, has_band = calibration_chart(_payload()["calibration"])

    assert has_band is False
    assert [trace["name"] for trace in figure["data"]] == ["Measurements"]
