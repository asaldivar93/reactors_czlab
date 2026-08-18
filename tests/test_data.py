"""Tests for the shared dataclasses."""

from __future__ import annotations

import pytest

from reactors_czlab.core.data import (
    MAX_DOSE_SECONDS,
    MAX_OUTPUT,
    MIN_DISPENSE_FLOW,
    Calibration,
    ControlConfig,
    ControlMethod,
    OutputUnit,
)


def test_a_fresh_calibration_is_unfitted() -> None:
    """A calibration with no fit timestamp must not be trusted."""
    cal = Calibration("pump_0")

    assert cal.is_fitted is False
    assert cal.points == []
    assert cal.max_duty == MAX_OUTPUT
    assert cal.dispense_duty == MAX_OUTPUT


def test_calibration_points_are_not_shared_between_instances() -> None:
    """points must be a per-instance list, not a class-level default."""
    first = Calibration("pump_0")
    second = Calibration("pump_1")

    first.points.append((100.0, 1.0))

    assert second.points == []


def test_the_line_converts_both_ways() -> None:
    """flow_at and duty_for are inverses of flow = a * duty + b."""
    cal = Calibration("pump_0", a=0.01, b=-2.0)

    assert cal.flow_at(1000.0) == 8.0
    assert cal.duty_for(8.0) == 1000.0


def test_a_fitted_calibration_reports_itself_fitted() -> None:
    """A non-empty fitted_at is what makes a calibration usable."""
    cal = Calibration("pump_0", fitted_at="2026-07-27T10:00:00+00:00")

    assert cal.is_fitted is True


def test_control_config_defaults_to_duty() -> None:
    """Existing callers get today's behaviour with no change."""
    config = ControlConfig(ControlMethod.manual, value=150)

    assert config.output_unit is OutputUnit.duty


def _cal(**kwargs) -> Calibration:
    """A calibration that is installable unless a field is overridden.

    flow = 0.01 * duty, a 0 - 4000 count band and a 2000 count dispense
    duty: the same shape as the fitted fixture, so every test below
    changes exactly the one field it is about.
    """
    fields = {
        "a": 0.01,
        "b": 0.0,
        "min_duty": 0.0,
        "max_duty": 4000.0,
        "dispense_duty": 2000.0,
        "fitted_at": "2026-07-27T10:00:00+00:00",
    }
    return Calibration("pump_0", **(fields | kwargs))


def test_the_baseline_of_these_tests_is_installable() -> None:
    """Every rejection below must come from the field it overrides.

    Without this, a test asserting "some reason was returned" proves
    nothing: the baseline itself could be what is being refused.
    """
    assert _cal().installable_reason() is None


def test_the_startup_placeholder_is_installable() -> None:
    """server_info.py builds a bare `Calibration(<name>)` for every pump.

    An authority that refused it would stop duty mode working on a
    never-calibrated pump - over-strict is its own defect.
    """
    assert Calibration("pump_0").installable_reason() is None


def test_installable_reason_rejects_a_flat_line() -> None:
    """A zero slope divides by zero in duty_for().

    Pins the `a <= 0` branch: with b positive the line produces flow
    everywhere, so no later branch would catch it.
    """
    reason = _cal(a=0.0, b=1.0).installable_reason()

    assert reason is not None
    assert "coefficient" in reason


def test_installable_reason_rejects_a_negative_stall_floor() -> None:
    """The floor is what keeps a converted duty at or above zero.

    Pins the `min_duty < 0` branch: every other field here is sound, so
    without it this installs.
    """
    reason = _cal(min_duty=-500.0).installable_reason()

    assert reason is not None
    assert "negative" in reason


def test_installable_reason_rejects_an_inverted_band() -> None:
    """min_duty above max_duty leaves no duty the pump may be driven at.

    Pins the `min_duty > max_duty` branch by its text: the stall-floor
    branch would also refuse this input, so `is not None` alone does
    not discriminate. Deleting the check was the gap D2 named - it is
    the only thing stopping set_duties() from inverting a band.
    """
    reason = _cal(min_duty=3000.0, max_duty=2000.0).installable_reason()

    assert reason is not None
    assert "no usable band" in reason


def test_installable_reason_rejects_a_max_duty_above_full_scale() -> None:
    """A hand-edited max_duty beyond MAX_OUTPUT is not a usable band.

    _duty_for_flow() clamps a converted duty to max_duty and nothing
    else, so a max_duty of 1e9 puts 1e9 on the pin.
    """
    reason = _cal(max_duty=1e9).installable_reason()

    assert reason is not None
    assert "full scale" in reason


def test_a_dispense_duty_below_the_stall_floor_is_refused_honestly() -> None:
    """The refusal must name the failure that actually happens.

    Regression (D1): this branch said "a dose at that duty would never
    finish". For this calibration - the findings file's own numbers -
    flow at 1450 counts is a positive 1.57 mL/min, so the dose does
    finish, in 39 s. What really happens is that the pump was *measured*
    not to turn at 1450, so those 39 s deliver nothing. A guard that
    lies about why it refused sends the operator after the wrong number.
    """
    cal = _cal(a=0.0257, b=-35.7, min_duty=1500.0, dispense_duty=1450.0)

    reason = cal.installable_reason()

    # The evidence that the old message was false, asserted rather than
    # described: the dose terminates, and quickly.
    assert cal.flow_at(1450.0) == pytest.approx(1.565)
    assert 60.0 / cal.flow_at(1450.0) == pytest.approx(38.3, abs=0.1)

    assert reason is not None
    assert "stall floor" in reason
    assert "never finish" not in reason
    assert "measured not to turn" in reason


