from __future__ import annotations

import json
import os
import socket
from contextlib import AbstractContextManager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from DrissionPage import Chromium, ChromiumOptions

from drissionpage_cli.errors import BrowserConfigError
from drissionpage_cli.models import (
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


def _read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _port_is_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


class RuntimeContext(AbstractContextManager):
    def __init__(self, manager: "SessionManager", meta: SessionMeta, state: SessionState, browser, tab):
        self.manager = manager
        self.meta = meta
        self.state = state
        self.browser = browser
        self.tab = tab

    def current_page_info(self) -> dict:
        return {
            "tab_id": getattr(self.tab, "tab_id", None),
            "url": getattr(self.tab, "url", None),
            "title": getattr(self.tab, "title", None),
        }

    def sync_runtime_identity(self) -> None:
        current_pid = getattr(self.browser, "process_id", None)
        if self.meta.runtime_id == "" or self.meta.browser_pid != current_pid:
            self.meta.runtime_id = _new_id("rt")
            self.state.runtime_id = self.meta.runtime_id
            self.state.last_tab_id = None
            self.state.active_page = ActivePage()
            self.state.refs = {}
            self.state.next_ref_index = 1
        elif not self.state.runtime_id:
            self.state.runtime_id = self.meta.runtime_id
        self.meta.browser_pid = current_pid
        self.meta.runtime_status = "running"
        self.meta.last_seen_at = _utc_now()

    def sync_page_identity(self) -> None:
        info = self.current_page_info()
        active_page = self.state.active_page
        page_changed = (
            active_page.tab_id != info["tab_id"]
            or active_page.url != info["url"]
        )
        if page_changed:
            snapshot_seq = active_page.snapshot_seq if active_page.page_id else 0
            self.state.active_page = ActivePage(
                tab_id=info["tab_id"],
                url=info["url"],
                title=info["title"],
                page_id=_new_id("page"),
                snapshot_id=None,
                snapshot_seq=snapshot_seq,
            )
        else:
            self.state.active_page.title = info["title"]

    def begin_snapshot(self) -> ActivePage:
        self.sync_runtime_identity()
        self.sync_page_identity()
        self.state.active_page.snapshot_seq += 1
        self.state.active_page.snapshot_id = _new_id("snap")
        self.save()
        return self.state.active_page

    def save(self) -> None:
        self.state.session_id = self.meta.session_id
        self.state.runtime_id = self.meta.runtime_id
        self.state.last_tab_id = getattr(self.tab, "tab_id", self.state.last_tab_id)
        self.manager.save_meta(self.meta)
        self.manager.save_state(self.state)

    def upsert_refs(self, records) -> list[dict]:
        active_page = self.state.active_page
        xpath_to_ref = {
            item.get("xpath"): ref
            for ref, item in self.state.refs.items()
            if (
                isinstance(item, dict)
                and item.get("xpath")
                and item.get("runtime_id") == self.meta.runtime_id
                and item.get("page_id") == active_page.page_id
            )
        }
        payloads = []
        for record in records:
            ref = xpath_to_ref.get(record.xpath)
            if ref is None:
                ref = f"e{self.state.next_ref_index}"
                self.state.next_ref_index += 1
            item = record.to_output(ref)
            item["session_id"] = self.meta.session_id
            item["runtime_id"] = self.meta.runtime_id
            item["page_id"] = active_page.page_id
            item["snapshot_id"] = active_page.snapshot_id
            item["url"] = active_page.url
            self.state.refs[ref] = item
            payloads.append(item)
        self.save()
        return payloads

    def ref_item(self, ref: str) -> dict:
        item = self.state.refs.get(ref)
        if not item:
            raise KeyError(ref)
        return item

    def __exit__(self, exc_type, exc, tb) -> None:
        self.save()
        return None


class SessionManager:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path.cwd() / ".dpcli"
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
                data = _read_json(meta_file, {})
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
        data = _read_json(paths.meta_file, {})
        if not data:
            data = asdict(
                SessionMeta(
                    session=session,
                    session_id=_new_id("sess"),
                    port=self.next_free_port(),
                    browser_path=self.detect_browser_path(),
                    user_data_dir=str(paths.profile_dir),
                    headless=bool(headless) if headless is not None else False,
                    runtime_id=_new_id("rt"),
                )
            )
            _write_json(paths.meta_file, data)
        else:
            data.setdefault("session", session)
            data.setdefault("session_id", _new_id("sess"))
            data.setdefault("runtime_id", _new_id("rt"))
            data.setdefault("runtime_status", "stale")
            data.setdefault("browser_pid", None)
            data.setdefault("last_seen_at", None)
            if headless is not None and bool(headless) != bool(data.get("headless", False)):
                # A live browser session owns its runtime mode. Do not mutate a running
                # session between headed and headless, otherwise later commands may try
                # to attach with options that no longer match the browser behind the port.
                if not _port_is_listening(int(data["port"])):
                    data["headless"] = bool(headless)
            _write_json(paths.meta_file, data)
        return SessionMeta(**data)

    def load_state(self, session: str = DEFAULT_SESSION) -> SessionState:
        paths = self.session_paths(session)
        data = _read_json(paths.state_file, {"session": session, "refs": {}, "next_ref_index": 1})
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
        _write_json(paths.meta_file, asdict(meta))

    def save_state(self, state: SessionState) -> None:
        paths = self.session_paths(state.session)
        _write_json(paths.state_file, asdict(state))

    def _build_options(self, meta: SessionMeta) -> ChromiumOptions:
        options = ChromiumOptions(read_file=False)
        options.set_browser_path(meta.browser_path)
        options.set_user_data_path(meta.user_data_dir)
        options.set_local_port(meta.port)
        if meta.headless:
            options.set_argument("--headless", "new")
        return options

    def _tab_is_usable(self, tab) -> bool:
        try:
            getattr(tab, "tab_id", None)
            getattr(tab, "url", None)
            return True
        except Exception:
            return False

    def _restore_tab(self, browser, state: SessionState):
        # Prefer tabs that the live browser currently exposes instead of trusting
        # a persisted tab id from a previous browser lifecycle.
        try:
            tab = browser.latest_tab
            if self._tab_is_usable(tab):
                return tab
        except Exception:
            pass

        for tab_id in reversed(list(getattr(browser, "tab_ids", []))):
            try:
                tab = browser.get_tab(tab_id)
                if self._tab_is_usable(tab):
                    return tab
            except Exception:
                continue
        return browser.new_tab(url="about:blank")

    def open_runtime(self, session: str = DEFAULT_SESSION, headless: bool | None = None) -> RuntimeContext:
        meta = self.load_meta(session=session, headless=headless)
        state = self.load_state(session=session)
        if not state.session_id:
            state.session_id = meta.session_id
        browser = Chromium(self._build_options(meta))
        tab = self._restore_tab(browser, state)
        ctx = RuntimeContext(self, meta, state, browser, tab)
        ctx.sync_runtime_identity()
        ctx.sync_page_identity()
        ctx.save()
        return ctx
