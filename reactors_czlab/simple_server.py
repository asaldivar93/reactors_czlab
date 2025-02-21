"""server test."""
import asyncio

from asyncua import Server, ua

async def main():
    server = Server()
    await server.init()
    server.set_endpoint("opc.tcp://localhost:4840/")

    uri = "http://czlab"
    idx = await server.register_namespace(uri)

    reactor = await server.nodes.objects.add_object(idx, "Reactor_1")
    myvar = await reactor.add_variable(idx, "Myvariable", 6.7)

    print(myvar)
    print(type(myvar))
    sensor = await reactor.add_object(idx, "Sensor_1")
    value = await sensor.add_variable(idx, "Temperature", 6.7)
    print(value)
    print(type(value))

    async with server:
        while True:
            await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main(), debug=True)
