from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from queue import Queue
from typing import Any

from reactors_czlab.opcua.client import OpcClient


@dataclass
class Request:
    type: str
    nodeid: str
    value: Any


class OpcWorker:
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self.client: OpcClient = OpcClient(endpoint)
        self.mappings: dict[str, dict] = {}

        self._thread: threading.Thread
        self._loop: asyncio.AbstractEventLoop
        self._request_q: Queue[Request] = Queue()
        self._stop = threading.Event()

    def start(self):
        if self._thread and self._thread.is_alive():
            return

        self._stop.clear()
        self._thread = threading.Thread(target=self._run_thread, daemon=True)
        self._thread.start()

    def _run_thread(self):
        asyncio.run(self._asyncio_main())

    async def _asyncio_main(self):
        await self.client.connect()
