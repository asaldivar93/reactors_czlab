"""Calibration screens: Hamilton sensors and analog pumps.

Both are driven entirely by OPC method calls that return status strings
written for an operator - ``installable_reason()`` and the
``CalibrationRun`` messages. Those strings are shown verbatim: the core
is the authority on why a calibration was refused, and rewording it here
would be a second opinion that can drift from the first.
"""

from __future__ import annotations

import json
import logging

from nicegui import ui

from reactors_czlab.drivers.hamilton_model import (
    CALIBRATION_OK,
    CALIBRATION_POINTS,
    status_text,
)
from reactors_czlab.gui.components.confirm import confirm, in_flight
from reactors_czlab.gui.components.shell import (
    disable_when_read_only,
    header,
    not_connected_notice,
    reactor_tabs,
)
from reactors_czlab.gui.controllers.pump_calibration import (
    calibration_chart,
    duty_error,
    seconds_error,
    view_from_payload,
    volume_error,
    zero_flow_duty_error,
)
from reactors_czlab.gui.format import render_value
from reactors_czlab.gui.state import STATE

_logger = logging.getLogger("gui")

#: Reported by the server for a sensor with no calibration points.
UNSUPPORTED = "unsupported"

#: What drivers.hamilton_model.status_text() renders for an accepted point.
CALIBRATION_OK_TEXT = status_text(CALIBRATION_OK)

#: Actuators that are not pumps and have no calibration slot.
NOT_A_PUMP = "mfc"

#: Seconds allowed on top of the run the operator asked for, to cover
#: the connect and the round trip.
CALL_MARGIN_SECONDS = 30.0


@ui.page("/reactor/{reactor}/calibration/sensors")
async def sensor_calibration_page(reactor: str) -> None:
    """Two-point calibration for the Hamilton probes."""
    header(reactor)
    with ui.column().classes("w-full").style("padding: 1rem; gap: 1rem"):
        reactor_tabs(reactor, "Sensor calibration")

        if not STATE.connected:
            not_connected_notice()
            return

        ui.label("Sensor calibration").classes("text-xl font-semibold")
        ui.label(
            "CP1 and CP2 are set independently. Reading a point does not "
            "change it.",
        ).classes("text-sm text-gray-500")

        sensors = [
            name
            for name in sorted(STATE.book.sensors(reactor))
            if STATE.book.has_method(reactor, name, "read_calibration_status")
        ]
        if not sensors:
            ui.label(
                "No sensor on this reactor exposes a calibration read-back.",
            ).classes("text-gray-500")
            return

        for sensor in sensors:
            await _sensor_card(reactor, sensor)


async def read_point(
    reactor: str,
    sensor: str,
    point: str,
) -> tuple | None:
    """Read one calibration point, or None if the read failed.

    Returns the four out-arguments as they came back, including the
    ``unsupported`` marker - the caller decides what that means.
    """
    number = float(point.removeprefix("cp"))
    try:
        return _unpack(
            await STATE.call(
                reactor,
                sensor,
                "read_calibration_status",
                number,
            ),
        )
    except (LookupError, OSError) as err:
        _logger.warning("Could not read %s of %s: %s", point, sensor, err)
        return None


async def _sensor_card(reactor: str, sensor: str) -> None:
    """One sensor, with CP1 and CP2 side by side.

    Both points are read when the page opens, because the requirement
    is that the screen *shows* their current state - an operator should
    not have to press a button to find out what a probe holds.

    The cost is two status reads per Hamilton sensor per page open,
    each escalating and dropping the sensor's operator level on the
    RS485 bus the sampling loop shares. That is acceptable for an
    operator-initiated page open and is why the read is not on a timer.
    A sensor that cannot be calibrated answers without touching the bus
    at all.
    """
    statuses = {
        point: await read_point(reactor, sensor, point)
        for point in CALIBRATION_POINTS
    }

    with ui.card().classes("w-full"):
        ui.label(sensor).classes("text-lg font-semibold font-mono")

        if all(_is_unsupported(status) for status in statuses.values()):
            # Every sensor node carries the calibration methods, so the
            # address book cannot tell a Hamilton probe from a spectral
            # one. Asking is what distinguishes them - and offering an
            # Apply button that silently does nothing would be worse
            # than saying so.
            ui.label(
                "This sensor does not support calibration.",
            ).classes("text-sm text-gray-500")
            return

        # min-width: 0 on the flex children, or a wide status line stops
        # the two points sitting side by side and they stack instead.
        with ui.element("div").style(
            "display: flex; flex-wrap: wrap; gap: 1rem; width: 100%",
        ):
            for point in CALIBRATION_POINTS:
                with ui.element("div").style(
                    "flex: 1 1 18rem; min-width: 0",
                ):
                    _point_panel(reactor, sensor, point, statuses[point])


