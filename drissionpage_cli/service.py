from __future__ import annotations

from drissionpage_cli.adapter import DrissionPageAdapter
from drissionpage_cli.errors import (
    ElementNotFoundError,
    InvalidInputError,
    RefNotFoundError,
    RefStaleError,
)
from drissionpage_cli.models import DEFAULT_SESSION
from drissionpage_cli.session import SessionManager


class CliService:
    def __init__(self, sessions: SessionManager | None = None, adapter: DrissionPageAdapter | None = None) -> None:
        self.sessions = sessions or SessionManager()
        self.adapter = adapter or DrissionPageAdapter()

    def open_page(self, url: str, session: str = DEFAULT_SESSION, headless: bool | None = None) -> dict:
        with self.sessions.open_runtime(session=session, headless=headless) as runtime:
            page = self.adapter.open_url(runtime.tab, url)
            runtime.sync_page_identity()
            runtime.save()
            return {"page": page}

    def snapshot_page(self, session: str = DEFAULT_SESSION, headless: bool | None = None) -> dict:
        with self.sessions.open_runtime(session=session, headless=headless) as runtime:
            runtime.begin_snapshot()
            elements = runtime.upsert_refs(self.adapter.interactive_elements(runtime.tab))
            return {
                "page": self.adapter.page_info(runtime.tab),
                "page_identity": self._page_identity(runtime),
                "count": len(elements),
                "elements": elements,
            }

    def find_elements(
        self,
        session: str = DEFAULT_SESSION,
        locator: str | None = None,
        text: str | None = None,
        headless: bool | None = None,
    ) -> dict:
        if not locator and not text:
            raise InvalidInputError("find requires either --locator or --text.")
        with self.sessions.open_runtime(session=session, headless=headless) as runtime:
            runtime.begin_snapshot()
            if locator:
                records = self.adapter.find_by_locator(runtime.tab, locator)
            else:
                records = self.adapter.find_by_text(runtime.tab, text or "")
            elements = runtime.upsert_refs(records)
            return {
                "page": self.adapter.page_info(runtime.tab),
                "page_identity": self._page_identity(runtime),
                "count": len(elements),
                "elements": elements,
                "query": {"locator": locator, "text": text},
            }

    def click_element(
        self,
        session: str = DEFAULT_SESSION,
        ref: str | None = None,
        locator: str | None = None,
        headless: bool | None = None,
    ) -> dict:
        with self.sessions.open_runtime(session=session, headless=headless) as runtime:
            target_locator = self._resolve_target(runtime, ref, locator)
            element = self.adapter.resolve(runtime.tab, target_locator)
            if not element:
                raise ElementNotFoundError("Could not find element to click.", {"locator": target_locator})
            self.adapter.click(element)
            runtime.sync_page_identity()
            runtime.save()
            return {"page": self.adapter.page_info(runtime.tab), "target": {"ref": ref, "locator": target_locator}}

    def type_into_element(
        self,
        text: str,
        session: str = DEFAULT_SESSION,
        ref: str | None = None,
        locator: str | None = None,
        headless: bool | None = None,
    ) -> dict:
        with self.sessions.open_runtime(session=session, headless=headless) as runtime:
            target_locator = self._resolve_target(runtime, ref, locator)
            element = self.adapter.resolve(runtime.tab, target_locator)
            if not element:
                raise ElementNotFoundError("Could not find element to type into.", {"locator": target_locator})
            self.adapter.type_text(element, text)
            runtime.save()
            return {
                "page": self.adapter.page_info(runtime.tab),
                "target": {"ref": ref, "locator": target_locator},
                "typed_text": text,
            }

    def inspect_session(self, session: str = DEFAULT_SESSION, headless: bool | None = None) -> dict:
        with self.sessions.open_runtime(session=session, headless=headless) as runtime:
            return {
                "session_name": runtime.meta.session,
                "session_id": runtime.meta.session_id,
                "runtime": {
                    "runtime_id": runtime.meta.runtime_id,
                    "status": runtime.meta.runtime_status,
                    "browser_pid": runtime.meta.browser_pid,
                    "port": runtime.meta.port,
                    "headless": runtime.meta.headless,
                    "last_seen_at": runtime.meta.last_seen_at,
                },
                "page": {
                    "tab_id": runtime.state.active_page.tab_id,
                    "url": runtime.state.active_page.url,
                    "title": runtime.state.active_page.title,
                    "page_id": runtime.state.active_page.page_id,
                    "snapshot_id": runtime.state.active_page.snapshot_id,
                    "snapshot_seq": runtime.state.active_page.snapshot_seq,
                },
                "ref_count": len(runtime.state.refs),
            }

    def _resolve_target(
        self,
        runtime,
        ref: str | None,
        locator: str | None,
    ) -> str:
        if ref:
            item = runtime.state.refs.get(ref)
            if not item:
                raise RefNotFoundError(ref)
            if item.get("runtime_id") != runtime.meta.runtime_id:
                raise RefStaleError(
                    ref,
                    {
                        "expected_runtime_id": runtime.meta.runtime_id,
                        "actual_runtime_id": item.get("runtime_id"),
                    },
                )
            current_page_id = runtime.state.active_page.page_id
            if item.get("page_id") != current_page_id:
                raise RefStaleError(
                    ref,
                    {
                        "expected_page_id": current_page_id,
                        "actual_page_id": item.get("page_id"),
                    },
                )
            return item["locator"]
        if locator:
            return locator
        raise InvalidInputError("Command requires either --ref or --locator.")

    def _page_identity(self, runtime) -> dict:
        return {
            "runtime_id": runtime.meta.runtime_id,
            "page_id": runtime.state.active_page.page_id,
            "snapshot_id": runtime.state.active_page.snapshot_id,
            "snapshot_seq": runtime.state.active_page.snapshot_seq,
        }
