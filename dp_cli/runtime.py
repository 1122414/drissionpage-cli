from __future__ import annotations

import time
from contextlib import AbstractContextManager

from dp_cli.fingerprint import FingerprintIndex, NodeFingerprint, SemanticFingerprint
from dp_cli.locator import LocatorGenerator
from dp_cli.models import ActivePage
from dp_cli.session_store import new_id, utc_now


class RuntimeContext(AbstractContextManager):
    def __init__(self, manager, meta, state, browser, tab):
        self.manager = manager
        self.meta = meta
        self.state = state
        self.browser = browser
        self.tab = tab
        self.fingerprint_index = FingerprintIndex()
        self.locator_generator = LocatorGenerator()

    def _tab_is_usable(self, tab) -> bool:
        try:
            getattr(tab, "tab_id", None)
            getattr(tab, "url", None)
            return True
        except Exception:
            return False

    def refresh_active_tab(self) -> dict:
        old_tab_id = getattr(self.tab, "tab_id", None)
        old_url = getattr(self.tab, "url", None)
        changed = False
        try:
            latest = self.browser.latest_tab
        except Exception:
            latest = None
        if latest is not None and self._tab_is_usable(latest):
            latest_tab_id = getattr(latest, "tab_id", None)
            latest_url = getattr(latest, "url", None)
            if latest_tab_id != old_tab_id or latest_url != old_url:
                self.tab = latest
                changed = True
        self.sync_page_identity()
        return {
            "active_tab_changed": changed,
            "old_tab_id": old_tab_id,
            "new_tab_id": getattr(self.tab, "tab_id", None),
            "old_url": old_url,
            "new_url": getattr(self.tab, "url", None),
        }

    def refresh_after_possible_tab_change(self, before_tab_ids: set[str], timeout: float = 1.5) -> dict:
        before_tab_id = getattr(self.tab, "tab_id", None)
        deadline = time.time() + timeout
        transition = {
            "opened_new_tab": False,
            "active_tab_changed": False,
            "old_tab_id": before_tab_id,
            "new_tab_id": before_tab_id,
            "old_url": getattr(self.tab, "url", None),
            "new_url": getattr(self.tab, "url", None),
        }
        while True:
            current_ids = set(getattr(self.browser, "tab_ids", []) or [])
            latest = None
            try:
                latest = self.browser.latest_tab
            except Exception:
                latest = None
            latest_id = getattr(latest, "tab_id", None) if latest is not None else None
            opened_new_tab = bool(current_ids - before_tab_ids)
            latest_changed = latest_id is not None and latest_id != before_tab_id
            if latest is not None and self._tab_is_usable(latest) and (opened_new_tab or latest_changed):
                self.tab = latest
                self.sync_page_identity()
                transition.update(
                    {
                        "opened_new_tab": opened_new_tab,
                        "active_tab_changed": latest_changed,
                        "new_tab_id": getattr(self.tab, "tab_id", None),
                        "new_url": getattr(self.tab, "url", None),
                    }
                )
                return transition
            if time.time() >= deadline:
                break
            time.sleep(0.1)

        refreshed = self.refresh_active_tab()
        transition.update(
            {
                "opened_new_tab": False,
                "active_tab_changed": refreshed.get("active_tab_changed", False),
                "new_tab_id": refreshed.get("new_tab_id"),
                "new_url": refreshed.get("new_url"),
            }
        )
        return transition

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
            self.state.last_snapshot_fingerprints = {}
            self.state.last_snapshot_diff = {}
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
            self.state.last_snapshot_fingerprints = {}
            self.state.last_snapshot_diff = {}
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

    def upsert_nodes(self, records, *, track_delta: bool = False) -> list[dict]:
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
        existing_by_ref = {}
        for ref_map in refs_by_type.values():
            for ref, item in ref_map.items():
                if (
                    isinstance(item, dict)
                    and item.get("xpath")
                    and item.get("runtime_id") == self.meta.runtime_id
                    and item.get("page_id") == active_page.page_id
                ):
                    xpath_to_ref[item["xpath"]] = ref
                    existing_by_ref[ref] = item

        semantic = SemanticFingerprint()
        candidate_lookup: dict[str, dict] = {}
        candidate_payloads = []
        for record in records:
            candidate = record.to_output("")
            candidate["xpath"] = record.xpath
            candidate["parent_xpath"] = record.parent_xpath
            candidate_lookup[record.xpath] = candidate
            candidate_payloads.append((record, candidate))
        for _record, candidate in candidate_payloads:
            candidate["semantic_features"] = semantic.features(
                candidate,
                candidate_lookup,
            )
            candidate["semantic_fingerprint"] = semantic.compute(
                candidate,
                candidate_lookup,
            )

        semantic_to_refs: dict[tuple[str, str], list[str]] = {}
        for ref, item in existing_by_ref.items():
            semantic_fingerprint = str(item.get("semantic_fingerprint") or "")
            if semantic_fingerprint:
                key = (str(item.get("ref_type") or ""), semantic_fingerprint)
                semantic_to_refs.setdefault(key, []).append(ref)

        assigned: list[tuple[object, str]] = []
        assigned_candidates: list[dict] = []
        used_refs = set()
        rebound_refs = []
        for record, candidate in candidate_payloads:
            ref = xpath_to_ref.get(record.xpath)
            if ref is not None:
                existing = existing_by_ref.get(ref) or {}
                existing_features = existing.get("semantic_features")
                if isinstance(existing_features, dict):
                    similarity = semantic.similarity(
                        existing_features,
                        candidate["semantic_features"],
                    )
                    if similarity < 0.55:
                        ref = None
            if ref is None:
                exact_refs = semantic_to_refs.get(
                    (record.ref_type, candidate["semantic_fingerprint"]),
                    [],
                )
                ref = next(
                    (candidate_ref for candidate_ref in exact_refs if candidate_ref not in used_refs),
                    None,
                )
            if ref is None:
                scored = []
                for candidate_ref, existing in existing_by_ref.items():
                    if candidate_ref in used_refs:
                        continue
                    if existing.get("ref_type") != record.ref_type:
                        continue
                    existing_features = existing.get("semantic_features")
                    if not isinstance(existing_features, dict):
                        continue
                    scored.append(
                        (
                            semantic.similarity(
                                existing_features,
                                candidate["semantic_features"],
                            ),
                            candidate_ref,
                        )
                    )
                scored.sort(reverse=True)
                best = scored[0] if scored else (0.0, "")
                runner_up = scored[1][0] if len(scored) > 1 else 0.0
                if best[0] >= 0.82 and best[0] - runner_up >= 0.12:
                    ref = best[1]
            if ref is None:
                attr = next_index_attr[record.ref_type]
                ref = f"{prefix[record.ref_type]}{getattr(self.state, attr)}"
                setattr(self.state, attr, getattr(self.state, attr) + 1)
                xpath_to_ref[record.xpath] = ref
            else:
                previous_xpath = (existing_by_ref.get(ref) or {}).get("xpath")
                if previous_xpath and previous_xpath != record.xpath:
                    rebound_refs.append(ref)
                xpath_to_ref[record.xpath] = ref
            used_refs.add(ref)
            assigned.append((record, ref))
            assigned_candidates.append(candidate)

        fp_gen = NodeFingerprint()
        payloads = []
        for (record, ref), candidate in zip(assigned, assigned_candidates):
            item = record.to_output(ref)
            item["xpath"] = record.xpath
            item["parent_ref"] = xpath_to_ref.get(record.parent_xpath) if record.parent_xpath else None
            item["parent_xpath"] = record.parent_xpath
            item["session_id"] = self.meta.session_id
            item["runtime_id"] = self.meta.runtime_id
            item["page_id"] = active_page.page_id
            item["snapshot_id"] = active_page.snapshot_id
            item["url"] = active_page.url
            item["fingerprint"] = fp_gen.compute(item)
            item["semantic_features"] = candidate["semantic_features"]
            item["semantic_fingerprint"] = candidate["semantic_fingerprint"]
            item["fingerprint_version"] = SemanticFingerprint.VERSION
            item["ref_rebound"] = ref in rebound_refs
            item["locator_candidates"] = self.locator_generator.generate(item)
            self.fingerprint_index.add(ref, item["fingerprint"])
            self.fingerprint_index.add(ref, item["semantic_fingerprint"])
            refs_by_type[record.ref_type][ref] = item
            payloads.append(item)

        if track_delta:
            previous = dict(self.state.last_snapshot_fingerprints or {})
            current = {
                item["ref"]: item["semantic_fingerprint"]
                for item in payloads
            }
            added = sorted(set(current) - set(previous))
            removed = sorted(set(previous) - set(current))
            changed = sorted(
                ref
                for ref in set(previous) & set(current)
                if previous[ref] != current[ref]
            )
            self.state.last_snapshot_diff = {
                "from_snapshot_id": self.state.active_page.snapshot_id,
                "added_refs": added,
                "removed_refs": removed,
                "changed_refs": changed,
                "rebound_refs": sorted(set(rebound_refs)),
                "unchanged_count": len(set(previous) & set(current)) - len(changed),
            }
            self.state.last_snapshot_fingerprints = current
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

    def find_by_fingerprint(self, fingerprint: str) -> str | None:
        return self.fingerprint_index.find(fingerprint)

    def __exit__(self, exc_type, exc, tb) -> None:
        self.persist()
        return None
