"""Operator workflow for server-owned pH PID relay autotuning."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, replace
from typing import Any

from nicegui import ui

from reactors_czlab.gui.components.confirm import confirm, in_flight
from reactors_czlab.gui.components.pairing import device_id, short_name
from reactors_czlab.gui.components.shell import (
    disable_when_read_only,
    header,
    not_connected_notice,
    reactor_tabs,
)
from reactors_czlab.gui.controllers.autotune import (
    FormState,
    GainView,
    PreflightView,
    RunView,
    ViewMode,
    calibration_timestamp,
    preflight_from_payload,
    replace_selection,
    run_from_payload,
    validate_form,
)
from reactors_czlab.gui.state import STATE

_logger = logging.getLogger("gui")

POLL_SECONDS = 2.0


@dataclass
class _Elements:
    """Stable references mutated by status polling and handlers."""

    setup: ui.element
    running: ui.element
    identified: ui.element
    failed: ui.element
    maintenance: ui.element
    sensor: ui.select
    base: ui.select
    acid: ui.select
    setpoint: ui.number
    base_dose: ui.number
    acid_dose: ui.number
    hysteresis: ui.number
    max_minutes: ui.number
    phosphate: ui.number
    base_molar: ui.number
    acid_molar: ui.number
    budget_override: ui.number
    other_loops_ack: ui.checkbox
    budget_ack: ui.checkbox
    calibration_base: ui.label
    calibration_acid: ui.label
    preflight_message: ui.label
    preflight_budget: ui.label
    preflight_warnings: ui.label
    start: ui.button
    phase: ui.label
    status_message: ui.label
    current_ph: ui.label
    relay: ui.label
    doses: ui.label
    sigma: ui.label
    cycles: ui.label
    dose: ui.label
    elapsed: ui.label
    run_warnings: ui.label
    chart: ui.plotly
    failure_message: ui.label
    result: ui.label
    gains: dict[str, ui.label]
    rule: ui.select
    derivative_warning: ui.label
    apply: ui.button
    scale: ui.button
    reapply: ui.button


@ui.page("/reactor/{reactor}/autotune")
async def autotune_page(reactor: str) -> None:
    """Build the autotune screen once and adopt any server-owned run."""
    header(reactor)
    with ui.column().classes("w-full").style("padding: 1rem; gap: 1rem"):
        reactor_tabs(reactor, "PID autotuning")

        if not STATE.connected:
            not_connected_notice()
            return

        sensors, pumps = _device_options(reactor)
        if not sensors:
            ui.label("No sensor with a pH channel is available.").classes(
                "text-orange-700",
            )
            return
        if len(pumps) < 2:
            ui.label(
                "PID autotuning needs two calibrated pump nodes.",
            ).classes("text-orange-700")
            return

        pump_ids = list(pumps)
        defaults = FormState(
            sensor_id=next(iter(sensors)),
            base_id=pump_ids[0],
            acid_id=pump_ids[1],
        )
        try:
            initial = await _status(reactor, defaults.base_id)
        except (LookupError, OSError, TypeError, ValueError) as err:
            _logger.warning(
                "Could not load initial autotune status for %s: %s",
                reactor,
                err,
            )
            initial = RunView(
                mode=ViewMode.failed,
                phase="failed",
                ok=False,
                message=f"Could not read autotune status: {err}",
                form=defaults,
            )
        form = replace_selection(
            initial.form,
            sensor_id=defaults.sensor_id,
            base_id=defaults.base_id,
            acid_id=defaults.acid_id,
        )
        initial = replace(initial, form=form)

        ui.label("pH PID autotuning").classes("text-xl font-semibold")
        ui.label(
            "The server runs a non-blocking relay experiment. Gains are "
            "reviewed here and are never applied automatically.",
        ).classes("text-sm text-gray-600")

        page_state: dict[str, Any] = {
            "view": initial,
            "preflight": None,
            "preflight_form": None,
            "generation": STATE.generation,
            "polling": False,
        }
        elements = _build_elements(
            reactor,
            sensors,
            pumps,
            form,
            page_state,
        )
        page_state["elements"] = elements

        await _load_calibrations(reactor, elements)
        _render(elements, initial, None)

    # The timer mutates only existing labels, visibility and Plotly data.
    # It never clears a container or creates an actionable element.
    ui.timer(
        POLL_SECONDS,
        lambda: _poll(reactor, page_state),
    )


def _device_options(reactor: str) -> tuple[dict[str, str], dict[str, str]]:
    """Return full OPC ids with short AddressBook names as labels."""
    if STATE.book is None:
        return ({}, {})
    sensors = {
        device_id(reactor, name): name
        for name, refs in STATE.book.sensors(reactor).items()
        if any(ref.channel.lower() == "ph" for ref in refs)
    }
    pumps = {
        device_id(reactor, name): name
        for name in STATE.book.actuators(reactor)
        if STATE.book.has_method(reactor, name, "autotune_status")
    }
    return (dict(sorted(sensors.items())), dict(sorted(pumps.items())))


def _build_elements(
    reactor: str,
    sensors: dict[str, str],
    pumps: dict[str, str],
    form: FormState,
    page_state: dict[str, Any],
) -> _Elements:
    """Create every input, action and result panel exactly once."""
    setup = ui.column().classes("w-full").style("gap: 1rem")
    with setup:
        with ui.card().classes("w-full"):
            ui.label("Run setup").classes("text-lg font-semibold")
            with ui.element("div").style(
                "display: flex; flex-wrap: wrap; width: 100%; gap: 1rem",
            ):
                sensor = ui.select(
                    sensors,
                    value=form.sensor_id,
                    label="pH sensor",
                ).style("flex: 1 1 12rem; min-width: 0")
                base = ui.select(
                    pumps,
                    value=form.base_id,
                    label="Base pump",
                ).style("flex: 1 1 12rem; min-width: 0")
                acid = ui.select(
                    pumps,
                    value=form.acid_id,
                    label="Acid pump",
                ).style("flex: 1 1 12rem; min-width: 0")

            with ui.row().classes("items-center flex-wrap").style(
                "gap: 1rem",
            ):
                ui.badge(
                    "Base pump: backwards=False",
                    color="blue",
                ).classes("text-sm")
                ui.badge(
                    "Acid pump: backwards=True",
                    color="deep-orange",
                ).classes("text-sm")

            calibration_base = ui.label("").classes(
                "text-xs text-gray-600 font-mono",
            )
            calibration_acid = ui.label("").classes(
                "text-xs text-gray-600 font-mono",
            )

        with ui.card().classes("w-full"):
            ui.label("Relay and chemistry").classes("text-lg font-semibold")
            with ui.element("div").style(
                "display: flex; flex-wrap: wrap; width: 100%; gap: 1rem",
            ):
                setpoint = _number(
                    "Setpoint (pH)", form.setpoint, "%.3f",
                )
                base_dose = _number(
                    "Base dose (mL)", form.base_dose_ml, "%.3f",
                )
                acid_dose = _number(
                    "Acid dose (mL)", form.acid_dose_ml, "%.3f",
                )
                hysteresis = _number(
                    "Hysteresis (pH)", form.hysteresis_ph, "%.3f",
                )
                max_minutes = _number(
                    "Maximum time (min)", form.max_minutes, "%.1f",
                )
                phosphate = _number(
                    "Phosphate (mM)", form.phosphate_mm, "%.1f",
                )
                base_molar = _number(
                    "Base titrant (M)", form.base_molar, "%.3f",
                )
                acid_molar = _number(
                    "Acid titrant (M)", form.acid_molar, "%.3f",
                )

            ui.separator()
            ui.label(
                "Leave the override blank to use the chemistry-computed "
                "combined dose budget.",
            ).classes("text-sm text-gray-600")
            with ui.row().classes("items-center flex-wrap").style(
                "gap: 1rem",
            ):
                budget_override = ui.number(
                    "Dose budget override (mL)",
                    value=form.dose_budget_override_ml,
                    format="%.3f",
                ).style("min-width: 15rem")
                budget_ack = ui.checkbox(
                    "I explicitly acknowledge this budget override",
                    value=form.acknowledge_budget_override,
                )
            other_loops_ack = ui.checkbox(
                "I acknowledge that the pH excursion may affect other loops",
                value=form.acknowledge_other_loops,
            )

            preflight_message = ui.label("").classes(
                "text-sm whitespace-pre-wrap",
            )
            preflight_budget = ui.label("").classes(
                "text-sm font-mono",
            )
            preflight_warnings = ui.label("").classes(
                "text-sm text-orange-700 whitespace-pre-wrap",
            )

            with ui.row().classes("items-center flex-wrap").style(
                "gap: 0.75rem",
            ):
                preflight_button = disable_when_read_only(
                    ui.button("Check preflight").props("outline"),
                )
                # Start has two independent gates (writable connection and a
                # matching accepted preflight), so a one-property enabled
                # binding would overwrite the safety gate whenever writable
                # remains True.
                start = ui.button("Review and start", color="primary")
                start.tooltip("Requires an accepted preflight")
                start.bind_enabled_from(
                    STATE,
                    "writable",
                    backward=lambda writable: bool(
                        writable
                        and isinstance(
                            page_state.get("preflight"),
                            PreflightView,
                        )
                        and page_state["preflight"].ok
                        and page_state.get("preflight_form") is not None
                    ),
                )
                start.disable()

    running = ui.column().classes("w-full").style("gap: 1rem")
    with running:
        with ui.card().classes("w-full"):
            with ui.row().classes("items-center justify-between w-full"):
                phase = ui.label("").classes("text-lg font-semibold")
                abort = disable_when_read_only(
                    ui.button("Abort", color="negative"),
                )
            status_message = ui.label("").classes(
                "text-sm whitespace-pre-wrap",
            )
            run_warnings = ui.label("").classes(
                "text-sm text-orange-700 whitespace-pre-wrap",
            )

        with ui.element("div").style(
            "display: flex; flex-wrap: wrap; width: 100%; gap: 1rem",
        ):
            with ui.element("div").style(
                "flex: 2 1 34rem; min-width: 0",
            ), ui.card().classes("w-full"):
                chart = ui.plotly(_trace_figure(None)).style(
                    "height: 24rem; width: 100%",
                )
            with ui.element("div").style(
                "flex: 1 1 18rem; min-width: 0",
            ), ui.card().classes("w-full"):
                current_ph = _metric_label()
                relay = _metric_label()
                doses = _metric_label()
                sigma = _metric_label()
                cycles = _metric_label()
                dose = _metric_label()
                elapsed = _metric_label()

    failed = ui.column().classes("w-full").style("gap: 1rem")
    with failed, ui.card().classes("w-full border border-red-300"):
        ui.label("Autotune did not identify gains").classes(
            "text-lg font-semibold text-red-700",
        )
        failure_message = ui.label("").classes(
            "text-sm whitespace-pre-wrap",
        )
        ui.label(
            "Review the server message and run setup before trying again.",
        ).classes("text-sm text-gray-600")

    identified = ui.column().classes("w-full").style("gap: 1rem")
    with identified, ui.card().classes("w-full"):
        ui.label("Identified relay response").classes(
            "text-lg font-semibold",
        )
        result = ui.label("").classes("font-mono text-sm")
        gains: dict[str, ui.label] = {}
        with ui.element("div").style(
            "overflow-x: auto; width: 100%",
        ):
            for rule_name in ("TL-PI", "ZN-PID", "TL-PID", "SIMC"):
                gains[rule_name] = ui.label("").classes(
                    "font-mono text-sm",
                )
        rule = ui.select(
            ["TL-PI", "ZN-PID", "TL-PID", "SIMC"],
            value="TL-PI",
            label="Tuning rule",
        ).style("min-width: 12rem")
        derivative_warning = ui.label(
            "Derivative action is enabled by this candidate. Confirm "
            "that the pH signal is suitable before applying it.",
        ).classes("text-sm text-orange-700")
        apply = disable_when_read_only(
            ui.button("Apply gains", color="primary"),
        )

    maintenance = ui.column().classes("w-full").style("gap: 1rem")
    with maintenance, ui.card().classes("w-full"):
        ui.label("Previously applied tune").classes("text-lg font-semibold")
        ui.label(
            "These server-audited actions revalidate both pumps, their "
            "pairings, directions and calibrations before changing gains.",
        ).classes("text-sm text-gray-600")
        with ui.row().classes("items-center flex-wrap").style("gap: 0.75rem"):
            scale = disable_when_read_only(
                ui.button("Scale to current setpoint").props("outline"),
            )
            reapply = disable_when_read_only(
                ui.button("Reapply last tune").props("outline"),
            )

    elements = _Elements(
        setup=setup,
        running=running,
        identified=identified,
        failed=failed,
        maintenance=maintenance,
        sensor=sensor,
        base=base,
        acid=acid,
        setpoint=setpoint,
        base_dose=base_dose,
        acid_dose=acid_dose,
        hysteresis=hysteresis,
        max_minutes=max_minutes,
        phosphate=phosphate,
        base_molar=base_molar,
        acid_molar=acid_molar,
        budget_override=budget_override,
        other_loops_ack=other_loops_ack,
        budget_ack=budget_ack,
        calibration_base=calibration_base,
        calibration_acid=calibration_acid,
        preflight_message=preflight_message,
        preflight_budget=preflight_budget,
        preflight_warnings=preflight_warnings,
        start=start,
        phase=phase,
        status_message=status_message,
        current_ph=current_ph,
        relay=relay,
        doses=doses,
        sigma=sigma,
        cycles=cycles,
        dose=dose,
        elapsed=elapsed,
        run_warnings=run_warnings,
        chart=chart,
        failure_message=failure_message,
        result=result,
        gains=gains,
        rule=rule,
        derivative_warning=derivative_warning,
        apply=apply,
        scale=scale,
        reapply=reapply,
    )

    def invalidate(_: object = None) -> None:
        _clear_preflight(
            elements,
            page_state,
            "Parameters changed; check preflight again.",
        )

    async def selection_changed(_: object = None) -> None:
        _logger.info(
            "Operator changed autotune selection to sensor=%s base=%s acid=%s",
            sensor.value,
            base.value,
            acid.value,
        )
        invalidate()
        await _load_calibrations(reactor, elements)

    for control in (
        setpoint,
        base_dose,
        acid_dose,
        hysteresis,
        max_minutes,
        phosphate,
        base_molar,
        acid_molar,
        budget_override,
        other_loops_ack,
        budget_ack,
    ):
        control.on_value_change(invalidate)
    sensor.on_value_change(selection_changed)
    base.on_value_change(selection_changed)
    acid.on_value_change(selection_changed)

    async def check_preflight() -> None:
        _logger.info("Operator requested autotune preflight on %s", reactor)
        form_now = _read_form(elements)
        errors = validate_form(form_now)
        if errors:
            text = "\n".join(errors)
            elements.preflight_message.set_text(text)
            ui.notify(errors[0], type="warning")
            return
        with in_flight(preflight_button):
            try:
                raw = await STATE.call(
                    reactor,
                    short_name(form_now.base_id),
                    "autotune_preflight",
                    *form_now.opc_args(),
                )
                flight = preflight_from_payload(raw)
            except (LookupError, OSError, TypeError, ValueError) as err:
                _logger.warning("Autotune preflight failed: %s", err)
                ui.notify(f"Autotune preflight failed: {err}", type="negative")
                return
        page_state["preflight"] = flight
        page_state["preflight_form"] = form_now if flight.ok else None
        _render_preflight(elements, flight)
        ui.notify(flight.message, type="positive" if flight.ok else "negative")
        if flight.ok:
            if STATE.writable:
                elements.start.enable()
        else:
            elements.start.disable()

    async def start_run() -> None:
        _logger.info("Operator requested autotune start on %s", reactor)
        form_now = _read_form(elements)
        flight = page_state.get("preflight")
        if (
            not isinstance(flight, PreflightView)
            or not flight.ok
            or page_state.get("preflight_form") != form_now
        ):
            ui.notify("Check preflight for these parameters first", type="warning")
            return
        summary = (
            f"Sensor {short_name(form_now.sensor_id)}; base pump "
            f"{short_name(form_now.base_id)} (backwards=False); acid pump "
            f"{short_name(form_now.acid_id)} (backwards=True). Safety "
            f"{flight.safe_low:.3f}–{flight.safe_high:.3f} pH, up to "
            f"{form_now.max_minutes:.1f} minutes and "
            f"{flight.effective_budget_ml:.3f} mL combined dose."
        )
        if not await confirm("Start pH PID autotuning?", summary, danger=True):
            return
        # Start is different from ordinary repeatable actions: its accepted
        # preflight is a single-use authorization. Manage loading explicitly
        # so no generic finally block can re-enable a consumed authorization.
        elements.start.disable()
        elements.start.props("loading")
        try:
            await _action_and_readback(
                reactor,
                form_now.base_id,
                "autotune_start",
                page_state,
                *form_now.opc_args(),
            )
        finally:
            elements.start.props(remove="loading")
            if STATE.writable and page_state.get("preflight") is not None:
                elements.start.enable()
            else:
                elements.start.disable()

    async def abort_run() -> None:
        view = page_state["view"]
        _logger.info("Operator requested autotune abort on %s", reactor)
        if not await confirm(
            "Abort pH PID autotuning?",
            "Both selected pumps will stop and the partial run will remain "
            "in the server audit.",
            danger=True,
        ):
            return
        with in_flight(abort):
            await _action_and_readback(
                reactor,
                view.form.base_id,
                "autotune_abort",
                page_state,
            )

    async def apply_gains() -> None:
        view = page_state["view"]
        candidate = _gain(view, str(elements.rule.value))
        _logger.info(
            "Operator requested autotune rule %s on %s",
            elements.rule.value,
            reactor,
        )
        if candidate is None:
            ui.notify("That gain candidate is unavailable", type="warning")
            return
        message = (
            f"Apply {candidate.rule}: kp={candidate.kp:.6g}, "
            f"ki={candidate.ki:.6g}, kd={candidate.kd:.6g} to both "
            "selected PID controllers?"
        )
        if candidate.has_derivative:
            message += " This enables derivative action."
        if not await confirm("Apply autotune gains?", message):
            return
        with in_flight(elements.apply):
            await _action_and_readback(
                reactor,
                view.form.base_id,
                "autotune_apply",
                page_state,
                candidate.rule,
            )

    async def scale_gains() -> None:
        view = page_state["view"]
        base_id = view.form.base_id or str(elements.base.value)
        _logger.info("Operator requested autotune scale on %s", reactor)
        try:
            raw = await STATE.call(
                reactor,
                short_name(base_id),
                "get_control_config",
            )
            config = json.loads(str(raw))
            target = float(config["setpoint"])
        except (KeyError, LookupError, OSError, TypeError, ValueError) as err:
            ui.notify(f"Could not read the current setpoint: {err}", type="negative")
            return
        if not await confirm(
            "Scale the last applied tune?",
            f"Scale the audited gains to the controllers' current shared "
            f"setpoint of pH {target:.3f}, then apply them to both pumps?",
        ):
            return
        with in_flight(elements.scale):
            await _action_and_readback(
                reactor,
                base_id,
                "autotune_scale_to_setpoint",
                page_state,
                target,
            )

    async def reapply_gains() -> None:
        view = page_state["view"]
        base_id = view.form.base_id or str(elements.base.value)
        _logger.info("Operator requested autotune reapply on %s", reactor)
        if not await confirm(
            "Reapply the last tune?",
            "Revalidate the audited selection and reapply its saved gains "
            "to both PID controllers?",
        ):
            return
        with in_flight(elements.reapply):
            await _action_and_readback(
                reactor,
                base_id,
                "autotune_reapply_last",
                page_state,
            )

    def rule_changed(_: object = None) -> None:
        _show_derivative_warning(elements, page_state["view"])

    preflight_button.on_click(check_preflight)
    elements.start.on_click(start_run)
    abort.on_click(abort_run)
    elements.apply.on_click(apply_gains)
    elements.scale.on_click(scale_gains)
    elements.reapply.on_click(reapply_gains)
    elements.rule.on_value_change(rule_changed)
    return elements


def _number(label: str, value: float, fmt: str) -> ui.number:
    control = ui.number(label, value=value, format=fmt)
    control.style("flex: 1 1 10rem; min-width: 0")
    return control


def _metric_label() -> ui.label:
    return ui.label("").classes("font-mono text-sm")


def _read_form(elements: _Elements) -> FormState:
    """Copy widget values into the pure, comparable form model."""
    return FormState(
        sensor_id=str(elements.sensor.value or ""),
        base_id=str(elements.base.value or ""),
        acid_id=str(elements.acid.value or ""),
        setpoint=_value(elements.setpoint.value),
        base_dose_ml=_value(elements.base_dose.value),
        acid_dose_ml=_value(elements.acid_dose.value),
        hysteresis_ph=_value(elements.hysteresis.value),
        max_minutes=_value(elements.max_minutes.value),
        phosphate_mm=_value(elements.phosphate.value),
        base_molar=_value(elements.base_molar.value),
        acid_molar=_value(elements.acid_molar.value),
        dose_budget_override_ml=(
            None
            if elements.budget_override.value is None
            else _value(elements.budget_override.value)
        ),
        acknowledge_other_loops=bool(elements.other_loops_ack.value),
        acknowledge_budget_override=bool(elements.budget_ack.value),
    )


def _value(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


async def _status(reactor: str, base_id: str) -> RunView:
    """Read and parse status through the current address-book generation."""
    raw = await STATE.call(
        reactor,
        short_name(base_id),
        "autotune_status",
    )
    return run_from_payload(raw)


async def _action_and_readback(
    reactor: str,
    base_id: str,
    method: str,
    page_state: dict[str, Any],
    *args: object,
) -> None:
    """Call one short action, show its exact message, then read status."""
    previous: RunView = page_state["view"]
    try:
        response = run_from_payload(
            await STATE.call(
                reactor,
                short_name(base_id),
                method,
                *args,
            ),
        )
        ui.notify(response.message, type="positive" if response.ok else "negative")
    except (LookupError, OSError, TypeError, ValueError) as err:
        _logger.warning("Autotune action %s failed: %s", method, err)
        ui.notify(f"{method} failed: {err}", type="negative")
        return
    if response.ok and (
        method == "autotune_start" or response.phase != previous.phase
    ):
        _clear_preflight(page_state["elements"], page_state)
    try:
        view = await _status(reactor, base_id)
    except (LookupError, OSError, TypeError, ValueError) as err:
        _logger.warning("Autotune readback after %s failed: %s", method, err)
        ui.notify(f"Could not read autotune status: {err}", type="negative")
        return
    page_state["view"] = view
    _render(page_state["elements"], view, page_state.get("preflight"))


async def _load_calibrations(reactor: str, elements: _Elements) -> None:
    """Read fitted timestamps on demand when either selection changes."""
    for role, selected, label in (
        ("Base", elements.base.value, elements.calibration_base),
        ("Acid", elements.acid.value, elements.calibration_acid),
    ):
        if not selected:
            label.set_text(f"{role} calibration: no pump selected")
            continue
        try:
            raw = await STATE.call(
                reactor,
                short_name(str(selected)),
                "get_calibration",
            )
            stamp = calibration_timestamp(raw)
        except (LookupError, OSError) as err:
            stamp = f"could not be read: {err}"
        label.set_text(
            f"{role} calibration ({short_name(str(selected))}): {stamp}",
        )


async def _poll(reactor: str, page_state: dict[str, Any]) -> None:
    """Adopt server state after samples, navigation away/back or reconnect."""
    if page_state.get("polling") or not STATE.connected:
        return
    elements: _Elements = page_state["elements"]
    view: RunView = page_state["view"]
    base_id = view.form.base_id or str(elements.base.value or "")
    if not base_id:
        return
    page_state["polling"] = True
    try:
        generation_changed = page_state["generation"] != STATE.generation
        if generation_changed:
            _clear_preflight(
                elements,
                page_state,
                "Connection changed; check preflight again.",
            )
            sensors, pumps = _device_options(reactor)
            elements.sensor.set_options(sensors)
            elements.base.set_options(pumps)
            elements.acid.set_options(pumps)
            page_state["generation"] = STATE.generation
        updated = await _status(reactor, base_id)
    except (LookupError, OSError, TypeError, ValueError) as err:
        _logger.warning("Could not poll autotune status: %s", err)
        elements.status_message.set_text(f"Could not read status: {err}")
        return
    finally:
        page_state["polling"] = False
    if updated.phase != view.phase:
        _clear_preflight(elements, page_state)
    page_state["view"] = updated
    _render(elements, updated, page_state.get("preflight"))


def _render(
    elements: _Elements,
    view: RunView,
    flight: PreflightView | None,
) -> None:
    """Copy a pure view onto the already-built component tree."""
    elements.setup.set_visibility(
        view.mode in {ViewMode.setup, ViewMode.failed},
    )
    elements.running.set_visibility(view.mode is ViewMode.running)
    elements.identified.set_visibility(view.mode is ViewMode.identified)
    elements.failed.set_visibility(view.mode is ViewMode.failed)
    elements.maintenance.set_visibility(view.mode is not ViewMode.running)

    elements.phase.set_text(f"Phase: {view.phase}")
    elements.status_message.set_text(view.message)
    elements.failure_message.set_text(view.message)
    elements.current_ph.set_text(
        f"Current pH: {_format(view.current_ph, 3)}",
    )
    elements.relay.set_text(f"Relay direction: {view.relay_direction}")
    elements.doses.set_text(
        "Adjusted doses: base "
        f"{_format(view.adjusted_base_ml, 3)} mL, acid "
        f"{_format(view.adjusted_acid_ml, 3)} mL",
    )
    elements.sigma.set_text(
        f"Noise sigma: {_format(view.noise_sigma, 4)} pH",
    )
    elements.cycles.set_text(
        f"Cycles: {view.settling_cycles} settling, "
        f"{view.clean_cycles} clean",
    )
    elements.dose.set_text(
        f"Combined dose: {view.dose_actual_ml:.3f} / "
        f"{_format(view.dose_budget_ml, 3)} mL",
    )
    elements.elapsed.set_text(
        f"Elapsed: {_elapsed(view.elapsed_seconds)}",
    )
    elements.run_warnings.set_text("\n".join(view.warnings))
    elements.chart.update_figure(_trace_figure(view))

    elements.result.set_text(
        f"Ku: {_format(view.ku, 6)} mL/pH · "
        f"Pu: {_format(view.pu_seconds, 3)} s",
    )
    gain_by_rule = {gain.rule: gain for gain in view.gains}
    for rule, label in elements.gains.items():
        candidate = gain_by_rule.get(rule)
        if candidate is None:
            label.set_text(f"{rule}: unavailable")
        else:
            label.set_text(
                f"{rule}: kp={candidate.kp:.6g}, "
                f"ki={candidate.ki:.6g}, kd={candidate.kd:.6g}",
            )
    elements.apply.set_enabled(view.mode is ViewMode.identified and STATE.writable)
    _show_derivative_warning(elements, view)
    _render_preflight(elements, flight)


def _render_preflight(
    elements: _Elements,
    flight: PreflightView | None,
) -> None:
    if flight is None:
        return
    elements.preflight_message.set_text(flight.message)
    if flight.default_budget_ml is None:
        elements.preflight_budget.set_text("")
    else:
        elements.preflight_budget.set_text(
            f"Chemistry-computed budget: {flight.default_budget_ml:.3f} mL; "
            f"effective budget: {flight.effective_budget_ml:.3f} mL; "
            f"safety: {flight.safe_low:.3f}–{flight.safe_high:.3f} pH",
        )
    elements.preflight_warnings.set_text("\n".join(flight.warnings))


def _clear_preflight(
    elements: _Elements,
    page_state: dict[str, Any],
    message: str = "",
) -> None:
    """Forget a preflight once its exact candidate is no longer current."""
    page_state["preflight"] = None
    page_state["preflight_form"] = None
    elements.start.disable()
    elements.preflight_message.set_text(message)
    elements.preflight_budget.set_text("")
    elements.preflight_warnings.set_text("")


def _show_derivative_warning(elements: _Elements, view: RunView) -> None:
    candidate = _gain(view, str(elements.rule.value))
    elements.derivative_warning.set_visibility(
        candidate is not None and candidate.has_derivative,
    )


def _gain(view: RunView, rule: str) -> GainView | None:
    return next((item for item in view.gains if item.rule == rule), None)


def _trace_figure(view: RunView | None) -> dict[str, Any]:
    """Build the live pH trace with setpoint, relay and safety bands."""
    trace = () if view is None else view.trace
    x = [point.seconds for point in trace]
    ph = [point.ph for point in trace]
    data: list[dict[str, Any]] = [
        {
            "name": "pH",
            "type": "scattergl",
            "mode": "lines+markers",
            "x": x,
            "y": ph,
            "line": {"color": "#2563eb"},
        },
    ]
    if view is not None:
        for name, value, dash, color in (
            ("Setpoint", view.form.setpoint, "solid", "#111827"),
            (
                "+ hysteresis",
                view.form.setpoint + view.form.hysteresis_ph,
                "dot",
                "#7c3aed",
            ),
            (
                "- hysteresis",
                view.form.setpoint - view.form.hysteresis_ph,
                "dot",
                "#7c3aed",
            ),
            ("Safety high", view.safe_high, "dash", "#dc2626"),
            ("Safety low", view.safe_low, "dash", "#dc2626"),
        ):
            if value is None:
                continue
            data.append(
                {
                    "name": name,
                    "type": "scatter",
                    "mode": "lines",
                    "x": x,
                    "y": [value] * len(x),
                    "line": {"dash": dash, "color": color},
                    "hoverinfo": "skip",
                },
            )
    shapes: list[dict[str, Any]] = []
    if view is not None:
        if view.safe_low is not None and view.safe_high is not None:
            shapes.append(
                {
                    "type": "rect",
                    "xref": "paper",
                    "x0": 0,
                    "x1": 1,
                    "y0": view.safe_low,
                    "y1": view.safe_high,
                    "fillcolor": "rgba(34, 197, 94, 0.08)",
                    "line": {"width": 0},
                    "layer": "below",
                },
            )
        shapes.append(
            {
                "type": "rect",
                "xref": "paper",
                "x0": 0,
                "x1": 1,
                "y0": view.form.setpoint - view.form.hysteresis_ph,
                "y1": view.form.setpoint + view.form.hysteresis_ph,
                "fillcolor": "rgba(124, 58, 237, 0.10)",
                "line": {"width": 0},
                "layer": "below",
            },
        )
    return {
        "data": data,
        "layout": {
            "title": "Live pH relay trace",
            "xaxis": {"title": "Elapsed time (s)"},
            "yaxis": {"title": "pH"},
            "margin": {"l": 55, "r": 20, "t": 45, "b": 50},
            "legend": {"orientation": "h"},
            "shapes": shapes,
            "uirevision": "autotune",
        },
    }


def _format(value: float | None, places: int) -> str:
    return "—" if value is None else f"{value:.{places}f}"


def _elapsed(seconds: float) -> str:
    minutes, remainder = divmod(max(seconds, 0.0), 60.0)
    return f"{int(minutes):02d}:{remainder:04.1f}"
