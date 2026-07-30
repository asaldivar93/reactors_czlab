"""Web GUI for the bioreactor controller.

Runs on the PC and on the Raspberry Pi. It hosts the OPC UA client and
the archiver in its own event loop, so recording is a task this process
starts and stops.
"""

from __future__ import annotations

import argparse
import logging

from nicegui import app, ui

from reactors_czlab.gui import pages  # noqa: F401 - registers the routes
from reactors_czlab.gui.state import DEFAULT_ENDPOINT, DEFAULT_PERIOD, STATE

_logger = logging.getLogger("gui")

DEFAULT_PORT = 8080


def setup_logging(verbose: bool = True) -> None:
    """Attach the file and stream handlers to the gui logger."""
    _logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter(
        "%(name)s: %(asctime)s %(levelname)s - %(message)s",
    )

    file_handler = logging.FileHandler("gui.log")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(logging.DEBUG if verbose else logging.INFO)

    _logger.addHandler(file_handler)
    _logger.addHandler(stream_handler)


def cli() -> None:
    """Parse the command line and serve the GUI."""
    parser = argparse.ArgumentParser(description="Run the bioreactor GUI")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--period", type=float, default=DEFAULT_PERIOD)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    setup_logging(verbose=not args.quiet)
    STATE.endpoint = args.endpoint
    STATE.period = args.period

    app.on_startup(STATE.connect)
    app.on_shutdown(STATE.disconnect)

    ui.run(
        host=args.host,
        port=args.port,
        title="Bioreactors",
        reload=False,
        show=False,
    )


if __name__ in {"__main__", "__mp_main__"}:
    cli()
