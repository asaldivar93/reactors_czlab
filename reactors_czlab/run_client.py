"""Script to run experiments: subscribe to the server and archive the data."""

from __future__ import annotations

import argparse
import asyncio
import logging

from reactors_czlab.opcua.client import OpcClient

_logger = logging.getLogger("client")

DEFAULT_ENDPOINT = "opc.tcp://localhost:4840/"


def setup_logging(verbose: bool = True) -> None:
    """Attach the file and stream handlers to the client logger."""
    _logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter(
        "%(name)s: %(asctime)s %(levelname)s - %(message)s",
    )

    file_handler = logging.FileHandler("client.log")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(logging.DEBUG if verbose else logging.INFO)

    _logger.addHandler(file_handler)
    _logger.addHandler(stream_handler)


async def main(endpoint: str) -> None:
    """Subscribe to the server and stream readings into PostgreSQL."""
    async with OpcClient(endpoint) as client:
        await client.init_subscriptions()
        # The archiver runs until interrupted.
        await client.run_archiver()


def cli() -> None:
    """Parse the command line and run the client."""
    parser = argparse.ArgumentParser(description="Run the bioreactor client")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    setup_logging(verbose=not args.quiet)
    try:
        asyncio.run(main(args.endpoint))
    except KeyboardInterrupt:
        _logger.info("Interrupted")


if __name__ == "__main__":
    cli()