def test_installable_reason_rejects_a_dispense_duty_above_max_duty() -> None:
    """_start_dose writes dispense_duty; the band is what bounds it.

    Regression (D3): `set_duties(0, 4095)` on a max_duty=4000 pump
    installed, and a hand-edited `dispense_duty: 1e9` installed through
    reload()/load_into() and reached the pin. MAX_OUTPUT is 4095.
    """
    reason = _cal(dispense_duty=4095.0).installable_reason()

    assert reason is not None
    assert "above max duty" in reason
    assert _cal(dispense_duty=1e9).installable_reason() is not None


def test_installable_reason_rejects_a_line_dead_across_its_band() -> None:
    """No flow at max_duty means no flow at any usable duty.

    Pins the max-duty branch by its text: with the dispense duty at the
    ceiling the dispense-flow branch would also refuse this, but with a
    message about doses, which is not what an operator selecting flow
    mode needs to read.
    """
    cal = _cal(b=-50.0, min_duty=4000.0, dispense_duty=4000.0)

    reason = cal.installable_reason()

    assert reason is not None
    assert "no flow anywhere" in reason


def test_installable_reason_rejects_exact_zero_flow_at_dispense() -> None:
    """flow_at(dispense_duty) == 0 divides by zero in _start_dose.

    The one branch whose "would never finish" wording is fair: the
    deadline is 60 * demand / 0.
    """
    reason = _cal(b=-10.0, dispense_duty=1000.0).installable_reason()

    assert reason is not None
    assert "produces no flow" in reason
    assert "never finish" in reason


def test_installable_reason_rejects_negative_flow_at_dispense() -> None:
    """A dispense duty under the line's zero crossing delivers nothing.

    Split from the exact-zero branch because the consequence differs:
    the deadline is negative, so the dose expires on the next tick
    having delivered nothing at all - it does not "never finish".
    """
    reason = _cal(b=-10.0, dispense_duty=500.0).installable_reason()

    assert reason is not None
    assert "zero flow" in reason
    assert "end immediately" in reason


def test_installable_reason_rejects_a_line_too_flat_to_finish() -> None:
    """A near-zero slope strands the pump ON.

    Regression (D4): `flow_at(dispense_duty) > 0` is the wrong shape for
    the hazard _start_dose names. A slope of 1e-12 is positive, so it
    installed, and a 1 mL dose got a deadline 6e10 s - about 1900 years
    - out, with the pump held at the dispense duty the whole time. That
    is the same "stranded ON" failure the non-finite demand rejection
    exists to prevent, reached through the calibration instead.
    """
    cal = _cal(a=1e-12)

    reason = cal.installable_reason()

    assert cal.flow_at(cal.dispense_duty) > 0  # the old check passed
    assert 60.0 / cal.flow_at(cal.dispense_duty) > MAX_DOSE_SECONDS
    assert reason is not None
    assert "delivers only" in reason
    assert f"{MAX_DOSE_SECONDS:.0f} s" in reason


def test_a_legitimately_slow_pump_is_still_installable() -> None:
    """The dose-time bound must not refuse a real, slow pump.

    0.01 mL/min at the dispense duty is 1 mL in 100 minutes - already
    at the edge of what this system's own calibration procedure can
    measure (a single point is capped at 600 s) - and it installs with
    an order of magnitude to spare. So does the one-count-above-the-
    stall-floor refit the suite already pins, whose dispense flow is
    0.00999 mL/min.
    """
    slow = _cal(a=1e-5, dispense_duty=1000.0)  # 0.01 mL/min
    barely = _cal(a=0.01, b=-10.0, dispense_duty=1001.0)  # 0.00999

    assert slow.flow_at(1000.0) > MIN_DISPENSE_FLOW
    assert slow.installable_reason() is None
    assert barely.installable_reason() is None


@pytest.mark.parametrize(
    "field",
    ["a", "b", "min_duty", "max_duty", "dispense_duty", "r2"],
)
@pytest.mark.parametrize(
    "bad",
    [float("nan"), float("inf"), float("-inf")],
)
def test_installable_reason_rejects_a_non_finite_field(
    field: str,
    bad: float,
) -> None:
    """A non-finite field is refused before any comparison is made.

    Regression: every one of the ten branches below the finiteness
    check is a comparison, and a comparison against NaN is False, so a
    NaN slope walked through all of them and came out *installable*.
    `CalibrationRun.record_point()` accepted an operator's `inf` from a
    generic OPC client, the old fitter's `a <= 0` did not catch the NaN
    slope it produced, `json.dumps` wrote the literal `NaN`, and
    `load_calibration`'s `a <= 0` reloaded it at every boot. The duty
    that came out reached `int(nan)` in `PlcActuator.write`, whose
    ValueError propagates out of the sampling loop and stops every
    reactor on the server.
    """
    cal = _cal(**{field: bad})

    reason = cal.installable_reason()

    assert reason is not None
    assert "finite" in reason
    assert field in reason


def test_a_non_finite_field_is_refused_before_the_comparisons() -> None:
    """The finiteness check has to come first, not last.

    A NaN slope is also a non-positive slope as far as the operator is
    concerned, but `nan <= 0` is False - so if the ordering ever flips,
    the branch that would catch it cannot.
    """
    cal = _cal(a=float("nan"))

    assert (cal.a <= 0) is False
    assert "not a finite number" in cal.installable_reason()
