"""Script to run experiments."""

from __future__ import annotations

import asyncio
import json
import logging

from asyncua import Client

from reactors_czlab.opcua.client import ReactorOpcClient

_logger = logging.getLogger("client")
_logger.setLevel(logging.DEBUG)

_formatter = logging.Formatter(
    "%(name)s: %(asctime)s %(levelname)s - %(message)s",
)

_file_handler = logging.FileHandler("client.log")
_file_handler.setFormatter(_formatter)
_file_handler.setLevel(logging.DEBUG)

_stream_handler = logging.StreamHandler()
_stream_handler.setLevel(logging.DEBUG)
_stream_handler.setFormatter(_formatter)

_logger.addHandler(_file_handler)
_logger.addHandler(_stream_handler)

# We use the node ids to connect to the server

# We need to know all the node ids of the variables we'll monitor
# and group them based on the reactor they occupy.

# The keys "model", "variable", "datetime", "value" are used
# to commit to the sql database
with open("server_vars.json") as file:
    server_nodes = json.load(file)


async def main():
    reactor_nodes = [ReactorOpcClient(k) for k in server_nodes]
    client = Client(url="opc.tcp://localhost:4840/")
    async with client:
        for reactor in reactor_nodes:
            variables = server_nodes[reactor.id]
            await reactor.connect_nodes(client, variables)
        while True:
            await asyncio.sleep(1)
            await client.check_connection()


if __name__ == "__main__":
    asyncio.run(main(), debug=True)