def _is_unsupported(status: tuple | None) -> bool:
    """Whether a read said the sensor has no calibration points."""
    return status is None or status[0] == UNSUPPORTED


def _point_panel(
    reactor: str,
    sensor: str,
    point: str,
    initial: tuple | None,
) -> None:
    """One calibration point: its state, and how to set it."""
    number = float(point.removeprefix("cp"))

    with ui.card().classes("w-full"):
        ui.label(point.upper()).classes("text-sm font-semibold")

        status_label = ui.label("").classes("text-sm font-mono")
        value_label = ui.label("").classes("text-sm text-gray-600 font-mono")
        quality_label = ui.label("").classes(
            "text-sm text-gray-600 font-mono",
        )
        process_label = ui.label("").classes(
            "text-sm text-gray-600 font-mono",
        )

        def show(status: tuple | None) -> None:
            """Render one read's four out-arguments.

            One render path for the read on page open, the Read button
            and the read-back after a write, so the three cannot drift.
            """
            if status is None:
                status_label.set_text("could not be read")
                return
            if _is_unsupported(status):
                status_label.set_text("unsupported by this sensor")
                return

            text, quality, value, process = status
            status_label.set_text(f"status: {text}")
            value_label.set_text(f"stored value: {render_value(value)}")
            quality_label.set_text(f"quality: {render_value(quality)}")
            if process is not None:
                process_label.set_text(
                    f"process value now: {render_value(process)}",
                )

        show(initial)

        async def read() -> None:
            """Re-read the point's stored state from the sensor."""
            _logger.info("Operator read %s of %s", point, sensor)
            with in_flight(read_button):
                show(await read_point(reactor, sensor, point))

        with ui.row().classes("items-end w-full").style("gap: 0.5rem"):
            new_value = ui.number(
                "New value",
                value=None,
                format="%.3f",
            ).style("flex: 1; min-width: 0")

            async def apply() -> None:
                """Write this point, then show what the sensor reports."""
                if new_value.value is None:
                    ui.notify("Enter a calibration value", type="warning")
                    return
                if not await confirm(
                    f"Write {point.upper()} on {sensor}?",
                    f"The probe will store {float(new_value.value):.3f} "
                    "as this calibration point.",
                ):
                    return
                _logger.info(
                    "Operator calibrating %s %s to %s",
                    sensor,
                    point,
                    new_value.value,
                )
                with in_flight(apply_button):
                    try:
                        result = await STATE.call(
                            reactor,
                            sensor,
                            "calibration",
                            number,
                            float(new_value.value),
                        )
                    except (LookupError, OSError) as err:
                        ui.notify(
                            f"Calibration failed: {err}",
                            type="negative",
                        )
                        return

                # The write's out-arguments are Status, Quality, Value -
                # no process value - so they are reshaped into what
                # show() renders rather than being formatted separately.
                status, quality, value = _unpack(result)[:3]
                show((status, quality, value, None))
                if status == CALIBRATION_OK_TEXT:
                    ui.notify(f"{point.upper()} calibrated", type="positive")
                else:
                    # The sensor refused it - an unstable reading, or a
                    # value that matches no calibration standard. Its own
                    # status code is more use than anything worded here.
                    ui.notify(
                        f"{point.upper()} not accepted: {status}",
                        type="negative",
                    )

            apply_button = disable_when_read_only(
                ui.button("Apply", on_click=apply).props(
                    "size=sm color=primary",
                ),
            )
            read_button = disable_when_read_only(
                ui.button("Read", on_click=read).props("outline size=sm"),
            )


