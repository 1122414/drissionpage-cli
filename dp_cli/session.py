from __future__ import annotations

import socket
from pathlib import Path

from DrissionPage import Chromium, ChromiumOptions

from dp_cli.models import DEFAULT_SESSION, SessionMeta, SessionPaths, SessionState
from dp_cli.runtime import RuntimeContext
from dp_cli.session_store import SessionStore


class SessionManager:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.store = SessionStore(base_dir or Path.cwd() / ".dpcli")

    def session_paths(self, session: str) -> SessionPaths:
        return self.store.session_paths(session)

    def load_meta(self, session: str = DEFAULT_SESSION, headless: bool | None = None) -> SessionMeta:
        return self.store.load_meta(session=session, headless=headless)

    def load_state(self, session: str = DEFAULT_SESSION) -> SessionState:
        return self.store.load_state(session=session)

    def save_meta(self, meta: SessionMeta) -> None:
        self.store.save_meta(meta)

    def save_state(self, state: SessionState) -> None:
        self.store.save_state(state)

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
        ctx.persist()
        return ctx
