from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from asyncua import ua

if TYPE_CHECKING:
    from opcua.common.node import Node
    from reactors_czlab.core.actuator import Actuator

control_methods = {0: "manual", 1: "timer", 2: "on_boundaries", 3: "pid"}


class ActuatorOpc:
    """Actuator node."""

    def __init__(self, actuator: Actuator) -> None:
        """Initialize the OPC actuator node."""
        self.actuator = actuator

    async def add_node(self, parent: Node, idx: str) -> None:
        """Add node and variables for the actuator."""
        actuator = self.actuator
        self.node = await parent.add_object(idx, actuator.id)
        self.value = await self.node.add_variable(
            idx, "value", 0, ua.VariantType.Int32
        )

        self.control_method = await self.node.add_variable(
            idx, "control_method", 0, varianttype=ua.VariantType.Int32
        )
        # self.control_method.write_attribute(
        #     ua.AttributeIds.Description, ua.LocalizedText("Control method")
        # )
        # self.control_method.write_attribute(
        #     ua.AttributeIds.ValueAsText,
        #     ua.LocalizedText(self.control_method_states[0]),  # Default state
        # )
