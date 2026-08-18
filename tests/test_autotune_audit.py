"""Persistence and gain-preparation tests for pH autotune audit history."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from reactors_czlab.core.autotune import (
    AutotuneContext,
    AutotuneCoordinator,
    RelayTuneConfig,
)
from reactors_czlab.core.autotune_audit import AutotuneAudit, audit_path
from reactors_czlab.core.data import ControlConfig, ControlMethod, OutputUnit
from reactors_czlab.core.dispenser import Dispenser
from reactors_czlab.core.ph_model import Chemistry, buffering_intensity


def _context(make_calibrated_actuator, clock):
    base = make_calibrated_actuator("R0:base")
    acid = make_calibrated_actuator("R0:acid")
    for actuator, backwards in ((base, False), (acid, True)):
        actuator.control_period = 10.0
        assert actuator.set_control_config(
            ControlConfig(ControlMethod.pid, setpoint=7.0, output_unit=OutputUnit.volume, backwards=backwards),
        ) is None
        actuator.dispenser = Dispenser(OutputUnit.volume, actuator.channel, 10.0, clock=clock)
    sensor = SimpleNamespace(id="R0:ph", channels=[SimpleNamespace(units="pH")])
    pairings = {sensor.id: [(base.id, 0), (acid.id, 0)]}
    return (
        AutotuneContext("R0", 5.0, {sensor.id: sensor}, {base.id: base, acid.id: acid}, lambda: pairings),
        base,
        acid,
        pairings,
    )


def _audit(tmp_path, **kwargs):
    return AutotuneAudit(
        "R0",
        directory=lambda: tmp_path,
        utcnow=lambda: datetime(2026, 8, 17, 12, tzinfo=UTC),
        run_id=lambda: "run-1",
        **kwargs,
    )


def _candidate(reference=7.0, gains=(5.0, 0.2, 0.0)):
    return {
        "reactor_id": "R0", "sensor_id": "R0:ph", "base_id": "R0:base", "acid_id": "R0:acid",
        "channel_index": 0, "rule": "TL-PI", "reference_ph": reference, "tuned_ph": 7.0,
        "chemistry": {"phosphate_molar": 0.014, "base_molar": 0.5, "acid_molar": 0.5},
        "gains": {"kp": gains[0], "ki": gains[1], "kd": gains[2]},
    }


def _identify(run, clock) -> None:
    for index in range(7):
        clock.advance(index * 10.0 - clock.now)
        run.sample(7.0)
    for ph in [7.08, 7.08, 6.92, 6.92] * 12:
        run.sample(ph)
        elapsed = 0.0
        while elapsed < 10.0 and (run.base.channel.value or run.acid.channel.value):
            clock.advance(0.05)
            elapsed += 0.05
            run.tick()
        clock.advance(10.0 - elapsed)
        if not run.is_active:
            return


def test_started_and_terminal_run_history_is_appended(tmp_path, make_calibrated_actuator, clock) -> None:
    context, base, acid, _ = _context(make_calibrated_actuator, clock)
    run_ids = iter(("run-1", "run-2"))
    audit = AutotuneAudit(
        "R0", directory=lambda: tmp_path, run_id=lambda: next(run_ids),
    )
    coordinator = AutotuneCoordinator(context, clock=clock, audit=audit)
    run = coordinator.start("R0:ph", base.id, acid.id, RelayTuneConfig(acknowledge_other_loops=True))
    run.abort("operator stopped test")

    stored = audit.read()
    assert stored.ok
    record = stored.data["runs"][0]
    assert record["run_id"] == "run-1"
    assert record["selection"]["channel_index"] == 0
    assert record["phase"] == "aborted"
    assert record["terminal_reason"] == "operator stopped test"

    coordinator.start("R0:ph", base.id, acid.id, RelayTuneConfig(acknowledge_other_loops=True)).abort("second stop")
    records = audit.read().data["runs"]
    assert [(record["run_id"], record["terminal_reason"]) for record in records] == [
        ("run-1", "operator stopped test"),
        ("run-2", "second stop"),
    ]


def test_identified_and_failed_lifecycle_records_are_audited(tmp_path, make_calibrated_actuator, clock) -> None:
    context, base, acid, _ = _context(make_calibrated_actuator, clock)
    ids = iter(("identified", "failed"))
    audit = AutotuneAudit("R0", directory=lambda: tmp_path, run_id=lambda: next(ids))
    coordinator = AutotuneCoordinator(context, clock=clock, audit=audit)
    run = coordinator.start("R0:ph", base.id, acid.id, RelayTuneConfig(acknowledge_other_loops=True))
    _identify(run, clock)
    assert run.phase.value == "identified"
    record = audit.read().data["runs"][0]
    assert record["phase"] == "identified"
    assert record["started_utc"] and record["ended_utc"]
    assert record["baseline_sigma"] >= 0
    assert record["adjusted_doses_ml"]["base"] > 0
    assert record["adjusted_boluses_ml"] == record["adjusted_doses_ml"]
    assert record["initial_boluses_ml"] == record["initial_doses_ml"]
    assert record["actual_dose_ml"] > 0
    assert record["identification"]["Ku"] > 0 and record["identification"]["Pu"] > 0
    assert record["candidates"]["TL-PI"]["kp"] > 0
    assert record["trace"] and record["switch_times"] and record["cycles"]

    clock.now = 0.0
    failed = coordinator.start("R0:ph", base.id, acid.id, RelayTuneConfig(acknowledge_other_loops=True))
    for index, value in enumerate([7.0, 7.015, 6.988, 7.02, 6.982, 7.011, 6.995]):
        clock.advance(index * 10.0 - clock.now)
        failed.sample(value)
    failure_record = audit.read().data["runs"][1]
    assert failure_record["phase"] == "failed"
    assert "2*sigma" in failure_record["terminal_reason"]


def test_atomic_replacement_leaves_prior_document_when_replace_fails(tmp_path) -> None:
    audit = _audit(tmp_path)
    assert audit.record_apply_success(_candidate()).ok
    before = audit.path.read_text(encoding="utf-8")

    def fail_replace(_source, _target):
        raise OSError("injected replace failure")

    failing = _audit(tmp_path, replace_file=fail_replace)
    result = failing.record_scale_success(_candidate(7.1))
    assert not result.ok
    assert audit.path.read_text(encoding="utf-8") == before
    assert audit.path.with_suffix(".json.tmp").exists()


@pytest.mark.parametrize(
    "raw",
    ["{", "[]", json.dumps({"version": 99, "reactor_id": "R0", "runs": [], "events": [], "latest_applied": None})],
)
def test_bad_audit_documents_are_visible_refusals(tmp_path, raw) -> None:
    audit = _audit(tmp_path)
    audit.path.write_text(raw, encoding="utf-8")
    result = audit.read()
    assert not result.ok
    assert "audit" in result.message


@pytest.mark.parametrize("run_id", ["", "duplicate"])
def test_empty_or_duplicate_run_ids_are_refused(tmp_path, run_id) -> None:
    audit = _audit(tmp_path)
    run = {"run_id": run_id, "selection": {"channel_index": 0}, "chemistry": {"phosphate_molar": 0.014, "base_molar": 0.5, "acid_molar": 0.5}, "safety": {"safe_low": 6.0, "safe_high": 8.0, "dose_budget_ml": 1.0}, "initial_doses_ml": {"base": 0.2, "acid": 0.2}}
    document = {"version": 1, "reactor_id": "R0", "runs": [run], "events": [], "latest_applied": None}
    if run_id == "duplicate":
        document["runs"].append(dict(run))
    audit.path.write_text(json.dumps(document), encoding="utf-8")
    assert not audit.read().ok


def test_generated_duplicate_run_id_refuses_without_corrupting_history(tmp_path, make_calibrated_actuator, clock) -> None:
    context, base, acid, _ = _context(make_calibrated_actuator, clock)
    audit = AutotuneAudit("R0", directory=lambda: tmp_path, run_id=lambda: "same-id")
    coordinator = AutotuneCoordinator(context, clock=clock, audit=audit)
    coordinator.start("R0:ph", base.id, acid.id, RelayTuneConfig(acknowledge_other_loops=True)).abort()
    before = audit.path.read_text(encoding="utf-8")
    second = coordinator.start("R0:ph", base.id, acid.id, RelayTuneConfig(acknowledge_other_loops=True))
    assert "already exists" in second.message
    assert audit.path.read_text(encoding="utf-8") == before
    assert audit.read().ok
    second.abort()


def test_legacy_dose_keys_are_loaded_and_reemitted_as_aliases(tmp_path) -> None:
    """Mixed-version audit files cross one isolated serialization adapter."""
    audit = _audit(tmp_path)
    run = {
        "run_id": "legacy",
        "selection": {"channel_index": 0},
        "chemistry": {
            "phosphate_molar": 0.014,
            "base_molar": 0.5,
            "acid_molar": 0.5,
        },
        "safety": {
            "safe_low": 6.0,
            "safe_high": 8.0,
            "dose_budget_ml": 1.0,
        },
        "initial_boluses_ml": {"base": 0.2, "acid": 0.2},
        "adjusted_boluses_ml": {"base": 0.3, "acid": 0.3},
    }
    audit.path.write_text(
        json.dumps(
            {
                "version": 1,
                "reactor_id": "R0",
                "runs": [run],
                "events": [],
                "latest_applied": None,
            },
        ),
        encoding="utf-8",
    )

    loaded = audit.read()

    assert loaded.ok
    record = loaded.data["runs"][0]
    assert record["initial_doses_ml"] == record["initial_boluses_ml"]
    assert record["adjusted_doses_ml"] == record["adjusted_boluses_ml"]


def test_missing_and_wrong_reactor_documents_are_refused(tmp_path) -> None:
    audit = _audit(tmp_path)
    assert not audit.read().ok
    document = {"version": 1, "reactor_id": "R1", "runs": [], "events": [], "latest_applied": None}
    audit.path.write_text(json.dumps(document), encoding="utf-8")
    assert not audit.read().ok


def test_nested_nonfinite_or_bool_numbers_are_refused(tmp_path) -> None:
    audit = _audit(tmp_path)
    document = {"version": 1, "reactor_id": "R0", "runs": [], "events": [], "latest_applied": _candidate()}
    document["latest_applied"]["gains"]["kp"] = True
    audit.path.write_text(json.dumps(document), encoding="utf-8")
    assert not audit.read().ok
    document["latest_applied"]["gains"]["kp"] = float("nan")
    audit.path.write_text(json.dumps(document), encoding="utf-8")
    assert not audit.read().ok


def test_safe_reactor_filename_rejects_path_escape(tmp_path) -> None:
    assert audit_path("R0", directory=lambda: tmp_path) == tmp_path / "R0_ph_autotune.json"
    with pytest.raises(ValueError, match="unsafe reactor"):
        audit_path("../R0", directory=lambda: tmp_path)


def test_scale_uses_current_reference_without_compounding(tmp_path, make_calibrated_actuator, clock) -> None:
    context, base, acid, _ = _context(make_calibrated_actuator, clock)
    audit = _audit(tmp_path)
    original = _candidate()
    assert audit.record_apply_success(original).ok
    for actuator in (base, acid):
        actuator.controller.set_gains([5.0, 0.2, 0.0])

    for actuator in (base, acid):
        actuator.controller.setpoint = 6.8
    first = audit.prepare_scale(context, 6.8)
    assert first.ok
    first_gains = tuple(first.data["gains"][name] for name in ("kp", "ki", "kd"))
    for actuator in (base, acid):
        actuator.controller.set_gains(list(first_gains))
    assert audit.record_scale_success(first.data).ok

    for actuator in (base, acid):
        actuator.controller.setpoint = 6.5
    second = audit.prepare_scale(context, 6.5)
    assert second.ok
    beta_ratio = buffering_intensity(6.5, 0.014, Chemistry()) / buffering_intensity(7.0, 0.014, Chemistry())
    assert second.data["gains"]["kp"] == pytest.approx(5.0 * beta_ratio)
    for actuator in (base, acid):
        actuator.controller.set_gains([second.data["gains"][name] for name in ("kp", "ki", "kd")])
    assert audit.record_scale_success(second.data).ok
    document = audit.read().data
    assert [event["action"] for event in document["events"]] == ["apply", "scale", "scale"]
    latest = document["latest_applied"]
    assert (latest["sensor_id"], latest["base_id"], latest["acid_id"], latest["channel_index"]) == ("R0:ph", "R0:base", "R0:acid", 0)
    assert latest["rule"] == "TL-PI" and latest["tuned_ph"] == 7.0 and latest["reference_ph"] == 6.5
    assert latest["chemistry"]["phosphate_molar"] == 0.014
    assert latest["gains"] == second.data["gains"]

    base.controller.setpoint = 6.4
    assert not audit.prepare_scale(context, 6.5).ok


@pytest.mark.parametrize(
    "break_selection",
    [
        lambda context, base, acid, pairings: pairings.clear(),
        lambda context, base, acid, pairings: acid.set_control_config(
            ControlConfig(ControlMethod.manual, output_unit=OutputUnit.volume),
        ),
        lambda context, base, acid, pairings: setattr(acid.dispenser, "unit", OutputUnit.duty),
        lambda context, base, acid, pairings: setattr(acid.controller, "backwards", False),
        lambda context, base, acid, pairings: setattr(acid.channel.calibration, "fitted_at", ""),
        lambda context, base, acid, pairings: setattr(acid.controller, "setpoint", 6.9),
    ],
)
def test_reapply_revalidates_every_stage2_selection_guard(tmp_path, make_calibrated_actuator, clock, break_selection) -> None:
    context, base, acid, pairings = _context(make_calibrated_actuator, clock)
    audit = _audit(tmp_path)
    assert audit.record_apply_success(_candidate()).ok
    break_selection(context, base, acid, pairings)
    assert not audit.prepare_reapply(context).ok


def test_reapply_success_and_write_failures_are_refusals(tmp_path, make_calibrated_actuator, clock) -> None:
    context, _, _, _ = _context(make_calibrated_actuator, clock)
    audit = _audit(tmp_path)
    assert audit.record_apply_success(_candidate()).ok
    assert audit.prepare_reapply(context).ok
    assert audit.record_reapply_success(_candidate()).ok
    events = audit.read().data["events"]
    assert events[-1]["action"] == "reapply"

    def fail_replace(_source, _target):
        raise OSError("full disk")

    failed = _audit(tmp_path, replace_file=fail_replace).record_apply_failure("reapply", _candidate(), "atomic write failed")
    assert not failed.ok


def test_coordinator_survives_audit_write_failures(tmp_path, make_calibrated_actuator, clock) -> None:
    class BrokenAudit:
        def record_started(self, run):
            raise OSError("disk unavailable")

        def record_terminal(self, run):
            raise OSError("disk unavailable")

    context, base, acid, _ = _context(make_calibrated_actuator, clock)
    run = AutotuneCoordinator(context, clock=clock, audit=BrokenAudit()).start(
        "R0:ph", base.id, acid.id, RelayTuneConfig(acknowledge_other_loops=True),
    )
    assert "audit was not saved" in run.message
    run.abort()
    assert "audit callback failed" in run.message
    assert base.autotune_owner is acid.autotune_owner is None
    assert not base.calibrating and not acid.calibrating