def _unpack(result: object) -> tuple:
    """Normalise an OPC method result to a plain tuple."""
    if result is None:
        return ("", 0.0, 0.0, 0.0)
    if isinstance(result, (list, tuple)):
        values = list(result)
    else:
        values = [result]
    while len(values) < 4:
        values.append(0.0)
    return tuple(values)


@ui.page("/reactor/{reactor}/calibration/pumps")
async def pump_calibration_page(reactor: str) -> None:
    """Drive a full CalibrationRun for one pump at a time."""
    header(reactor)
    with ui.column().classes("w-full").style("padding: 1rem; gap: 1rem"):
        reactor_tabs(reactor, "Pump calibration")

        if not STATE.connected:
            not_connected_notice()
            return

        ui.label("Pump calibration").classes("text-xl font-semibold")
        ui.label(
            "Run the pump at a duty, weigh what it delivered, record it. "
            "Four distinct duties are required to fit and qualify uncertainty.",
        ).classes("text-sm text-gray-500")

        pumps = [
            name
            for name in sorted(STATE.book.actuators(reactor))
            if not name.endswith(NOT_A_PUMP)
            and STATE.book.has_method(reactor, name, "get_calibration")
        ]
        if not pumps:
            ui.label("No calibratable pumps on this reactor").classes(
                "text-gray-500",
            )
            return

        selected = ui.select(pumps, value=pumps[0], label="Pump").style(
            "min-width: 12rem",
        )
        panel = ui.column().classes("w-full").style("gap: 0.75rem")

        async def show() -> None:
            """Redraw the panel for the selected pump."""
            panel.clear()
            with panel:
                await _pump_panel(reactor, selected.value, show)

        selected.on_value_change(lambda _: show())
        # Awaited here rather than deferred onto a timer: a once-timer
        # created while the page builds can fire after the client is
        # gone - an operator navigating straight back out - and then
        # raises "the client this element belongs to has been deleted"
        # against a page nobody is looking at. Awaiting also means the
        # first paint already carries the pump's real state.
        await show()


async def _pump_panel(reactor: str, pump: str, reload) -> None:
    """The whole calibration workflow for one pump."""
    try:
        payload = json.loads(
            await STATE.call(reactor, pump, "get_calibration"),
        )
    except (LookupError, OSError, TypeError, ValueError) as err:
        ui.label(f"Could not read the calibration: {err}").classes(
            "text-red-600",
        )
        return

    view = view_from_payload(payload)

    if not view.has_slot:
        ui.label(
            f"{pump} has no calibration slot on its channel.",
        ).classes("text-gray-500")
        return

    _installed_line(view)
    _collected_points(view)
    await _run_controls(reactor, pump, view, reload)


def _installed_line(view) -> None:
    """The fitted model currently installed, plot, and duty limits."""
    cal = view.calibration
    with ui.card().classes("w-full"):
        if view.fitted:
            equation = cal.get(
                "numeric_equation",
                f"flow = {cal['a']:.6g} * duty + {cal['b']:.6g}",
            )
            ui.label(
                f"{cal.get('model', 'linear')}: {equation}",
            ).classes("font-mono text-sm")
            ui.label(
                f"r2={cal['r2']:.4f}, AIC={cal.get('aic')}, "
                f"residual={cal.get('residual')}",
            ).classes("font-mono text-xs text-gray-500")
            ui.label(f"fitted at {cal['fitted_at']}").classes(
                "text-xs text-gray-500",
            )
        else:
            ui.label("No fitted calibration installed").classes(
                "text-orange-700 text-sm",
            )
            ui.label(
                "This pump can only be driven in raw duty counts until "
                "it is calibrated.",
            ).classes("text-xs text-gray-500")

        ui.label(
            f"min duty {cal['min_duty']:.0f}, dispense duty "
            f"{cal['dispense_duty']:.0f}, max duty {cal['max_duty']:.0f}",
        ).classes("text-xs text-gray-500 font-mono")
        if cal.get("zero_flow_duty") is not None:
            ui.label(
                f"installed zero-flow evidence: {cal['zero_flow_duty']:.0f} counts",
            ).classes("text-xs text-gray-500 font-mono")

        if cal.get("installable_reason"):
            # The single authority on whether this may be installed,
            # already worded for an operator.
            ui.label(cal["installable_reason"]).classes(
                "text-sm text-red-700",
            )

        if view.fitted:
            figure, has_band = calibration_chart(cal)
            ui.plotly(figure).style("width: 100%; min-width: 0")
            if not has_band:
                ui.label(
                    "Uncertainty is unavailable for this legacy linear fit. "
                    "Refit with at least four distinct duties to qualify it.",
                ).classes("text-sm text-orange-700")


