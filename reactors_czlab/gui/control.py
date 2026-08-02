"""Building the write plan for a control-configuration change.

The order these variables are written in is a correctness requirement,
not a preference, which is why it lives in a pure module with a test on
it rather than inside a form's submit handler.

``ActuatorOpc.datachange_notification`` rebuilds a whole ``ControlConfig``
on *every* variable notification, reading only the parameters the
method currently selected needs. So writing ``method`` first applies the
new controller against whatever setpoint, bounds and gains are still
sitting in the server's variables from the previous configuration - a
manual to pid switch could drive a pump hard for one notification.

Writing it last means every intermediate notification rebuilds the *old*
method's config, which is a no-op, and only the final write commits the
new one.
"""

from __future__ import annotations

from dataclasses import dataclass

from reactors_czlab.core.data import ControlMethod, OutputUnit

#: Server-side enum encodings. These mirror the maps in
#: ``opcua/actuator.py``; the OPC variables are UInt32 indices, not
#: strings.
METHOD_CODES: dict[str, int] = {
    ControlMethod.manual: 0,
    ControlMethod.timer: 1,
    ControlMethod.on_boundaries: 2,
    ControlMethod.pid: 3,
}

OUTPUT_UNIT_CODES: dict[str, int] = {
    OutputUnit.duty: 0,
    OutputUnit.flow: 1,
    OutputUnit.volume: 2,
}

#: Which config channels each method actually reads, mirroring the
#: `match method:` in ``ActuatorOpc.datachange_notification``. Writing
#: the others would be harmless but pointless traffic, and showing them
#: would suggest they do something.
METHOD_FIELDS: dict[str, tuple[str, ...]] = {
    ControlMethod.manual: ("value",),
    ControlMethod.timer: ("time_on", "time_off", "value"),
    ControlMethod.on_boundaries: ("lb", "ub", "value", "backwards"),
    ControlMethod.pid: (
        "setpoint",
        "kp",
        "ki",
        "kd",
        "backwards",
        "auto_integral_band",
        "min_integral",
        "max_integral",
    ),
}

#: Written second to last, ahead of `method`. It changes how a demand is
#: delivered rather than how it is decided, so it must already be in
#: place when the new controller is built.
UNIT_CHANNEL = "output_unit"
METHOD_CHANNEL = "method"


@dataclass(frozen=True)
class Write:
    """One variable write in a plan."""

    channel: str
    value: object


def fields_for(method: str) -> tuple[str, ...]:
    """The config channels a method reads.

    Raises
    ------
    KeyError
        If the method is not one the server implements.

    """
    return METHOD_FIELDS[method]


def build_write_plan(
    method: str,
    output_unit: str,
    values: dict[str, object],
) -> list[Write]:
    """Order the writes for a control-configuration change.

    Parameters
    ----------
    method:
        The ``ControlMethod`` being selected.
    output_unit:
        The ``OutputUnit`` being selected.
    values:
        Field values from the form, keyed by channel name. Fields the
        selected method does not read are ignored.

    Returns
    -------
    list[Write]
        Parameters first, then ``output_unit``, then ``method``. The
        order is the point; see the module docstring.

    Raises
    ------
    KeyError
        If the method or output unit is not one the server implements.
    ValueError
        If a field the method needs was not supplied. A missing field
        would otherwise be written as whatever the form defaulted to,
        silently retuning a controller an operator did not mean to
        touch.

    """
    method_code = METHOD_CODES[method]
    unit_code = OUTPUT_UNIT_CODES[output_unit]

    plan: list[Write] = []
    for channel in fields_for(method):
        if channel not in values:
            error_message = (
                f"{method} needs a value for {channel}, none was given"
            )
            raise ValueError(error_message)
        plan.append(Write(channel, values[channel]))

    plan.append(Write(UNIT_CHANNEL, unit_code))
    plan.append(Write(METHOD_CHANNEL, method_code))
    return plan


def unit_rejection_reason(
    output_unit: str,
    calibration: dict | None,
) -> str | None:
    """Why a flow or volume configuration would be refused, if it would.

    ``core.dispenser.check_unit`` makes this same judgement server-side
    and only *logs* a refusal - ``set_control_config`` keeps the running
    controller and the client is told nothing. So the form has to ask
    the question before writing, or an operator sees a configuration
    that appears to have been accepted and has not been.

    Parameters
    ----------
    output_unit:
        The unit being selected.
    calibration:
        The ``calibration`` object from ``get_calibration()``, or None
        for an actuator with no calibration slot at all.

    Returns
    -------
    str | None
        An operator-readable reason, or None if the unit is usable.

    """
    if output_unit == OutputUnit.duty:
        # Raw counts need no calibration; this is the default and the
        # only unit an uncalibrated pump can be driven in.
        return None

    if calibration is None:
        return (
            f"{output_unit} needs a pump calibration, and this actuator "
            "has no calibration slot"
        )

    if not calibration.get("is_fitted"):
        return (
            f"{output_unit} needs a fitted calibration - run a pump "
            "calibration first"
        )

    # installable_reason() is the single authority on whether a
    # calibration may be used, and its text is already written for an
    # operator, so it is passed through rather than reworded.
    return calibration.get("installable_reason")
