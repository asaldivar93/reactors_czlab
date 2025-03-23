"""OPC-UA Actuator node."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from asyncua import ua

if TYPE_CHECKING:
    from asyncua import Server
    from asyncua.common.node import Node

    from reactors_czlab.core.actuator import Actuator

_logger = logging.getLogger("server.opcactuator")

control_method = {0: "manual", 1: "timer", 2: "on_boundaries", 3: "pid"}


class ActuatorOpc:
    """Actuator node."""

    def __init__(self, actuator: Actuator) -> None:
        """Initialize the OPC actuator node."""
        self.actuator = actuator

    async def init_node(self, server: Server, parent: Node, idx: int) -> None:
        """Add node and variables for the actuator."""
        actuator = self.actuator

        # Add actuator node to reactor
        self.node = await parent.add_object(idx, actuator.id)
        _logger.info(f"New Actuator node {self.actuator.id}:{self.node}")

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
        self, node: Node, val: float, data: object
    ) -> None:
        """Read the control configuration, and update the actuator accordingly."""
        _logger.debug(f"Config update: {self.actuator.id}:{node}:{val}")
        index = await self.method.get_value()
        try:
            method = control_method[index]
            control_config = {"method": method}
            # Build a dictionary with the appropiate
            # parameters based on the method variable
            match method:
                case "manual":
                    control_config["value"] = await self.value.get_value()
                    self.actuator.set_control_config(control_config)
                    _logger.debug(f"Control config: {control_config}")

                case "timer":
                    control_config["value"] = await self.value.get_value()
                    control_config["time_on"] = await self.time_on.get_value()
                    control_config["time_off"] = await self.time_off.get_value()
                    self.actuator.set_control_config(control_config)
                    _logger.debug(f"Control config: {control_config}")

                case "on_boundaries":
                    await self.update_reference_sensor()
                    control_config["value"] = await self.value.get_value()
                    control_config["lower_bound"] = await self.lb.get_value()
                    control_config["upper_bound"] = await self.ub.get_value()
                    # There is a bug here, it will not update the actuator when
                    # there is a change in the value. It will update with
                    # changes in everything else
                    self.actuator.set_control_config(control_config)
                    _logger.debug(f"Control config: {control_config}")

                case "pid":
                    await self.update_reference_sensor()
                    control_config["setpoint"] = await self.setpoint.get_value()
                    self.actuator.set_control_config(control_config)
                    _logger.debug(f"Control config: {control_config}")
        except KeyError:
            _logger.exception(f"{index} not a member of {control_method}")

        # We need to find a way to register a custum callback
        # to update the PID gains without resetting the controller

    async def update_reference_sensor(self) -> None:
        """Find the reference sensor and pass it to the actuator."""
        sensor_idx = await self.curr_sensor.get_value()
        try:
            reference_sensor = self.sensors_dict[sensor_idx]
            self.actuator.set_reference_sensor(reference_sensor)
            _logger.debug(f"Available sensors: {self.sensors_dict}")
            _logger.debug(f"Selected sensor: {sensor_idx}: {reference_sensor}")
        except KeyError:
            _logger.exception(
                f"{sensor_idx} not a member of {self.sensors_dict}"
            )

    async def update_value(self) -> None:
        """Update the actuator state in the server."""
        new_val = self.actuator.channel.value
        await self.curr_value.write_value(float(new_val))

    async def init_control_node(self, idx: int) -> None:
        """Add configuration variables for the control method."""
        # This is a mess, might need to think of a better way,
        # maybe use an OPC structure instead or a Builder Class?
        # Add Node to store the control settings
        self.control_method = await self.node.add_object(idx, "ControlMethod")

        # Add variable to set the desired status
        self.value = await self.control_method.add_variable(idx, "value", 0.0)
        await self.value.set_writable()

        # Add variable to record the current status
        self.curr_value = await self.node.add_variable(idx, "curr_value", 0.0)
        await self.curr_value.set_writable()

        # ControlMethod
        self.method = await self.control_method.add_variable(
            idx,
            "method",
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

        # TimerControl
        self.time_on = await self.control_method.add_variable(
            idx,
            "time_on",
            0.0,
        )
        await self.time_on.set_writable()
        self.time_off = await self.control_method.add_variable(
            idx,
            "time_off",
            0.0,
        )
        await self.time_off.set_writable()

        # OnBoundariesControl
        self.lb = await self.control_method.add_variable(
            idx,
            "lb",
            0.0,
        )
        await self.lb.set_writable()
        self.ub = await self.control_method.add_variable(
            idx,
            "ub",
            0.0,
        )
        await self.ub.set_writable()

        # PidControl
        self.setpoint = await self.control_method.add_variable(
            idx,
            "setpoint",
            0.0,
        )
        await self.setpoint.set_writable()

        # Sensor used as control variable
        self.curr_sensor = await self.control_method.add_variable(
            idx,
            "reference_sensor",
            0,
            varianttype=ua.VariantType.UInt32,
        )
        await self.curr_sensor.set_writable()

        # The default sensor is none, user needs to set it
        sensors_list = ["none"]
        # Get all avaliable sensors
        for sensor in self.actuator.sensors.values():
            sensors_list.append(sensor.id)
        # Build ua.VariantType list
        sensors_variant = ua.Variant(
            [ua.LocalizedText(sensor) for sensor in sensors_list],
            ua.VariantType.LocalizedText,
        )

        # Build a dict to recover sensor name
        # for datachange_notification callback
        self.sensors_dict = dict(enumerate(sensors_list))
        # Add sensor list to the opc server
        await self.curr_sensor.add_property(
            ua.ObjectIds.MultiStateDiscreteType_EnumStrings,
            "EnumStrings",
            sensors_variant,
        )