def _collected_points(view) -> None:
    """The points collected so far in this run."""
    with ui.card().classes("w-full"):
        ui.label("Collected points").classes("text-sm font-semibold")
        if not view.points:
            ui.label("None yet").classes("text-sm text-gray-500")
            return
        with ui.element("div").style("overflow-x: auto; width: 100%"):
            ui.table(
                columns=[
                    {"name": "duty", "label": "Duty", "field": "duty"},
                    {
                        "name": "flow",
                        "label": "Flow (mL/min)",
                        "field": "flow",
                    },
                ],
                rows=[
                    {"duty": f"{duty:.0f}", "flow": f"{flow:.4f}"}
                    for duty, flow in view.points
                ],
            ).props("dense flat").classes("w-full")


async def _run_controls(reactor: str, pump: str, view, reload) -> None:
    """Run a point, record a volume, fit, and the housekeeping calls."""

    async def call(method: str, *args: object) -> bool:
        """Call one CalibrationRun method and show what it said."""
        _logger.info("Operator called %s on %s %s", method, pump, args)
        try:
            status = await STATE.call(reactor, pump, method, *args)
        except (LookupError, OSError) as err:
            ui.notify(f"{method} failed: {err}", type="negative")
            return False
        # These strings are written for an operator by core.calibration
        # and core.data; showing them verbatim is the point.
        ui.notify(str(status))
        return True

    async def slow_call(
        method: str,
        *args: object,
        timeout: float,
    ) -> bool:
        """Call a CalibrationRun method that runs the pump."""
        _logger.info("Operator called %s on %s %s", method, pump, args)
        try:
            status = await STATE.call_slow(
                reactor,
                pump,
                method,
                *args,
                timeout=timeout,
            )
        except (LookupError, OSError) as err:
            ui.notify(f"{method} failed: {err}", type="negative")
            return False
        ui.notify(str(status))
        return True

    with ui.card().classes("w-full"):
        ui.label("Run a point").classes("text-sm font-semibold")
        with ui.row().classes("items-end flex-wrap").style("gap: 0.5rem"):
            duty = ui.number("Duty (counts)", value=1000.0, format="%.0f")
            seconds = ui.number("Run for (s)", value=60.0, format="%.0f")

            async def run_point() -> None:
                problem = duty_error(duty.value) or seconds_error(
                    seconds.value,
                )
                if problem:
                    ui.notify(problem, type="warning")
                    return
                if not await confirm(
                    f"Run {pump}?",
                    f"The pump will run at {duty.value:.0f} counts for "
                    f"{seconds.value:.0f} seconds.",
                    danger=True,
                ):
                    return
                ui.notify(
                    f"Running {pump} at {duty.value:.0f} for "
                    f"{seconds.value:.0f}s...",
                )
                # The one call that outlives asyncua's reconnect
                # watchdog, so it goes on its own session - see
                # OpcClient.call_slow_method.
                with in_flight(run_button):
                    changed = await slow_call(
                        "calibrate_point",
                        float(duty.value),
                        float(seconds.value),
                        timeout=float(seconds.value) + CALL_MARGIN_SECONDS,
                    )
                if changed:
                    await reload()

            run_button = ui.button("Run", on_click=run_point).props(
                "color=primary",
            )
            if not view.can_run_point:
                run_button.disable()
                run_button.tooltip(
                    "A run is in flight, or a measurement is owed",
                )
            else:
                disable_when_read_only(run_button)

    with ui.card().classes("w-full"):
        ui.label("Record the measured volume").classes(
            "text-sm font-semibold",
        )
        if view.state.value == "awaiting":
            ui.label(
                f"{view.pending_duty:.0f} counts ran for "
                f"{view.pending_seconds:.3f}s - how much came out?",
            ).classes("text-sm text-orange-700")
        with ui.row().classes("items-end flex-wrap").style("gap: 0.5rem"):
            volume = ui.number("Volume (mL)", value=None, format="%.3f")

            async def record() -> None:
                problem = volume_error(volume.value)
                if problem:
                    ui.notify(problem, type="warning")
                    return
                with in_flight(record_button):
                    changed = await call("record_point", float(volume.value))
                if changed:
                    await reload()

            record_button = ui.button("Record", on_click=record)
            if not view.can_record:
                record_button.disable()
                record_button.tooltip("Run a point first")
            else:
                disable_when_read_only(record_button)

            async def discard() -> None:
                with in_flight(discard_button):
                    changed = await call("discard_pending_point")
                if changed:
                    await reload()

            discard_button = ui.button("Discard pending", on_click=discard).props(
                "outline",
            )
            if not view.can_discard:
                discard_button.disable()
            else:
                disable_when_read_only(discard_button)

    ui.label(
        "Zero-flow duty is optional stall evidence. It sets the installed "
        "floor but is excluded from coefficients, residuals, AIC and the chart.",
    ).classes("text-xs text-gray-500")
    with ui.row().classes("items-end flex-wrap").style("gap: 0.5rem"):
        zero_flow = ui.number(
            "Zero-flow duty",
            value=view.zero_flow_duty,
            format="%.0f",
        )

        async def fit() -> None:
            problem = zero_flow_duty_error(zero_flow.value)
            if problem:
                ui.notify(problem, type="warning")
                return
            method = (
                "fit_calibration_with_zero_flow"
                if zero_flow.value is not None
                else "fit_calibration"
            )
            args = (
                (float(zero_flow.value),)
                if zero_flow.value is not None
                else ()
            )
            with in_flight(fit_button):
                changed = await call(method, *args)
            if changed:
                await reload()

        fit_button = ui.button("Fit calibration", on_click=fit).props(
            "color=primary",
        )
        if not view.can_fit:
            fit_button.disable()
            fit_button.tooltip(
                "Four points at different duties are needed to fit",
            )
        else:
            disable_when_read_only(fit_button)

        _calibration_method_button(
            "Clear points",
            "clear_points",
            call,
            reload,
            view.can_edit,
            danger=True,
        )
        _calibration_method_button(
            "Reload from disk",
            "reload_calibration",
            call,
            reload,
            view.can_edit,
        )

    _set_duties_card(view, call, reload)


