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
            self.state.container_refs = {}
            self.state.element_refs = {}
            self.state.next_container_index = 1
            self.state.next_element_index = 1
            self.state.last_snapshot_file = None
            self.state.last_snapshot_mode = None
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

    def upsert_nodes(self, records) -> list[dict]:
        active_page = self.state.active_page
        refs_by_type = {
            "container": self.state.container_refs,
            "element": self.state.element_refs,
        }
        next_index_attr = {
            "container": "next_container_index",
            "element": "next_element_index",
        }
        prefix = {
            "container": "r",
            "element": "e",
        }
        xpath_to_ref = {}
        for ref_map in refs_by_type.values():
            for ref, item in ref_map.items():
                if (
                    isinstance(item, dict)
                    and item.get("xpath")
                    and item.get("runtime_id") == self.meta.runtime_id
                    and item.get("page_id") == active_page.page_id
                ):
                    xpath_to_ref[item["xpath"]] = ref

        assigned: list[tuple[object, str]] = []
        for record in records:
            ref = xpath_to_ref.get(record.xpath)
            if ref is None:
                attr = next_index_attr[record.ref_type]
                ref = f"{prefix[record.ref_type]}{getattr(self.state, attr)}"
                setattr(self.state, attr, getattr(self.state, attr) + 1)
                xpath_to_ref[record.xpath] = ref
            assigned.append((record, ref))

        payloads = []
        for record, ref in assigned:
            item = record.to_output(ref)
            item["xpath"] = record.xpath
            item["parent_ref"] = xpath_to_ref.get(record.parent_xpath) if record.parent_xpath else None
            item["parent_xpath"] = record.parent_xpath
            item["session_id"] = self.meta.session_id
            item["runtime_id"] = self.meta.runtime_id
            item["page_id"] = active_page.page_id
            item["snapshot_id"] = active_page.snapshot_id
            item["url"] = active_page.url
            refs_by_type[record.ref_type][ref] = item
            payloads.append(item)
        return payloads

    def remember_snapshot(self, artifact_file: str, mode: str) -> None:
        self.state.last_snapshot_file = artifact_file
        self.state.last_snapshot_mode = mode

    def ref_item(self, ref: str) -> dict:
        item = self.state.container_refs.get(ref) or self.state.element_refs.get(ref)
        if not item:
            raise KeyError(ref)
        return item

    def total_ref_count(self) -> int:
        return len(self.state.container_refs) + len(self.state.element_refs)

    def __exit__(self, exc_type, exc, tb) -> None:
        self.persist()
        return None
