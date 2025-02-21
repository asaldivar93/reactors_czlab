import asyncio

from asyncua import Server, ua

from reactors_czlab import Actuator, Sensor
from reactors_czlab.opcua import ReactorOpc

control_config = {"method": "manual", "value": 150}

sensor_1 = Sensor("temperature_1")
sensor_2 = Sensor("temperature_2")

actuator_1 = Actuator("pump_1", control_config)
actuator_2 = Actuator("pump_2", control_config)

sensors = [sensor_1, sensor_2]
actuators = [actuator_1, actuator_2]

async def main():
    server = Server()
    await server.init()
    server.set_endpoint("opc.tcp://localhost:4840/")

    uri = "http://czlab"
    idx = await server.register_namespace(uri)

    reactor = ReactorOpc("Reactor_1", 5, sensors, actuators)
    await reactor.add_opc_nodes(server, idx)

    async with server:
        try:
            while True:
                await asyncio.sleep(1)
                await reactor.update_sensors()
        except KeyboardInterrupt:
            await server.stop()

if __name__ == "__main__":
    asyncio.run(main(), debug=True)