def _calibration_method_button(
    label: str,
    method: str,
    call,
    reload,
    enabled: bool,
    danger: bool = False,
) -> None:
    """Build one in-flight-locked calibration housekeeping button."""

    async def invoke() -> None:
        if danger and not await confirm(
            "Clear calibration points?",
            "All collected run points and run-level zero-flow evidence will be discarded.",
            danger=True,
        ):
            return
        with in_flight(button):
            changed = await call(method)
        if changed:
            await reload()

    button = ui.button(label, on_click=invoke).props("outline")
    if enabled:
        disable_when_read_only(button)
    else:
        button.disable()


def _set_duties_card(view, call, reload) -> None:
    """Adjust the stall floor and non-PID dose duty without a refit."""
    cal = view.calibration
    with ui.card().classes("w-full"):
        ui.label("Duty limits").classes("text-sm font-semibold")
        ui.label(
            "Changes the stall floor and the duty used for volume "
            "doses. PID volume doses select duty dynamically. Does not "
            "refit the model.",
        ).classes("text-xs text-gray-500")
        with ui.row().classes("items-end flex-wrap").style("gap: 0.5rem"):
            min_duty = ui.number(
                "Min duty",
                value=cal["min_duty"],
                format="%.0f",
            )
            dispense = ui.number(
                "Dispense duty",
                value=cal["dispense_duty"],
                format="%.0f",
            )

            async def apply() -> None:
                if min_duty.value is None or dispense.value is None:
                    ui.notify("Both duties are needed", type="warning")
                    return
                with in_flight(button):
                    changed = await call(
                        "set_duties",
                        float(min_duty.value),
                        float(dispense.value),
                    )
                if changed:
                    await reload()

            button = ui.button("Apply", on_click=apply).props("outline")
            if not view.can_edit:
                button.disable()
            else:
                disable_when_read_only(button)
