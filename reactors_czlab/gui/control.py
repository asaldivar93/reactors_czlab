"""Pure helpers for the atomic control-configuration method."""

from __future__ import annotations

from reactors_czlab.core.data import ControlMethod

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

#: Config arguments following Method and Output_unit in the server method.
#: The form starts with all of them from ``get_control_config`` and overlays
#: the fields currently visible to the operator.
CONFIG_FIELDS = (
    "value",
    "time_on",
    "time_off",
    "lb",
    "ub",
    "setpoint",
    "kp",
    "ki",
    "kd",
    "min_integral",
    "max_integral",
    "auto_integral_band",
    "backwards",
)


def fields_for(method: str) -> tuple[str, ...]:
    """Return the config fields consumed by ``method``.

    Raises
    ------
    KeyError
        If the server does not implement ``method``.

    """
    return METHOD_FIELDS[method]


def build_config_args(
    method: int,
    output_unit: int,
    values: dict[str, object],
) -> tuple[object, ...]:
    """Build one complete ``apply_control_config`` argument tuple.

    Parameters
    ----------
    method, output_unit:
        Indices derived from the server-provided option lists.
    values:
        Every configuration field from the read-back payload, overlaid with
        values edited in the form.

    Returns
    -------
    tuple
        Arguments in the order declared by the OPC method.

    Raises
    ------
    ValueError
        If any declared method argument is missing.

    """
    for field in CONFIG_FIELDS:
        if field not in values:
            error_message = f"control config needs a value for {field}"
            raise ValueError(error_message)
    return (
        method,
        output_unit,
        *(values[field] for field in CONFIG_FIELDS),
    )
