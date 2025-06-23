"""OPC-UA Actuator node."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from asyncua import ua

from reactors_czlab.core.data import ControlConfig, ControlMethod
from reactors_czlab.core.sensor import Sensor
from reactors_czlab.core.utils import Timer
from reactors_czlab.server_info import VERBOSE

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


class ActuatorOpc:
    """Actuator node."""

    def __init__(self, actuator: Actuator, timer: Timer) -> None:
        """Initialize the OPC actuator node."""
        self.actuator = actuator
        self.id = actuator.id
        self.base_timer = timer
        self._timer = None
        self.timer = self.base_timer
        self.sensors_enum = None
        self.sensor = None

    def __repr__(self) -> str:
        """Print sensor id."""
        return f"ActuatorOpc(id: {self.actuator.id})"

    def __eq__(self, other: object) -> bool:
        """Test equality by senor id."""
        this = self.actuator.id
        return this == other

    @property
    def timer(self) -> Timer | None:
        """Timer getter."""
        return self._timer

    @timer.setter
    def timer(self, timer: Timer | None) -> None:
        """Timer setter."""
        if not isinstance(timer, Timer | None):
            raise TypeError
        if self._timer is not None:
            self._timer.remove_async_actuator(self)
        if timer is not None:
            timer.add_async_actuator(self)
        else:
            timer = self.base_timer
        self._timer = timer

    @property
    def sensors(self) -> dict[str, Sensor] | None:
        """Return a Dict of Sensors."""
        return self._sensors

    @sensors.setter
    def sensors(self, sensors: list[Sensor] | None) -> None:
        """Set available sensors."""
        if not isinstance(sensors, list | None):
            raise TypeError
        if sensors is None:
            self._sensors = None
        else:
            self._sensors = {s.id: s for s in sensors}

    @property
    def reference_sensor(self) -> Sensor | None:
        """Sensor getter."""
        return self._reference_sensor

    @reference_sensor.setter
    def reference_sensor(self, sensor: Sensor | str | None) -> None:
        """Set reference sensor."""
        if not isinstance(sensor, Sensor | None | str):
            raise TypeError
        if isinstance(sensor, str):
            sensor = self.sensors.get(sensor, None)
        if sensor is None:
            self.timer = self.base_timer
            error_message = f"None sensor in actuator {self.actuator.id}"
            _logger.warning(error_message)
            _logger.warning(f"Available sensors: {self.sensors_enum}")
        else:
            self.timer = sensor.timer
        self._reference_sensor = sensor
        _logger.info(f"Updated sensor {self._reference_sensor} in {self.id}")

    async def async_timer_callback(self) -> None:
        """Update actuator values and push to server."""
        await self.update_value()

    async def update_value(self) -> None:
        """Update the actuator state in the server."""
        new_val = self.actuator.channel.value
        await self.curr_value.write_value(float(new_val))
        if VERBOSE:
            _logger.debug(f"Updated {self.curr_value} with value {new_val}")

    async def init_node(self, server: Server, parent: Node, idx: int) -> None:
        """Add node and variables for the actuator."""
        actuator = self.actuator

        # Add actuator node to reactor
        self.node = await parent.add_object(idx, actuator.id)

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
        _logger.debug(f"Config update: {self.actuator.id}:{node}:{val}")
        index = await self.method.get_value()
        try:
            method = control_method[index]
            value = await self.value.get_value()
            config = ControlConfig(method, value=value)

            # Build a dictionary with the appropiate
            # parameters based on the method variable
            match method:
                case "manual":
                    self.actuator.set_control_config(config)
                    self.timer = self.base_timer
                    self.actuator.timer = self.base_timer
                    self.reference_sensor = None
                    _logger.debug(f"Control config: {config}")

                case "timer":
                    config.time_on = await self.time_on.get_value()
                    config.time_off = await self.time_off.get_value()
                    self.timer = self.base_timer
                    self.actuator.timer = self.base_timer
                    self.reference_sensor = None
                    self.actuator.set_control_config(config)
                    _logger.debug(f"Control config: {config}")

                case "on_boundaries":
                    config.lb = await self.lb.get_value()
                    config.ub = await self.ub.get_value()
                    # There is a bug here, it will not update the actuator when
                    # there is a change in the value. It will update with
                    # changes in everything else
                    self.actuator.set_control_config(config)
                    await self.update_reference_sensor()
                    _logger.debug(f"Control config: {config}")

                case "pid":
                    config.setpoint = await self.setpoint.get_value()
                    self.actuator.set_control_config(config)
                    await self.update_reference_sensor()
                    _logger.debug(f"Control config: {config}")
        except KeyError:
            _logger.exception(f"{index} not a member of {control_method}")

    # We need to find a way to register a custum callback
    # to update the PID gains without resetting the controller

    async def update_reference_sensor(self) -> None:
        """Find the reference sensor and pass it to the actuator."""
        sensor_idx = await self.curr_sensor.get_value()
        new_sensor = self.sensors_enum.get(sensor_idx, None)
        self.actuator.reference_sensor = new_sensor
        self.reference_sensor = new_sensor
        _logger.debug(
            f"Sensor {sensor_idx}: {new_sensor} set for {self.actuator.id}",
        )

    async def init_control_node(self, idx: int) -> None:
        """Add configuration variables for the control method."""
        # This is a mess, might need to think of a better way,
        # maybe use an OPC structure instead or a Builder Class?
        # Add Node to store the control settings
        self.control_method = await self.node.add_object(idx, "ControlMethod")

        # Add variable to set the desired output
        self.value = await self.control_method.add_variable(idx, "value", 0.0)
        await self.value.set_writable()

        # Add variable to record the current output
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

        # Get all avaliable sensors
        sensors_list = [sensor.id for sensor in self.actuator.sensors.values()]
        # The default sensor is none, user needs to set it
        sensors_list.insert(0, "none")
        # Build a dict dict[sensor index, sensor id]
        self.sensors_enum = dict(enumerate(sensors_list))

        # Add sensor list to the opc server
        sensors_variant = ua.Variant(
            [ua.LocalizedText(sensor) for sensor in sensors_list],
            ua.VariantType.LocalizedText,
        )
        await self.curr_sensor.add_property(
            ua.ObjectIds.MultiStateDiscreteType_EnumStrings,
            "EnumStrings",
            sensors_variant,
        )
