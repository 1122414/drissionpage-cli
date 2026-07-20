from __future__ import annotations

import time
from pathlib import Path

from DrissionPage import Chromium, ChromiumOptions

from dp_cli.models import DEFAULT_SESSION, SessionMeta, SessionPaths, SessionState
from dp_cli.runtime import RuntimeContext
from dp_cli.session_store import SessionStore, port_is_listening


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
        # DrissionPage documents latest_tab as the last activated tab. For agent
        # commands this is a better default than a persisted tab id after a click
        # opens a new tab or the user switches tabs manually.
        try:
            tab = browser.latest_tab
            if self._tab_is_usable(tab):
                return tab
        except Exception:
            pass

        saved_tab_id = state.last_tab_id
        if saved_tab_id and saved_tab_id in set(getattr(browser, "tab_ids", [])):
            try:
                tab = browser.get_tab(saved_tab_id)
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
        last_error = None
        for _ in range(2):
            try:
                browser = Chromium(self._build_options(meta))
                tab = self._restore_tab(browser, state)
                break
            except Exception as exc:
                last_error = exc
                time.sleep(0.5)
        else:
            raise last_error  # type: ignore[misc]
        ctx = RuntimeContext(self, meta, state, browser, tab)
        ctx.sync_runtime_identity()
        ctx.refresh_active_tab()
        ctx.persist()
        return ctx

    @staticmethod
    def _wait_for_port_close(port: int, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while port_is_listening(port):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)
        return True

    def close_session(self, session: str = DEFAULT_SESSION) -> dict:
        paths = self.session_paths(session)
        if not paths.meta_file.exists():
            return {"closed": False, "reason": "session_not_found", "session": session}

        meta = self.load_meta(session=session)
        was_running = port_is_listening(meta.port)
        close_errors = []
        forced = False
        if was_running:
            try:
                Chromium(self._build_options(meta)).quit(timeout=5, force=False)
            except Exception as exc:
                close_errors.append(f"normal close failed: {exc}")

            closed = self._wait_for_port_close(meta.port, timeout=5)
            if not closed:
                forced = True
                try:
                    Chromium(self._build_options(meta)).quit(timeout=5, force=True)
                except Exception as exc:
                    close_errors.append(f"forced close failed: {exc}")
                closed = self._wait_for_port_close(meta.port, timeout=5)
        else:
            closed = True

        still_running = not closed
        meta.runtime_status = "running" if still_running else "stopped"
        if not still_running:
            meta.browser_pid = None
        self.save_meta(meta)
        return {
            "closed": not still_running,
            "was_running": was_running,
            "session": session,
            "port": meta.port,
            "forced": forced,
            "error": "; ".join(close_errors) or None,
        }
