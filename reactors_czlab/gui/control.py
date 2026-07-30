"""Turn a filled-in configuration form into an ordered set of writes.

``ActuatorOpc.datachange_notification`` rebuilds an entire
``ControlConfig`` on *every* variable change and hands it to
``Actuator.set_control_config``. So the GUI cannot write a configuration
atomically, and the order it writes in decides what the actuator does in
between. Writing ``method`` first would apply the new controller against
whatever stale setpoint, bounds and gains are still sitting in the
server's variables.

The rule: parameters first, ``output_unit`` next, ``method`` last. Every
intermediate notification then keeps the *old* method, and only the last
write commits the new one.

Pure: no nicegui, no network.
"""

from __future__ import annotations

from dataclasses import dataclass

from reactors_czlab.core.data import ControlMethod, OutputUnit
from reactors_czlab.opcua.actuator import control_method, output_unit_map

#: Which configuration variables each method actually consumes. Mirrors
#: the ``match`` in ``ActuatorOpc.datachange_notification``: writing a
#: variable the selected method ignores is harmless but pointless, and
#: writing a stale one the method *does* consume is not.
METHOD_FIELDS: dict[ControlMethod, tuple[str, ...]] = {
    ControlMethod.manual: (),
    ControlMethod.timer: ("time_on", "time_off"),
    ControlMethod.on_boundaries: ("lb", "ub", "backwards"),
    ControlMethod.pid: (
        "setpoint",
        "kp",
        "ki",
        "kd",
        "backwards",
        "min_integral",
        "max_integral",
        "auto_integral_band",
    ),
}


@dataclass(frozen=True)
class Write:
    """One variable write: the channel segment and the value."""

    channel: str
    value: object


def method_index(method: ControlMethod) -> int:
    """The integer the server's ``method`` variable expects."""
    return next(k for k, v in control_method.items() if v is method)


def unit_index(unit: OutputUnit) -> int:
    """The integer the server's ``output_unit`` variable expects."""
    return next(k for k, v in output_unit_map.items() if v is unit)


def build_write_plan(
    method: ControlMethod,
    unit: OutputUnit,
    fields: dict[str, object],
) -> list[Write]:
    """Order the writes that install a configuration.

    Parameters
    ----------
    method:
        The control method to install.
    unit:
        The output unit the demand is expressed in.
    fields:
        Form values, keyed by the channel segment of the browse name
        (``setpoint``, ``kp``, ``value``, ...). Keys the selected method
        does not consume are ignored.

    Returns
    -------
    list of Write
        Parameters first, then ``output_unit``, then ``method``. Callers
        must write them in this order and must not parallelise them.

    """
    plan = [
        Write(field, fields[field])
        for field in METHOD_FIELDS[method]
        if field in fields
    ]
    if "value" in fields:
        plan.append(Write("value", fields["value"]))
    plan.append(Write("output_unit", unit_index(unit)))
    plan.append(Write("method", method_index(method)))
    return plan


def unit_rejection_reason(
    unit: OutputUnit,
    calibration: dict | None,
) -> str | None:
    """Why this output unit cannot be used on this pump, if it cannot.

    ``core.dispenser.check_unit()`` asks the same question server-side
    and rejects the whole configuration when the answer is no - but it
    only logs the reason, and ``set_control_config`` returns nothing, so
    the operator would see a write succeed and nothing happen. Asking it
    here first is the only way the reason reaches a screen.

    Parameters
    ----------
    unit:
        The unit the form is about to install.
    calibration:
        The payload from the actuator's ``get_calibration()`` method, or
        ``None`` if it could not be read.

    Returns
    -------
    str or None
        ``None`` when the unit is usable, otherwise a reason safe to
        show verbatim.

    """
    if unit is OutputUnit.duty:
        return None
    if calibration is None:
        return (
            f"{unit} needs a calibration, and this actuator's could not "
            "be read"
        )
    if not calibration.get("is_fitted"):
        return (
            f"{unit} needs a fitted calibration; this pump has never "
            "been calibrated, so mL cannot be converted to a duty. Run "
            "a calibration first, or use duty."
        )
    return None
