"""Launch the local research dashboard inside a Windows desktop window."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import webview
from werkzeug.serving import make_server

from research_app.server import create_app


HOST = "127.0.0.1"
PORT = 8765


def serve() -> None:
    server = make_server(HOST, PORT, create_app())
    server.serve_forever()


if __name__ == "__main__":
    threading.Thread(target=serve, daemon=True).start()
    time.sleep(0.5)
    webview.create_window("비선호시설 주민 숙의 연구", f"http://{HOST}:{PORT}", width=1440, height=940, min_size=(1080, 720))
    webview.start(gui="edgechromium")
