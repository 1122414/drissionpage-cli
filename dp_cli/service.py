from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager

from dp_cli.adapter import DrissionPageAdapter
from dp_cli.errors import (
    ElementNotFoundError,
    InvalidInputError,
    RefNotFoundError,
    RefStaleError,
)
from dp_cli.models import DEFAULT_SESSION
from dp_cli.session import SessionManager


class CliService:
    def __init__(self, sessions: SessionManager | None = None, adapter: DrissionPageAdapter | None = None) -> None:
        self.sessions = sessions or SessionManager()
        self.adapter = adapter or DrissionPageAdapter()

    def open_page(self, url: str, session: str = DEFAULT_SESSION, headless: bool | None = None) -> dict:
        with self._with_runtime(session=session, headless=headless) as runtime:
            page = self.adapter.open_url(runtime.tab, url)
            runtime.sync_page_identity()
            runtime.persist()
            return {"page": page}

    def snapshot_page(self, session: str = DEFAULT_SESSION, headless: bool | None = None) -> dict:
        with self._with_runtime(session=session, headless=headless) as runtime:
            runtime.begin_snapshot()
            elements = runtime.upsert_refs(self.adapter.interactive_elements(runtime.tab))
            runtime.persist()
            return {
                "page": self._page_payload(runtime),
                "page_identity": self._page_identity_payload(runtime),
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
        with self._with_runtime(session=session, headless=headless) as runtime:
            runtime.begin_snapshot()
            if locator:
                records = self.adapter.find_by_locator(runtime.tab, locator)
            else:
                records = self.adapter.find_by_text(runtime.tab, text or "")
            elements = runtime.upsert_refs(records)
            runtime.persist()
            return {
                "page": self._page_payload(runtime),
                "page_identity": self._page_identity_payload(runtime),
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
        return self._perform_element_action(
            session=session,
            headless=headless,
            ref=ref,
            locator=locator,
            element_error_message="Could not find element to click.",
            action=lambda element, _text: self.adapter.click(element),
            include_payload=lambda runtime, target_locator: {
                "page": self._page_payload(runtime),
                "target": self._target_payload(ref, target_locator),
            },
        )

    def type_into_element(
        self,
        text: str,
        session: str = DEFAULT_SESSION,
        ref: str | None = None,
        locator: str | None = None,
        headless: bool | None = None,
    ) -> dict:
        return self._perform_element_action(
            session=session,
            headless=headless,
            ref=ref,
            locator=locator,
            text=text,
            element_error_message="Could not find element to type into.",
            action=lambda element, value: self.adapter.type_text(element, value or ""),
            include_payload=lambda runtime, target_locator: {
                "page": self._page_payload(runtime),
                "target": self._target_payload(ref, target_locator),
                "typed_text": text,
            },
        )

    def inspect_session(self, session: str = DEFAULT_SESSION, headless: bool | None = None) -> dict:
        with self._with_runtime(session=session, headless=headless) as runtime:
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

    @contextmanager
    def _with_runtime(self, session: str, headless: bool | None):
        with self.sessions.open_runtime(session=session, headless=headless) as runtime:
            yield runtime

    def _page_payload(self, runtime) -> dict:
        return self.adapter.page_info(runtime.tab)

    def _page_identity_payload(self, runtime) -> dict:
        return {
            "runtime_id": runtime.meta.runtime_id,
            "page_id": runtime.state.active_page.page_id,
            "snapshot_id": runtime.state.active_page.snapshot_id,
            "snapshot_seq": runtime.state.active_page.snapshot_seq,
        }

    def _target_payload(self, ref: str | None, locator: str) -> dict:
        return {"ref": ref, "locator": locator}

    def _resolve_target(self, runtime, ref: str | None, locator: str | None) -> str:
        if ref:
            try:
                item = runtime.ref_item(ref)
            except KeyError as exc:
                raise RefNotFoundError(ref) from exc
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

    def _perform_element_action(
        self,
        session: str,
        headless: bool | None,
        ref: str | None,
        locator: str | None,
        element_error_message: str,
        action: Callable,
        include_payload: Callable,
        text: str | None = None,
    ) -> dict:
        with self._with_runtime(session=session, headless=headless) as runtime:
            target_locator = self._resolve_target(runtime, ref, locator)
            element = self.adapter.resolve(runtime.tab, target_locator)
            if not element:
                raise ElementNotFoundError(element_error_message, {"locator": target_locator})
            action(element, text)
            runtime.sync_page_identity()
            runtime.persist()
            return include_payload(runtime, target_locator)
