"""Pure helpers for the atomic control-configuration method."""

from __future__ import annotations

from reactors_czlab.core.data import (
    MAX_OUTPUT,
    ControlMethod,
    OutputUnit,
)

#: Server-side enum encodings. Stage 2 replaces these client-owned maps with
#: the options returned by ``get_control_config``.
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

#: Which fields each strategy consumes. The server receives one complete
#: argument tuple but deliberately populates only the selected method's
#: ``ControlConfig`` fields.
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

_ARGUMENT_DEFAULTS: dict[str, object] = {
    "value": 0.0,
    "time_on": 0.0,
    "time_off": 0.0,
    "lb": 0.0,
    "ub": 0.0,
    "setpoint": 0.0,
    "kp": 100.0,
    "ki": 0.01,
    "kd": 0.0,
    "min_integral": 0.0,
    "max_integral": MAX_OUTPUT,
    "auto_integral_band": True,
    "backwards": False,
}


def fields_for(method: str) -> tuple[str, ...]:
    """Return the config fields consumed by ``method``.

    Raises
    ------
    KeyError
        If the server does not implement ``method``.

    """
    return METHOD_FIELDS[method]


def build_config_args(
    method: str,
    output_unit: str,
    values: dict[str, object],
) -> tuple[object, ...]:
    """Build one complete ``apply_control_config`` argument tuple.

    Parameters
    ----------
    method, output_unit:
        Names selected in the form.
    values:
        Values keyed by configuration field. Fields unused by the selected
        method are ignored and receive inert defaults in the method call.

    Returns
    -------
    tuple
        Arguments in the order declared by the OPC method.

    Raises
    ------
    KeyError
        If the method or output unit is unknown.
    ValueError
        If a field used by the method is missing.

    """
    method_code = METHOD_CODES[method]
    unit_code = OUTPUT_UNIT_CODES[output_unit]
    for field in fields_for(method):
        if field not in values:
            error_message = f"{method} needs a value for {field}, none was given"
            raise ValueError(error_message)

    arguments = {**_ARGUMENT_DEFAULTS, **values}
    return (
        method_code,
        unit_code,
        arguments["value"],
        arguments["time_on"],
        arguments["time_off"],
        arguments["lb"],
        arguments["ub"],
        arguments["setpoint"],
        arguments["kp"],
        arguments["ki"],
        arguments["kd"],
        arguments["min_integral"],
        arguments["max_integral"],
        arguments["auto_integral_band"],
        arguments["backwards"],
    )
