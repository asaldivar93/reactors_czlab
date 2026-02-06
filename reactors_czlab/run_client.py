"""Script to run experiments."""

from __future__ import annotations

import asyncio
import logging

from reactors_czlab.opcua.client import OpcClient

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
    client = OpcClient("opc.tcp://localhost:4840/")
    await client.connect()
    async with client.client:
        await client.init_subcriptions()
        while True:
            await asyncio.sleep(0.1)
            # await client._client.check_connection()


if __name__ == "__main__":
    asyncio.run(main(), debug=True)
