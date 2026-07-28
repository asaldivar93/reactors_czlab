"""OPC-UA Actuator node."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from asyncua import ua

from reactors_czlab.core.data import ControlConfig, ControlMethod, OutputUnit

if TYPE_CHECKING:
    from asyncua import Server
    from asyncua.common.node import Node

    from reactors_czlab.core.actuator import Actuator

_logger = logging.getLogger("server.opcactuator")

control_method = {
    0: ControlMethod.manual,
    1: ControlMethod.timer,
    2: ControlMethod.on_boundaries,
    3: ControlMethod.pid,
}

output_unit_map = {
    0: OutputUnit.duty,
    1: OutputUnit.flow,
    2: OutputUnit.volume,
}


class ActuatorOpc:
    """Actuator node."""

    def __init__(self, actuator: Actuator) -> None:
        """Initialize the OPC actuator node."""
        self.actuator = actuator
        self.id = actuator.id

    def __repr__(self) -> str:
        """Print actuator id."""
        return f"ActuatorOpc(id: {self.actuator.id})"

    async def update_value(self) -> None:
        """Publish the actuator output and pump data if they changed."""
        published = await self.curr_value.get_value()
        # old_value is what write_output() last pushed to the hardware.
        current = self.actuator.channel.old_value
        if current != published:
            await self.curr_value.write_value(float(current))
            _logger.debug("Updated %s with value %s", self.id, current)

        await self.total_volume.write_value(
            float(self.actuator.dispenser.total_volume),
        )
        cal = self.actuator.channel.calibration
        if cal is not None:
            await self.cal_a.write_value(float(cal.a))
            await self.cal_b.write_value(float(cal.b))
            await self.cal_r2.write_value(float(cal.r2))

    async def init_node(
        self,
        server: Server,
        parent: Node,
        idx: int,
    ) -> None:
        """Add node and variables for the actuator."""
        actuator = self.actuator

        # Add actuator node to reactor
        self.node = await parent.add_object(idx, actuator.id)
        bnp = await parent.read_browse_name()
        bns = await self.node.read_browse_name()
        _logger.info("In node %s added %s", bnp.Name, bns.Name)

        # Add a node with variables holding the control config
        await self.init_control_node(idx)
        # Start a subscription to the variables in the control
        await self.init_control_subscription(server)

    async def init_control_subscription(self, server: Server) -> None:
        """Create a subscription to the control parameters."""
        sub = await server.create_subscription(500, self)
        on_config = await self.control_method.get_variables()
        await sub.subscribe_data_change(on_config)

    async def datachange_notification(
        self,
        node: Node,
        val: float,
        data: object,
    ) -> None:
        """Read the control configuration, and update the actuator."""
        _logger.debug("Config update: %s:%s:%s", self.actuator.id, node, val)
        index = await self.method.get_value()
        try:
            method = control_method[index]
        except KeyError:
            _logger.exception(
                "%s is not a member of %s",
                index,
                sorted(control_method),
            )
            return

        unit_index = await self.output_unit.get_value()
        try:
            unit = output_unit_map[unit_index]
        except KeyError:
            _logger.exception(
                "%s is not a member of %s",
                unit_index,
                sorted(output_unit_map),
            )
            return

        config = ControlConfig(
            method,
            value=await self.value.get_value(),
            output_unit=unit,
        )

        # Only read the variables the selected method actually needs.
        match method:
            case ControlMethod.manual:
                pass

            case ControlMethod.timer:
                config.time_on = await self.time_on.get_value()
                config.time_off = await self.time_off.get_value()

            case ControlMethod.on_boundaries:
                config.lb = await self.lb.get_value()
                config.ub = await self.ub.get_value()

            case ControlMethod.pid:
                config.setpoint = await self.setpoint.get_value()

        self.actuator.set_control_config(config)
        _logger.debug("Control config: %s", config)

    async def init_control_node(self, idx: int) -> None:
        """Add configuration variables for the control method.

        Every controller's parameters live side by side under one node; the
        client writes ``method`` plus the parameters that method uses.
        """
        # Add Node to store the control settings
        self.control_method = await self.node.add_object(
            idx,
            f"{self.id}:ControlMethod",
        )

        # Add variable to set the desired status
        self.value = await self.control_method.add_variable(
            idx,
            f"{self.id}:value",
            0.0,
        )
        await self.value.set_writable()

        # Add variable to record the current status
        self.curr_value = await self.node.add_variable(
            idx,
            f"{self.id}:curr_value",
            0.0,
        )
        await self.curr_value.set_writable()

        # Published pump data. The browse names follow the
        # <reactor>:<name>:<channel> contract, so they reach the data table.
        self.total_volume = await self.node.add_variable(
            idx,
            f"{self.id}:total_volume",
            0.0,
        )
        self.cal_a = await self.node.add_variable(idx, f"{self.id}:cal_a", 0.0)
        self.cal_b = await self.node.add_variable(idx, f"{self.id}:cal_b", 0.0)
        self.cal_r2 = await self.node.add_variable(
            idx,
            f"{self.id}:cal_r2",
            0.0,
        )

        # ControlMethod
        self.method = await self.control_method.add_variable(
            idx,
            f"{self.id}:method",
            0,
            varianttype=ua.VariantType.UInt32,
        )
        await self.method.set_writable()
        enum_strings_variant = ua.Variant(
            [ua.LocalizedText(control_method[k]) for k in control_method],
            ua.VariantType.LocalizedText,
        )
        await self.method.add_property(
            ua.ObjectIds.MultiStateDiscreteType_EnumStrings,
            "EnumStrings",
            enum_strings_variant,
        )

        # Unit the demand is expressed in: raw counts, mL/min, or mL.
        self.output_unit = await self.control_method.add_variable(
            idx,
            f"{self.id}:output_unit",
            0,
            varianttype=ua.VariantType.UInt32,
        )
        await self.output_unit.set_writable()
        unit_strings_variant = ua.Variant(
            [ua.LocalizedText(output_unit_map[k]) for k in output_unit_map],
            ua.VariantType.LocalizedText,
        )
        await self.output_unit.add_property(
            ua.ObjectIds.MultiStateDiscreteType_EnumStrings,
            "EnumStrings",
            unit_strings_variant,
        )

        # TimerControl
        self.time_on = await self.control_method.add_variable(
            idx,
            f"{self.id}:time_on",
            0.0,
        )
        await self.time_on.set_writable()
        self.time_off = await self.control_method.add_variable(
            idx,
            f"{self.id}:time_off",
            0.0,
        )
        await self.time_off.set_writable()

        # OnBoundariesControl
        self.lb = await self.control_method.add_variable(
            idx,
            f"{self.id}:lb",
            0.0,
        )
        await self.lb.set_writable()
        self.ub = await self.control_method.add_variable(
            idx,
            f"{self.id}:ub",
            0.0,
        )
        await self.ub.set_writable()

        # PidControl
        self.setpoint = await self.control_method.add_variable(
            idx,
            f"{self.id}:setpoint",
            0.0,
        )
        await self.setpoint.set_writable()

        # Sensor used as control variable
        self.curr_sensor = await self.control_method.add_variable(
            idx,
            f"{self.id}:reference_sensor",
            0,
            varianttype=ua.VariantType.UInt32,
        )
        await self.curr_sensor.set_writable()
