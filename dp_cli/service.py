from __future__ import annotations

import json
import re
from collections.abc import Callable
from contextlib import contextmanager
from urllib.parse import urljoin

from dp_cli.adapter import DrissionPageAdapter
from dp_cli.compressor import CompressedGroup, CompressionConfig, DOMCompressor
from dp_cli.errors import (
    ElementNotFoundError,
    ElementNotInteractableError,
    InvalidInputError,
    InvalidRefTypeError,
    RefNotFoundError,
    RefStaleError,
)
from dp_cli.models import DEFAULT_SESSION, SNAPSHOT_DEFAULT_DEPTH, SnapshotArtifact
from dp_cli.session import SessionManager

PAGINATION_KEYWORDS = {
    "first",
    "last",
    "next",
    "prev",
    "previous",
    "nextpage",
    "prevpage",
    "previouspage",
    "firstpage",
    "lastpage",
    "首页",
    "上一页",
    "下一页",
    "尾页",
}

PRIMARY_ACTION_KEYWORDS = {
    "search",
    "submit",
    "save",
    "confirm",
    "login",
    "搜索",
    "提交",
    "保存",
    "确认",
    "登录",
}



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

    def snapshot_page(
        self,
        session: str = DEFAULT_SESSION,
        ref: str | None = None,
        depth: int | None = None,
        headless: bool | None = None,
        view: str | None = None,
        mode: str = "agent_summary",
    ) -> dict:
        if view is not None:
            mode = "full" if view == "full" else "agent_summary"
        if mode not in {"full", "agent_summary", "extract"}:
            raise InvalidInputError("snapshot --mode must be one of: full, agent_summary, extract.")

        snapshot_depth = depth if depth is not None else (SNAPSHOT_DEFAULT_DEPTH if ref else None)
        scope = "subtree" if ref else "page"

        with self._with_runtime(session=session, headless=headless) as runtime:
            runtime.begin_snapshot()
            root_ref = ref
            root_xpath = None
            if ref is not None:
                item = self._ref_item(runtime, ref)
                root_xpath = item["xpath"]

            records = self.adapter.snapshot_nodes(runtime.tab, root_xpath=root_xpath, depth=snapshot_depth)
            nodes = runtime.upsert_nodes(records)
            index = self._build_index(nodes)

            payload = {
                "schema_version": "0.6",
                "mode": mode,
                "page": self._page_payload(runtime),
                "page_identity": self._page_identity_payload(runtime),
                "scope": scope,
                "root_ref": root_ref,
                "depth": snapshot_depth,
                "index": index,
            }
            artifact_file = self._write_snapshot_artifact(
                session=session,
                artifact=SnapshotArtifact(
                    page=payload["page"],
                    page_identity=payload["page_identity"],
                    mode=mode,
                    scope=scope,
                    root_ref=root_ref,
                    depth=snapshot_depth,
                    nodes=nodes,
                    planner_view=index,
                    schema_version="0.6",
                ),
                snapshot_id=runtime.state.active_page.snapshot_id or "snapshot",
            )
            runtime.remember_snapshot(artifact_file, mode)
            runtime.persist()
            payload["artifact_file"] = artifact_file

            if mode == "full":
                payload["count"] = len(nodes)
                payload["nodes"] = nodes
                payload["index"] = index
            elif mode == "agent_summary":
                payload["index"] = index
            else:
                payload["index"] = index
            return payload

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
                nodes = runtime.upsert_nodes(records)
            else:
                nodes = runtime.upsert_nodes(self.adapter.snapshot_nodes(runtime.tab, depth=None))
                nodes = self._filter_text_matches(nodes, text or "")
            runtime.persist()
            return {
                "page": self._page_payload(runtime),
                "page_identity": self._page_identity_payload(runtime),
                "count": len(nodes),
                "nodes": nodes,
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
            include_payload=lambda runtime, target_locator, state: {
                "page": self._page_payload(runtime),
                "target": self._target_payload(ref, target_locator),
                "target_state": state,
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
            include_payload=lambda runtime, target_locator, state: {
                "page": self._page_payload(runtime),
                "target": self._target_payload(ref, target_locator),
                "target_state": state,
                "typed_text": text,
            },
        )

    def expand_container(
        self,
        session: str = DEFAULT_SESSION,
        ref: str | None = None,
        depth: int = 2,
        headless: bool | None = None,
    ) -> dict:
        with self._with_runtime(session=session, headless=headless) as runtime:
            runtime.begin_snapshot()
            records = self.adapter.snapshot_nodes(runtime.tab, root_xpath=self._ref_item(runtime, ref)["xpath"], depth=depth)
            nodes = runtime.upsert_nodes(records)
            runtime.persist()
            return {
                "page": self._page_payload(runtime),
                "page_identity": self._page_identity_payload(runtime),
                "target_ref": ref,
                "mode": "full",
                "count": len(nodes),
                "nodes": nodes,
            }

    def list_items(
        self,
        session: str = DEFAULT_SESSION,
        group_ref: str | None = None,
        sample_size: int = 3,
        headless: bool | None = None,
    ) -> dict:
        with self._with_runtime(session=session, headless=headless) as runtime:
            runtime.begin_snapshot()
            group_item = self._ref_item(runtime, group_ref)
            records = self.adapter.snapshot_nodes(runtime.tab, root_xpath=group_item["xpath"], depth=2)
            nodes = runtime.upsert_nodes(records)
            compressed_groups = self._compress_nodes(nodes)
            if compressed_groups:
                cg = compressed_groups[0]
                group = {
                    "representative_ref": cg.representative_ref,
                    "count": cg.count,
                    "member_refs": cg.member_refs,
                    "role": cg.role,
                    "tag": cg.tag,
                }
            else:
                group = {"representative_ref": group_ref, "count": len(nodes), "member_refs": [n["ref"] for n in nodes[:sample_size]]}
            from dp_cli.grouper import FieldSchemaExtractor, GroupKindDetector
            kind = GroupKindDetector().detect(group, nodes)
            fields = FieldSchemaExtractor().extract(group.get("member_refs", []), nodes)
            runtime.persist()
            return {
                "page": self._page_payload(runtime),
                "group_ref": group_ref,
                "group_kind": kind,
                "item_count": group.get("count", 0),
                "sample_items": [{"item_ref": ref, "fields": {}} for ref in group.get("member_refs", [])[:sample_size]],
                "schema_hints": fields,
            }

    def extract_group(
        self,
        session: str = DEFAULT_SESSION,
        target_ref: str | None = None,
        schema: list[str] | None = None,
        limit: int | None = None,
        headless: bool | None = None,
    ) -> dict:
        with self._with_runtime(session=session, headless=headless) as runtime:
            runtime.begin_snapshot()
            target_item = self._ref_item(runtime, target_ref)
            records = self.adapter.snapshot_nodes(runtime.tab, root_xpath=target_item["xpath"], depth=6)
            nodes = runtime.upsert_nodes(records)
            from dp_cli.projector import ExtractProjector
            projector = ExtractProjector()
            compressed_groups = self._compress_nodes(nodes)
            if compressed_groups:
                cg = compressed_groups[0]
                all_element_refs = [n["ref"] for n in nodes if n["ref_type"] == "element"]
                group = {
                    "representative_ref": cg.representative_ref,
                    "item_refs": all_element_refs,
                    "count": cg.count,
                }
            else:
                group = {
                    "representative_ref": target_ref,
                    "item_refs": [n["ref"] for n in nodes if n["ref_type"] == "element"],
                }
            result = projector.project(group, nodes, schema)
            if limit is not None and limit > 0:
                result["items"] = result["items"][:limit]
                result["item_count"] = len(result["items"])
            runtime.persist()
            return result

    def resolve_locator(
        self,
        session: str = DEFAULT_SESSION,
        ref: str | None = None,
        headless: bool | None = None,
    ) -> dict:
        with self._with_runtime(session=session, headless=headless) as runtime:
            item = self._ref_item(runtime, ref)
            return {
                "ref": ref,
                "fingerprint": item.get("fingerprint", ""),
                "confidence": 0.9 if item.get("fingerprint") else 0.0,
                "locator_candidates": item.get("locator_candidates", []),
                "re_resolve_result": "matched",
            }

    def eval_js(
        self,
        session: str = DEFAULT_SESSION,
        js: str = "",
        headless: bool | None = None,
    ) -> dict:
        with self._with_runtime(session=session, headless=headless) as runtime:
            result = runtime.tab.run_js(js, as_expr=True)
            return {"result": result}

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
                "ref_count": runtime.total_ref_count(),
                "container_ref_count": len(runtime.state.container_refs),
                "element_ref_count": len(runtime.state.element_refs),
                "last_snapshot_file": runtime.state.last_snapshot_file,
                "last_snapshot_mode": runtime.state.last_snapshot_mode,
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

    def _ref_item(self, runtime, ref: str) -> dict:
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
        return item

    def _resolve_target(self, runtime, ref: str | None, locator: str | None) -> str:
        if ref:
            item = self._ref_item(runtime, ref)
            if item.get("ref_type") != "element":
                raise InvalidRefTypeError(ref, expected="element", actual=item.get("ref_type", "unknown"))
            return item["locator"]
        if locator:
            return locator
        raise InvalidInputError("Command requires either --ref or --locator.")

    def _ensure_element_interactable(self, element, locator: str) -> dict:
        state = self.adapter.element_state(element)
        if state.get("interactable_now"):
            return state
        self.adapter.scroll_into_view(element)
        state = self.adapter.element_state(element)
        if state.get("interactable_now"):
            return state
        raise ElementNotInteractableError(
            "Element exists but is not interactable right now.",
            {
                "locator": locator,
                "visible": state.get("visible"),
                "in_viewport": state.get("in_viewport"),
                "enabled": state.get("enabled"),
                "interactable_now": state.get("interactable_now"),
            },
        )

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
            state = self._ensure_element_interactable(element, target_locator)
            action(element, text)
            try:
                state = self.adapter.element_state(element)
            except Exception:
                state = {**state, "state_after_action_unavailable": True}
            runtime.sync_page_identity()
            runtime.persist()
            return include_payload(runtime, target_locator, state)

    def _write_snapshot_artifact(self, session: str, artifact: SnapshotArtifact, snapshot_id: str) -> str:
        snapshots_dir = self.sessions.store.base_dir / "snapshots" / session
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        path = snapshots_dir / f"{snapshot_id}.json"
        path.write_text(json.dumps(artifact.to_output(), ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def _compress_nodes(self, nodes: list[dict]) -> list[CompressedGroup]:
        compressor = DOMCompressor(CompressionConfig(min_group_size=3))
        return compressor.compress(nodes)

    def _filter_text_matches(self, nodes: list[dict], query: str) -> list[dict]:
        from dp_cli.models import score_text_match
        lookup = {node["ref"]: node for node in nodes}
        children = self._children_map(nodes)
        normalized_query = self._normalized(query)
        matches: list[tuple[int, dict]] = []
        for node in nodes:
            if node["ref_type"] != "element":
                continue
            haystack = self._searchable_text(node)
            if normalized_query not in haystack:
                continue
            # Work on a copy to avoid mutating persisted state
            node_copy = dict(node)
            node_copy["_pinned"] = self._is_pinned_control(node, lookup, children)
            score = score_text_match(
                node_copy,
                normalized_query,
                pinned_bias=20,
                viewport_bias=5,
                interactable_bias=5,
                native_tag_bias=10,
            )
            # Mark exact text matches so callers can distinguish precise targets from container parents
            node_copy["exact_match"] = (
                self._normalized(node.get("text", "")) == normalized_query
                or self._normalized(node.get("name", "")) == normalized_query
            )
            matches.append((score, node_copy))
        matches.sort(key=lambda item: item[0], reverse=True)
        return [node for _, node in matches]

    def _children_map(self, nodes: list[dict]) -> dict[str, list[dict]]:
        mapping: dict[str, list[dict]] = {}
        for node in nodes:
            parent_ref = node.get("parent_ref")
            if not parent_ref:
                continue
            mapping.setdefault(parent_ref, []).append(node)
        return mapping

    def _descendant_elements(self, ref: str, children: dict[str, list[dict]]) -> list[dict]:
        queue = list(children.get(ref, []))
        descendants: list[dict] = []
        while queue:
            node = queue.pop(0)
            if node["ref_type"] == "element":
                descendants.append(node)
            queue.extend(children.get(node["ref"], []))
        return descendants

    def _is_pinned_control(self, node: dict, lookup: dict[str, dict], children: dict[str, list[dict]]) -> bool:
        if node["ref_type"] != "element":
            return False
        if self._is_pagination_control(node, lookup, children):
            return True
        if self._is_form_primary_action(node, lookup):
            return True
        if self._is_navigation_control(node, lookup):
            return True
        if any(node["states"].get(flag) for flag in ("selected", "expanded")):
            return True
        return False

    def _is_pagination_control(self, node: dict, lookup: dict[str, dict], children: dict[str, list[dict]]) -> bool:
        text = self._normalized(" ".join(part for part in (node.get("name"), node.get("text")) if part))
        if not text:
            return False
        if any(keyword in text for keyword in PAGINATION_KEYWORDS):
            return True
        if re.fullmatch(r"\d+", text):
            parent = lookup.get(node.get("parent_ref") or "")
            if not parent:
                return False
            siblings = [item for item in children.get(parent["ref"], []) if item["ref_type"] == "element"]
            sibling_texts = [
                self._normalized(" ".join(part for part in (item.get("name"), item.get("text")) if part))
                for item in siblings
            ]
            if sum(1 for value in sibling_texts if re.fullmatch(r"\d+", value or "")) >= 2:
                return True
            if any(any(keyword in value for keyword in PAGINATION_KEYWORDS) for value in sibling_texts):
                return True
        return False

    def _is_form_primary_action(self, node: dict, lookup: dict[str, dict]) -> bool:
        if node.get("role") not in {"button", "link"}:
            return False
        if not self._has_ancestor_role(node, lookup, {"form", "search", "dialog"}):
            return False
        text = self._normalized(" ".join(part for part in (node.get("name"), node.get("text")) if part))
        return any(keyword in text for keyword in PRIMARY_ACTION_KEYWORDS)

    def _is_navigation_control(self, node: dict, lookup: dict[str, dict]) -> bool:
        if node.get("role") not in {"button", "link", "tab"}:
            return False
        if self._has_ancestor_role(node, lookup, {"navigation"}):
            return True
        text = (node.get("name") or node.get("text") or "").strip()
        bounds = node.get("bounds") or {}
        if not text:
            return False
        if node.get("depth", 0) <= 8 and len(text) <= 32:
            if bounds.get("y", 9999) <= 180:
                return True
            if bounds.get("x", 9999) <= 180 and bounds.get("width", 9999) <= 260:
                return True
        return False

    def _has_ancestor_role(self, node: dict, lookup: dict[str, dict], roles: set[str]) -> bool:
        current_ref = node.get("parent_ref")
        while current_ref:
            parent = lookup.get(current_ref)
            if not parent:
                return False
            if parent.get("role") in roles:
                return True
            current_ref = parent.get("parent_ref")
        return False

    def _searchable_text(self, node: dict) -> str:
        return self._normalized(
            " ".join(
                part
                for part in (
                    node.get("name") or "",
                    node.get("text") or "",
                    node.get("label") or "",
                    node.get("value") or "",
                    node.get("placeholder") or "",
                    node.get("href") or "",
                    node.get("id") or "",
                    node.get("title") or "",
                    node.get("aria_label") or "",
                    node.get("context", {}).get("heading") or "",
                    node.get("context", {}).get("landmark") or "",
                    node.get("context", {}).get("form") or "",
                    node.get("context", {}).get("list") or "",
                )
                if part
            )
        )

    def _normalized(self, value: str) -> str:
        text = re.sub(r"\s+", "", (value or "").strip().lower())
        # Normalize leading zeros in numbers so "第05集" matches "第5集"
        return re.sub(r"(?<!\d)0+(\d)", r"\1", text)

    def _build_index(self, nodes: list[dict]) -> dict:
        lookup = {node["ref"]: node for node in nodes}
        children = self._children_map(nodes)
        data_regions = self._detect_data_regions(nodes)
        data_region_lookup = {region["ref"]: region for region in data_regions}
        for node in nodes:
            region = data_region_lookup.get(node["ref"])
            if region:
                node["_data_region_item_count"] = region["item_count"]

        surface_nodes = []
        deep_nodes = []
        for node in nodes:
            if node.get("semantic_level") == "surface":
                surface_nodes.append(node)
            else:
                deep_nodes.append(node)

        surface_nodes = self._rank_index_nodes(surface_nodes, lookup)
        surface_index = [self._surface_node_summary(node, children) for node in surface_nodes]
        deep_index = [self._deep_node_summary(node) for node in deep_nodes]

        roots = [n["ref"] for n in nodes if not n.get("parent_ref")]
        parent_map = {n["ref"]: n.get("parent_ref") for n in nodes if n.get("parent_ref")}
        children_map = {}
        for parent_ref, child_nodes in children.items():
            children_map[parent_ref] = [c["ref"] for c in child_nodes]

        interactable_nodes = self._rank_index_nodes(
            [
                n
                for n in nodes
                if n["ref_type"] == "element" and n["visibility"]["interactable_now"]
                and not self._is_redundant_action_parent(n, children)
            ],
            lookup,
        )
        interactable = [self._interactable_node_summary(n) for n in interactable_nodes]

        stats = {
            "total_nodes": len(nodes),
            "surface_count": len(surface_index),
            "deep_count": len(deep_index),
            "in_viewport": sum(1 for n in nodes if n["visibility"]["in_viewport"]),
            "offscreen": sum(1 for n in nodes if not n["visibility"]["in_viewport"]),
            "interactable_now": len(interactable),
        }

        index = {
            "interactable_elements": [self._filter_empty_fields(item) for item in interactable],
            "data_regions": [self._filter_empty_fields(item) for item in data_regions],
            "surface_index": [self._filter_empty_fields(item) for item in surface_index],
            "deep_index": [self._filter_empty_fields(item) for item in deep_index],
            "tree": {
                "roots": roots,
                "parent_map": parent_map,
                "children_map": children_map,
            },
            "stats": stats,
        }
        return index

    def _detect_data_regions(self, nodes: list[dict]) -> list[dict]:
        containers = [node for node in nodes if node.get("ref_type") == "container" and node.get("xpath")]
        item_links = [node for node in nodes if self._is_extractable_item_link(node)]
        if not item_links:
            return []

        candidates = []
        for container in containers:
            xpath = container["xpath"]
            descendant_links = [
                link
                for link in item_links
                if link.get("xpath", "").startswith(xpath + "/")
            ]
            if len(descendant_links) < 3:
                continue
            samples = [
                {
                    "text": link.get("text") or link.get("name"),
                    "url": self._absolute_href(link),
                }
                for link in descendant_links[:3]
            ]
            candidates.append(
                {
                    "ref": container["ref"],
                    "ref_type": container["ref_type"],
                    "tag": container.get("tag"),
                    "role": container.get("role"),
                    "name": container.get("name"),
                    "item_count": len(descendant_links),
                    "sample_items": samples,
                    "_depth": container.get("depth", 0),
                    "_text_len": len(container.get("text") or ""),
                }
            )

        if not candidates:
            return []

        # Prefer the smallest deep container that still covers many detail links.
        candidates.sort(key=lambda item: (-item["_depth"], item["_text_len"], -item["item_count"]))
        selected = []
        seen_refs = set()
        for candidate in candidates:
            if candidate["ref"] in seen_refs:
                continue
            seen_refs.add(candidate["ref"])
            candidate = dict(candidate)
            candidate.pop("_depth", None)
            candidate.pop("_text_len", None)
            selected.append(candidate)
            if len(selected) >= 5:
                break
        return selected

    def _is_extractable_item_link(self, node: dict) -> bool:
        if node.get("ref_type") != "element" or node.get("role") != "link":
            return False
        href = (node.get("href") or "").lower()
        text = (node.get("text") or node.get("name") or "").strip()
        if not href or len(text) < 2:
            return False
        if any(token in href for token in ("detail", "/vod/", "/movie/", "/video/", "/item/")):
            return True
        if any(token in href for token in ("vod-show", "vod-type", "year-", "area-", "by-", "class-", "page-")):
            return False
        if text in {"首页", "上一页", "下一页", "尾页", "全部"} or re.fullmatch(r"\d+", text):
            return False
        return False

    def _absolute_href(self, node: dict) -> str:
        href = node.get("href") or ""
        return urljoin(node.get("url") or "", href)

    def _is_redundant_action_parent(self, node: dict, children: dict[str, list[dict]]) -> bool:
        if node.get("role") != "button" or node.get("tag") not in {"div", "span"}:
            return False
        text = self._normalized(" ".join(part for part in (node.get("name"), node.get("text")) if part))
        if not text:
            return False
        for descendant in self._descendant_elements(node["ref"], children):
            if descendant is node:
                continue
            if descendant.get("role") != "button":
                continue
            descendant_text = self._normalized(
                " ".join(part for part in (descendant.get("name"), descendant.get("text")) if part)
            )
            if descendant_text == text:
                return True
        return False

    def _rank_index_nodes(self, nodes: list[dict], lookup: dict[str, dict]) -> list[dict]:
        return [
            node
            for _, node in sorted(
                enumerate(nodes),
                key=lambda item: (-self._planner_node_priority(item[1], lookup), item[0]),
            )
        ]

    def _planner_node_priority(self, node: dict, lookup: dict[str, dict]) -> int:
        score = 0
        role = node.get("role") or ""
        tag = node.get("tag") or ""
        visibility = node.get("visibility") or {}
        if visibility.get("in_viewport"):
            score += 20
        if visibility.get("interactable_now"):
            score += 50
        if role == "dialog" or self._has_ancestor_role(node, lookup, {"dialog"}):
            score += 1000
        if self._has_ancestor_role(node, lookup, {"form", "search"}):
            score += 120
        if role in {"tab", "button", "link", "textbox", "checkbox", "radio", "switch", "combobox", "option"}:
            score += 180
        if role == "tab":
            score += 80
        if role == "checkbox":
            score += 160
        if tag in {"button", "a", "input", "textarea", "select"}:
            score += 40
        if role == "button" and tag == "span":
            score += 90
        if role == "button" and tag == "div":
            score -= 60
        if node.get("_data_region_item_count"):
            score += 700 + min(int(node.get("_data_region_item_count") or 0), 100) + int(node.get("depth") or 0) * 20
        text = self._normalized(" ".join(part for part in (node.get("name"), node.get("text"), node.get("label")) if part))
        if any(keyword in text for keyword in ("验证码", "获取验证码", "发送验证码", "同意", "协议", "隐私", "条款")):
            score += 120
        if text in {"注册", "提交", "确认"} and role == "button":
            score += 40
        if node.get("states", {}).get("selected") or node.get("states", {}).get("expanded"):
            score += 30
        if text:
            score += 20
        return score

    def _interactable_node_summary(self, node: dict) -> dict:
        summary = {
            "ref": node["ref"],
            "role": node["role"],
            "name": node["name"],
            "tag": node.get("tag"),
            "text": node.get("text"),
            "placeholder": node.get("placeholder"),
            "label": node.get("label"),
            "input_type": node.get("input_type"),
            "value": node.get("value"),
            "checked": node.get("states", {}).get("checked"),
            "selected": node.get("states", {}).get("selected"),
        }
        return self._filter_empty_fields(summary)

    def _surface_node_summary(self, node: dict, children: dict[str, list[dict]]) -> dict:
        return {
            "ref": node["ref"],
            "ref_type": node["ref_type"],
            "tag": node["tag"],
            "role": node["role"],
            "name": node["name"],
            "text": node["text"],
            "parent_ref": node.get("parent_ref"),
            "placeholder": node.get("placeholder"),
            "label": node.get("label"),
            "input_type": node.get("input_type"),
            "value": node.get("value"),
            "in_viewport": node["visibility"]["in_viewport"],
            "interactable_now": node["visibility"]["interactable_now"],
            "child_count": len(children.get(node["ref"], [])),
            "item_count": node.get("_data_region_item_count"),
        }

    def _deep_node_summary(self, node: dict) -> dict:
        return {
            "ref": node["ref"],
            "ref_type": node["ref_type"],
            "tag": node["tag"],
            "role": node["role"],
            "name": node["name"][:40] if node["name"] else "",
            "text": node["text"][:60] if node["text"] else "",
            "parent_ref": node.get("parent_ref"),
            "in_viewport": node["visibility"]["in_viewport"],
        }

    @staticmethod
    def _filter_empty_fields(obj: dict) -> dict:
        result = {}
        for key, value in obj.items():
            if value is None:
                continue
            if value == "":
                continue
            if value == []:
                continue
            if value == {}:
                continue
            if isinstance(value, dict):
                filtered = CliService._filter_empty_fields(value)
                if filtered:
                    result[key] = filtered
            elif isinstance(value, list):
                filtered_list = [
                    CliService._filter_empty_fields(item) if isinstance(item, dict) else item
                    for item in value
                    if item not in (None, "", [], {})
                ]
                if filtered_list:
                    result[key] = filtered_list
            else:
                result[key] = value
        return result
