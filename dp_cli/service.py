from __future__ import annotations

import json
import re
from collections.abc import Callable
from contextlib import contextmanager

from dp_cli.adapter import DrissionPageAdapter
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

SURFACE_CONTAINER_ROLES = {
    "banner",
    "complementary",
    "contentinfo",
    "dialog",
    "form",
    "list",
    "main",
    "navigation",
    "region",
    "search",
    "table",
    "toolbar",
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
        view: str = "planner",
    ) -> dict:
        if view not in {"planner", "full"}:
            raise InvalidInputError("snapshot --view must be either 'planner' or 'full'.")

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
            planner_view = self._build_planner_view(nodes)

            payload = {
                "page": self._page_payload(runtime),
                "page_identity": self._page_identity_payload(runtime),
                "mode": "semantic",
                "scope": scope,
                "root_ref": root_ref,
                "depth": snapshot_depth,
            }
            artifact_file = self._write_snapshot_artifact(
                session=session,
                artifact=SnapshotArtifact(
                    page=payload["page"],
                    page_identity=payload["page_identity"],
                    mode="semantic",
                    scope=scope,
                    root_ref=root_ref,
                    depth=snapshot_depth,
                    nodes=nodes,
                    planner_view=planner_view,
                ),
                snapshot_id=runtime.state.active_page.snapshot_id or "snapshot",
            )
            runtime.remember_snapshot(artifact_file, view)
            runtime.persist()
            payload["artifact_file"] = artifact_file

            if view == "full":
                payload["count"] = len(nodes)
                payload["nodes"] = nodes
            else:
                payload["planner_view"] = planner_view
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
            elements = [node for node in nodes if node["ref_type"] == "element"]
            runtime.persist()
            return {
                "page": self._page_payload(runtime),
                "page_identity": self._page_identity_payload(runtime),
                "count": len(elements),
                "nodes": elements,
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
            runtime.sync_page_identity()
            runtime.persist()
            return include_payload(runtime, target_locator, state)

    def _write_snapshot_artifact(self, session: str, artifact: SnapshotArtifact, snapshot_id: str) -> str:
        snapshots_dir = self.sessions.store.base_dir / "snapshots" / session
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        path = snapshots_dir / f"{snapshot_id}.json"
        path.write_text(json.dumps(artifact.to_output(), ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def _build_planner_view(self, nodes: list[dict]) -> dict:
        lookup = {node["ref"]: node for node in nodes}
        children = self._children_map(nodes)

        pinned_controls: list[dict] = []
        pinned_refs: set[str] = set()
        for node in nodes:
            if node["ref_type"] != "element":
                continue
            if self._is_pinned_control(node, lookup, children):
                pinned_controls.append(self._node_summary(node))
                pinned_refs.add(node["ref"])

        condensed_groups: list[dict] = []
        condensed_member_refs: set[str] = set()
        candidate_groups: list[tuple[dict, list[dict]]] = []
        for node in nodes:
            if node["ref_type"] != "container":
                continue
            descendants = self._descendant_elements(node["ref"], children)
            if not self._is_condensable_group(node, lookup, children, descendants):
                continue
            if not descendants:
                continue
            candidate_groups.append((node, descendants))

        if not candidate_groups:
            fallback_groups = []
            for node in nodes:
                if node["ref_type"] != "container":
                    continue
                descendants = self._descendant_elements(node["ref"], children)
                if len(descendants) < 6:
                    continue
                if node["role"] in {"banner", "complementary", "contentinfo", "dialog", "form", "navigation", "search"}:
                    continue
                if sum(1 for item in descendants if self._is_pinned_control(item, lookup, children)) >= 3:
                    continue
                fallback_groups.append((node, descendants))
            fallback_groups.sort(key=lambda item: len(item[1]), reverse=True)
            candidate_groups = fallback_groups[:1]

        for node, descendants in candidate_groups:
            condensed_groups.append(self._group_summary(node, descendants))
            for descendant in descendants:
                if descendant["ref"] not in pinned_refs:
                    condensed_member_refs.add(descendant["ref"])

        viewport_nodes: list[dict] = []
        for node in nodes:
            if node["ref"] in pinned_refs or node["ref"] in condensed_member_refs:
                continue
            if not node["visibility"]["in_viewport"]:
                continue
            if node["ref_type"] == "container" and not self._should_surface_container(node):
                continue
            viewport_nodes.append(self._node_summary(node))

        surfaced_refs = (
            {item["ref"] for item in pinned_controls}
            | {item["ref"] for item in viewport_nodes}
            | {item["ref"] for item in condensed_groups}
        )
        omitted_nodes = [node for node in nodes if node["ref"] not in surfaced_refs]
        return {
            "pinned_controls": pinned_controls,
            "viewport_nodes": viewport_nodes,
            "condensed_groups": condensed_groups,
            "stats": {
                "total_nodes": len(nodes),
                "total_elements": sum(1 for node in nodes if node["ref_type"] == "element"),
                "total_containers": sum(1 for node in nodes if node["ref_type"] == "container"),
                "pinned_control_count": len(pinned_controls),
                "viewport_node_count": len(viewport_nodes),
                "condensed_group_count": len(condensed_groups),
            },
            "omitted_summary": {
                "omitted_node_count": len(omitted_nodes),
                "omitted_element_count": sum(1 for node in omitted_nodes if node["ref_type"] == "element"),
                "omitted_container_count": sum(1 for node in omitted_nodes if node["ref_type"] == "container"),
            },
        }

    def _filter_text_matches(self, nodes: list[dict], query: str) -> list[dict]:
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
            score = 0
            exact_name = self._normalized(node.get("name") or "")
            exact_text = self._normalized(node.get("text") or "")
            label_text = self._normalized(node.get("label") or "")
            if exact_name == normalized_query:
                score += 40
            if exact_text == normalized_query:
                score += 35
            if label_text == normalized_query:
                score += 25
            if normalized_query in exact_name:
                score += 15
            if normalized_query in exact_text:
                score += 12
            if normalized_query in label_text:
                score += 10
            if self._is_pinned_control(node, lookup, children):
                score += 20
            if node["visibility"]["in_viewport"]:
                score += 5
            if node["visibility"]["interactable_now"]:
                score += 5
            matches.append((score, node))
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

    def _node_summary(self, node: dict) -> dict:
        return {
            "ref": node["ref"],
            "ref_type": node["ref_type"],
            "role": node["role"],
            "name": node["name"],
            "text": node["text"],
            "id": node["id"],
            "depth": node["depth"],
            "visibility": node["visibility"],
            "context": node["context"],
            "states": node["states"],
        }

    def _should_surface_container(self, node: dict) -> bool:
        return node["role"] in SURFACE_CONTAINER_ROLES

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

    def _group_summary(self, node: dict, descendants: list[dict]) -> dict:
        return {
            "ref": node["ref"],
            "ref_type": node["ref_type"],
            "role": node["role"],
            "name": node["name"],
            "text": node["text"],
            "depth": node["depth"],
            "visibility": node["visibility"],
            "context": node["context"],
            "child_count": len(descendants),
            "sample_labels": self._sample_labels(descendants),
        }

    def _is_condensable_group(
        self,
        node: dict,
        lookup: dict[str, dict],
        children: dict[str, list[dict]],
        descendants: list[dict] | None = None,
    ) -> bool:
        if node["role"] in {"banner", "complementary", "contentinfo", "dialog", "form", "navigation", "search"}:
            return False
        bounds = node.get("bounds", {})
        if bounds.get("x", 9999) <= 200 and bounds.get("width", 9999) <= 320 and node.get("depth", 0) <= 8:
            return False
        descendants = descendants or self._descendant_elements(node["ref"], children)
        if len(descendants) < 6:
            return False
        if sum(1 for item in descendants if item.get("role") in {"link", "button"}) < 6:
            return False
        if sum(1 for item in descendants if self._is_pinned_control(item, lookup, children)) >= 3:
            return False
        return len(self._sample_labels(descendants)) >= 3

    def _sample_labels(self, nodes: list[dict], limit: int = 3) -> list[str]:
        labels: list[str] = []
        for node in nodes:
            label = (node.get("name") or node.get("text") or "").strip()
            if not label or label in labels:
                continue
            labels.append(label)
            if len(labels) >= limit:
                break
        return labels

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
        return re.sub(r"\s+", "", (value or "").strip().lower())
