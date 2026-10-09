# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A tiny stdlib HTTP server for the scene pages, on ``127.0.0.1`` and a free port. No network beyond loopback."""

from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass


class SceneServer:
    """Serves one folder over HTTP in a daemon thread; ``start()`` returns the base URL (ending with ``/``)."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        if self._server is None:
            handler = functools.partial(_QuietHandler, directory=str(self._root))
            self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            self._thread = threading.Thread(
                target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, name="scene-server", daemon=True
            )
            self._thread.start()
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/"

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
