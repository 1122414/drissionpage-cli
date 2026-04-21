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

from dp_cli.session import SessionManager

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
    command = [sys.executable, "-X", "utf8", "-m", "dp_cli", *args]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    payload = json.loads(stdout)
    if check and completed.returncode != 0:
        raise AssertionError(
            f"CLI command failed: {' '.join(command)}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        )
    payload["_returncode"] = completed.returncode
    payload["_stderr"] = stderr
    return payload


def run_local_workflow(session: str, url: str, typed_text: str = "Agentic CLI") -> dict:
    opened = run_cli("open", url, "--session", session, "--headless")
    button_match = run_cli("find", "--session", session, "--headless", "--text", "Primary Action")
    input_match = run_cli("find", "--session", session, "--headless", "--locator", "#name-input")
    button_ref = button_match["data"]["elements"][0]["ref"]
    input_ref = input_match["data"]["elements"][0]["ref"]
    typed = run_cli("type", "--session", session, "--headless", "--ref", input_ref, "--text", typed_text)
    clicked = run_cli("click", "--session", session, "--headless", "--ref", button_ref)
    snapshot = run_cli("snapshot", "--session", session, "--headless")
    return {
        "opened": opened,
        "button_match": button_match,
        "input_match": input_match,
        "button_ref": button_ref,
        "input_ref": input_ref,
        "typed": typed,
        "clicked": clicked,
        "snapshot": snapshot,
    }


def run_public_smoke_workflow(session: str, url: str = "https://example.com") -> dict:
    opened = run_cli("open", url, "--session", session, "--headless")
    found = run_cli("find", "--session", session, "--headless", "--locator", "tag:a")
    ref = found["data"]["elements"][0]["ref"]
    clicked = run_cli("click", "--session", session, "--headless", "--ref", ref)
    return {
        "opened": opened,
        "found": found,
        "ref": ref,
        "clicked": clicked,
    }


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
