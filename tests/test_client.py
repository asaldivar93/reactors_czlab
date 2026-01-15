"""Script to run experiments."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from asyncua import Client

from reactors_czlab.opcua.client import ReactorOpcClient
from reactors_czlab.server_info import server_test
from reactors_czlab.sql.operations import create_experiment

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


async def main():
    experiment = "test"
    reactors = ["R0", "R2", "R3"]
    volume = 2
    reactor_nodes = [ReactorOpcClient(k, experiment) for k in reactors]
    create_experiment(experiment, datetime.now(), reactors, volume)
    client = Client(url="opc.tcp://localhost:4840/")
    async with client:
        for reactor in reactor_nodes:
            variables = server_test[reactor.id]
            await reactor.connect_nodes(client, variables)
        while True:
            await asyncio.sleep(0.1)
            await client.check_connection()


if __name__ == "__main__":
    asyncio.run(main(), debug=True)
