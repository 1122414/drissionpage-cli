from __future__ import annotations

import json
import os
import socket
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from dp_cli.errors import BrowserConfigError
from dp_cli.models import (
    ActivePage,
    DEFAULT_PORT_END,
    DEFAULT_PORT_START,
    DEFAULT_SESSION,
    SessionMeta,
    SessionPaths,
    SessionState,
)

KNOWN_BROWSER_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)


def read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def port_is_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


class SessionStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def session_paths(self, session: str) -> SessionPaths:
        session_root = self.base_dir / "sessions" / session
        return SessionPaths(
            root=session_root,
            meta_file=session_root / "meta.json",
            state_file=session_root / "state.json",
            profile_dir=session_root / "profile",
        )

    def detect_browser_path(self) -> str:
        env_path = os.environ.get("DPCLI_BROWSER_PATH")
        if env_path and Path(env_path).exists():
            return env_path
        for candidate in KNOWN_BROWSER_PATHS:
            if Path(candidate).exists():
                return candidate
        raise BrowserConfigError(
            "Could not find a supported Chromium browser executable.",
            {"searched_paths": list(KNOWN_BROWSER_PATHS)},
        )

    def next_free_port(self) -> int:
        used_ports = set()
        sessions_root = self.base_dir / "sessions"
        if sessions_root.exists():
            for meta_file in sessions_root.glob("*/meta.json"):
                data = read_json(meta_file, {})
                port = data.get("port")
                if isinstance(port, int):
                    used_ports.add(port)

        for port in range(DEFAULT_PORT_START, DEFAULT_PORT_END + 1):
            if port in used_ports:
                continue
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind(("127.0.0.1", port))
                except OSError:
                    continue
            return port
        raise BrowserConfigError("No free session ports are available in the configured range.")

    def load_meta(self, session: str = DEFAULT_SESSION, headless: bool | None = None) -> SessionMeta:
        paths = self.session_paths(session)
        data = read_json(paths.meta_file, {})
        if not data:
            data = asdict(
                SessionMeta(
                    session=session,
                    session_id=new_id("sess"),
                    port=self.next_free_port(),
                    browser_path=self.detect_browser_path(),
                    user_data_dir=str(paths.profile_dir),
                    headless=bool(headless) if headless is not None else False,
                    runtime_id=new_id("rt"),
                )
            )
            write_json(paths.meta_file, data)
        else:
            data.setdefault("session", session)
            data.setdefault("session_id", new_id("sess"))
            data.setdefault("runtime_id", new_id("rt"))
            data.setdefault("runtime_status", "stale")
            data.setdefault("browser_pid", None)
            data.setdefault("last_seen_at", None)
            if headless is not None and bool(headless) != bool(data.get("headless", False)):
                if not port_is_listening(int(data["port"])):
                    data["headless"] = bool(headless)
            write_json(paths.meta_file, data)
        return SessionMeta(**data)

    def load_state(self, session: str = DEFAULT_SESSION) -> SessionState:
        paths = self.session_paths(session)
        data = read_json(paths.state_file, {"session": session, "refs": {}, "next_ref_index": 1})
        data.setdefault("session", session)
        data.setdefault("session_id", "")
        data.setdefault("runtime_id", "")
        data.setdefault("last_tab_id", None)
        active_page = data.get("active_page") or {}
        if not isinstance(active_page, dict):
            active_page = {}
        active_page.setdefault("tab_id", data.get("last_tab_id"))
        active_page.setdefault("url", None)
        active_page.setdefault("title", None)
        active_page.setdefault("page_id", None)
        active_page.setdefault("snapshot_id", None)
        active_page.setdefault("snapshot_seq", 0)
        data["active_page"] = ActivePage(**active_page)
        data.setdefault("refs", {})
        data.setdefault("next_ref_index", 1)
        return SessionState(**data)

    def save_meta(self, meta: SessionMeta) -> None:
        paths = self.session_paths(meta.session)
        write_json(paths.meta_file, asdict(meta))

    def save_state(self, state: SessionState) -> None:
        paths = self.session_paths(state.session)
        write_json(paths.state_file, asdict(state))
