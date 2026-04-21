from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import threading
import uuid
from contextlib import closing
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from drissionpage_cli.session import SessionManager

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "site"


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LocalFixtureServer:
    def __init__(self) -> None:
        self.port = free_port()
        handler = partial(SimpleHTTPRequestHandler, directory=str(FIXTURE_DIR))
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/index.html"

    def __enter__(self) -> "LocalFixtureServer":
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def run_cli(*args: str, check: bool = True) -> dict:
    command = [sys.executable, "-m", "drissionpage_cli", *args]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    payload = json.loads(completed.stdout)
    if check and completed.returncode != 0:
        raise AssertionError(
            f"CLI command failed: {' '.join(command)}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    payload["_returncode"] = completed.returncode
    payload["_stderr"] = completed.stderr
    return payload


def unique_session(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def cleanup_session(session: str) -> None:
    manager = SessionManager()
    paths = manager.session_paths(session)
    if not paths.meta_file.exists():
        return
    try:
        runtime = manager.open_runtime(session=session)
        runtime.browser.quit()
    except Exception:
        pass
    shutil.rmtree(paths.root, ignore_errors=True)
