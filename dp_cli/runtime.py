from __future__ import annotations

from contextlib import AbstractContextManager

from dp_cli.models import ActivePage
from dp_cli.session_store import new_id, utc_now


class RuntimeContext(AbstractContextManager):
    def __init__(self, manager, meta, state, browser, tab):
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
            self.meta.runtime_id = new_id("rt")
            self.state.runtime_id = self.meta.runtime_id
            self.state.last_tab_id = None
            self.state.active_page = ActivePage()
            self.state.refs = {}
            self.state.next_ref_index = 1
        elif not self.state.runtime_id:
            self.state.runtime_id = self.meta.runtime_id
        self.meta.browser_pid = current_pid
        self.meta.runtime_status = "running"
        self.meta.last_seen_at = utc_now()

    def sync_page_identity(self) -> None:
        info = self.current_page_info()
        active_page = self.state.active_page
        page_changed = active_page.tab_id != info["tab_id"] or active_page.url != info["url"]
        if page_changed:
            snapshot_seq = active_page.snapshot_seq if active_page.page_id else 0
            self.state.active_page = ActivePage(
                tab_id=info["tab_id"],
                url=info["url"],
                title=info["title"],
                page_id=new_id("page"),
                snapshot_id=None,
                snapshot_seq=snapshot_seq,
            )
        else:
            self.state.active_page.title = info["title"]

    def begin_snapshot(self) -> ActivePage:
        self.sync_runtime_identity()
        self.sync_page_identity()
        self.state.active_page.snapshot_seq += 1
        self.state.active_page.snapshot_id = new_id("snap")
        return self.state.active_page

    def persist(self) -> None:
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
        return payloads

    def ref_item(self, ref: str) -> dict:
        item = self.state.refs.get(ref)
        if not item:
            raise KeyError(ref)
        return item

    def __exit__(self, exc_type, exc, tb) -> None:
        self.persist()
        return None
