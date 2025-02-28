"""Script to run experiments."""

from __future__ import annotations

import asyncio
import logging

from asyncua import Client
from reactors_czlab.opcua.client import ReactorOpcClient

_logger = logging.getLogger("run")
_logger.setLevel(logging.DEBUG)

_formatter = logging.Formatter("%(name)s: %(asctime)s %(levelname)s - %(message)s")

_file_handler = logging.FileHandler("client.log")
_file_handler.setFormatter(_formatter)
_file_handler.setLevel(logging.DEBUG)

_stream_handler = logging.StreamHandler()
_stream_handler.setLevel(logging.WARNING)
_stream_handler.setFormatter(_formatter)

_logger.addHandler(_file_handler)
_logger.addHandler(_stream_handler)

# We use the node ids to connect to the server
reactors = ["ns=2;i=1", "ns=2;i=29"]
# We need to know all the node ids of the variables we'll monitor
# and group them based on the reactor they occupy.

# varibles = [reactor_0_vars, reactor_1_vars, ....]
# The keys "model", "variable", "datetime", "value" are used
# to commit to the sql database
variables = [
    {
        "ns=2;i=4": {
            "model": "none",
            "variable": "temp",
            "datetime": None,
            "value": None,
        },
        "ns=2;i=7": {
            "model": "none",
            "variable": "temp",
            "datetime": None,
            "value": None,
        },
    },
    {
        "ns=2;i=32": {
            "model": "none",
            "variable": "temp",
            "datetime": None,
            "value": None,
        },
        "ns=2;i=35": {
            "model": "none",
            "variable": "temp",
            "datetime": None,
            "value": None,
        },
    },
]

uri = "opc.tcp://localhost:4840"


async def main():
    reactor_nodes = [
        ReactorOpcClient(f"Reactor_{i}") for i,v in enumerate(reactors)
    ]
    client = Client(url=uri)
    async with client:
        for i, reactor in enumerate(reactor_nodes):
            await reactor.connect_nodes(client, variables[i])
        while True:
            await asyncio.sleep(1)
            await client.check_connection()


if __name__ == "__main__":
    asyncio.run(main(), debug=True)
