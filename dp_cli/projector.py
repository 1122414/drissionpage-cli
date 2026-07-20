from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from dp_cli.models import RecoveryInfo
from dp_cli.record_projection import StructuredRecordProjector


@dataclass
class AgentSummary:
    global_actions: list[dict] = field(default_factory=list)
    visible_focus: list[dict] = field(default_factory=list)
    repeated_regions: list[dict] = field(default_factory=list)


class SummaryProjector:
    def project(self, nodes: list[dict], groups: list[dict], recovery: RecoveryInfo) -> AgentSummary:
        global_actions = self._select_global_actions(nodes)
        visible_focus = self._select_visible_focus(nodes, groups)
        repeated_regions = self._build_repeated_regions(groups)
        return AgentSummary(
            global_actions=global_actions,
            visible_focus=visible_focus,
            repeated_regions=repeated_regions,
        )

    def _select_global_actions(self, nodes: list[dict]) -> list[dict]:
        actions = []
        for node in nodes:
            if node["ref_type"] != "element":
                continue
            if not node["visibility"]["interactable_now"]:
                continue
            if node["role"] in {"textbox", "searchbox", "button", "link", "combobox"}:
                actions.append({
                    "ref": node["ref"],
                    "role": node["role"],
                    "name": node["name"],
                })
        return actions[:12]

    def _select_visible_focus(self, nodes: list[dict], groups: list) -> list[dict]:
        focus = []
        for group in groups:
            ref = getattr(group, "representative_ref", "") or getattr(group, "ref", "")
            name = getattr(group, "name", "") or ""
            count = getattr(group, "count", 0) or 0
            focus.append({
                "ref": ref,
                "kind": "group",
                "name": name,
                "item_count": count,
            })
        visible_regions = [n for n in nodes if n.get("kind") == "region" and n["visibility"]["in_viewport"]]
        for region in visible_regions[:6]:
            focus.append({
                "ref": region["ref"],
                "kind": "region",
                "name": region.get("name", ""),
                "role": region.get("role", ""),
            })
        return focus[:6]

    def _build_repeated_regions(self, groups: list) -> list[dict]:
        regions = []
        for group in groups:
            ref = getattr(group, "representative_ref", "") or getattr(group, "ref", "")
            name = getattr(group, "name", "") or ""
            regions.append({
                "group_ref": ref,
                "group_kind": getattr(group, "group_kind", "list") or "list",
                "name": name,
                "sample_item_names": [],
                "entry_action_refs": [],
                "next_page_ref": None,
            })
        return regions


class ExtractProjector:
    def project(self, group: dict, nodes: list[dict], schema: list[str] | None = None) -> dict:
        item_refs = group.get("item_refs", [])
        lookup = {n["ref"]: n for n in nodes}
        children_by_parent: dict[str, list[dict]] = {}
        for node in nodes:
            parent_ref = node.get("parent_ref")
            if parent_ref:
                children_by_parent.setdefault(parent_ref, []).append(node)

        element_refs = [ref for ref in item_refs if lookup.get(ref, {}).get("ref_type") == "element"]
        container_refs = [ref for ref in item_refs if lookup.get(ref, {}).get("ref_type") == "container"]

        representative = lookup.get(
            group.get("group_ref") or group.get("representative_ref"),
            {},
        )
        root_xpath = str(
            group.get("root_xpath")
            or representative.get("xpath")
            or ""
        )
        structured_items = (
            StructuredRecordProjector(
                normalize_url=self._normalize_url,
            ).project(nodes, schema, root_xpath)
            if schema and root_xpath
            else []
        )
        structured_items = self._constrain_structured_items_to_seed_links(
            structured_items,
            element_refs,
            lookup,
        )

        if structured_items:
            items = structured_items
        elif container_refs:
            items = self._extract_from_containers(container_refs, lookup, children_by_parent, schema)
        elif element_refs:
            items = self._extract_from_elements(element_refs, lookup, children_by_parent, schema)
        else:
            items = []

        if schema:
            normalized_schema = [s.lower() for s in schema]
            filtered_items = []
            for item in items:
                filtered: dict[str, str] = {}
                for key, value in item.items():
                    if key.lower() in normalized_schema:
                        schema_key = next((s for s in schema if s.lower() == key.lower()), key)
                        filtered[schema_key] = value
                if filtered:
                    filtered_items.append(filtered)
            items = filtered_items

        if schema:
            url_fields = [s for s in schema if s.lower() in ("url", "href", "link")]
            if url_fields:
                items = [
                    item for item in items
                    if any(f.lower() in [k.lower() for k in item.keys()] for f in url_fields)
                ]

        detected_schema = []
        for item in items:
            if item:
                detected_schema = list(item.keys())
                break

        return {
            "group_ref": group.get("group_ref", group.get("representative_ref")),
            "item_count": len(items),
            "fields": schema if schema else detected_schema,
            "items": items,
        }

    def _constrain_structured_items_to_seed_links(
        self,
        items: list[dict],
        element_refs: list[str],
        lookup: dict[str, dict],
    ) -> list[dict]:
        seed_nodes = [lookup[ref] for ref in element_refs if ref in lookup]
        if len(seed_nodes) < 3 or any(
            node.get("role") != "link" or not node.get("href")
            for node in seed_nodes
        ):
            return items

        allowed_urls = {
            self._normalize_url(
                str(node.get("href") or ""),
                str(node.get("url") or ""),
            ).rstrip("/")
            for node in seed_nodes
        }
        allowed_urls.discard("")
        if not allowed_urls:
            return items

        return [
            item
            for item in items
            if any(
                str(item.get(field) or "").rstrip("/") in allowed_urls
                for field in ("url", "href", "link", "detail_url")
            )
        ]

    def _extract_from_containers(
        self,
        container_refs: list[str],
        lookup: dict[str, dict],
        children_by_parent: dict[str, list[dict]],
        schema: list[str] | None,
    ) -> list[dict[str, str]]:
        items = []
        for ref in container_refs:
            collected = []
            queue = [ref]
            visited = set()
            while queue:
                current_ref = queue.pop(0)
                if current_ref in visited:
                    continue
                visited.add(current_ref)
                node = lookup.get(current_ref)
                if node:
                    collected.append(node)
                    for child in children_by_parent.get(current_ref, []):
                        if child["ref"] not in visited:
                            queue.append(child["ref"])
            item = self._build_item(collected, schema)
            if item:
                items.append(item)
        return items

    def _extract_from_elements(
        self,
        element_refs: list[str],
        lookup: dict[str, dict],
        children_by_parent: dict[str, list[dict]],
        schema: list[str] | None,
    ) -> list[dict[str, str]]:
        link_items = self._extract_link_items(element_refs, lookup, schema)
        if link_items:
            return link_items

        parent_groups: dict[str, list[dict]] = {}
        for ref in element_refs:
            node = lookup.get(ref)
            if not node:
                continue
            parent_ref = node.get("parent_ref")
            if not parent_ref:
                item = self._build_item([node], schema)
                if item:
                    parent_groups.setdefault(f"__leaf_{ref}", []).append(node)
            else:
                parent_groups.setdefault(parent_ref, []).append(node)

        if len(parent_groups) == 1:
            all_nodes = list(parent_groups.values())[0]
            xpath_groups = self._group_by_xpath_row(all_nodes)
            if len(xpath_groups) > 1:
                parent_groups = xpath_groups

        items = []
        for _parent_ref, collected in parent_groups.items():
            item = self._build_item(collected, schema)
            if item:
                items.append(item)
        return items

    def _extract_link_items(
        self,
        element_refs: list[str],
        lookup: dict[str, dict],
        schema: list[str] | None,
    ) -> list[dict[str, str]]:
        links = []
        for ref in element_refs:
            node = lookup.get(ref)
            if not node or node.get("role") != "link" or not node.get("href"):
                continue
            text = node.get("text") or node.get("name") or ""
            if not text or self._is_navigation_or_filter_link(node):
                continue
            links.append(node)

        if len(links) < 1:
            return []

        items = []
        item_index_by_signature: dict[str, int] = {}
        for node in links:
            item = self._build_item([node], schema)
            if not item:
                continue
            item_url = next(
                (
                    str(item.get(key) or "").rstrip("/")
                    for key in ("url", "detail_url", "href", "link")
                    if item.get(key)
                ),
                "",
            )
            signature = (
                f"url:{item_url}"
                if item_url
                else "|".join(
                    str(item.get(key, ""))
                    for key in ("title", "name", "text")
                )
            )
            existing_index = item_index_by_signature.get(signature)
            if existing_index is not None:
                if self._item_quality(item) > self._item_quality(items[existing_index]):
                    items[existing_index] = item
                continue
            item_index_by_signature[signature] = len(items)
            items.append(item)
        return items

    @staticmethod
    def _item_quality(item: dict[str, str]) -> int:
        title = str(
            item.get("title")
            or item.get("name")
            or item.get("text")
            or ""
        ).strip()
        normalized = title.lower()
        generic_titles = {
            "link",
            "image",
            "cover",
            "cover image",
            "details",
            "detail",
            "read more",
            "more",
        }
        score = min(len(title), 200)
        if normalized in generic_titles:
            score -= 500
        if title.startswith(("http://", "https://")):
            score -= 200
        return score

    def _is_item_detail_link(self, node: dict) -> bool:
        href = (node.get("href") or "").lower()
        return any(token in href for token in ("detail", "/vod/", "/movie/", "/video/", "/item/"))

    def _is_navigation_or_filter_link(self, node: dict) -> bool:
        href = (node.get("href") or "").lower()
        text = (node.get("text") or node.get("name") or "").strip().lower()
        parsed = urlparse(href)
        if href in {"", "#", "/"} or parsed.scheme not in {"", "http", "https"}:
            return True
        noise_patterns = (
            "vod-show",
            "vod-type",
            "year-",
            "area-",
            "by-",
            "class-",
            "page-",
            "search",
            "label",
            "topic",
            "/author/",
            "/authors/",
            "/writer/",
            "/user/",
            "/profile/",
            "/category/",
            "/categories/",
            "/genre/",
            "/rank",
            "/ranking",
            "/help",
            "/login",
            "/logout",
            "/register",
        )
        if any(pattern in href for pattern in noise_patterns):
            return True
        if text in {"首页", "上一页", "下一页", "尾页", "全部", "home", "next", "previous"}:
            return True
        if re.fullmatch(r"\d+", text):
            return True
        return False

    def _group_by_xpath_row(self, nodes: list[dict]) -> dict[str, list[dict]]:
        groups: dict[str, list[dict]] = {}
        for node in nodes:
            xpath = node.get("xpath", "")
            parts = xpath.split("/")
            indexed_indices = [i for i, part in enumerate(parts) if re.search(r"\[\d+\]", part)]

            if len(indexed_indices) >= 2:
                row_idx = indexed_indices[-2]
            elif len(indexed_indices) == 1:
                row_idx = indexed_indices[0]
            else:
                groups.setdefault(xpath, []).append(node)
                continue

            key = "/".join(parts[: row_idx + 1])
            groups.setdefault(key, []).append(node)

        return groups

    def _build_item(self, nodes: list[dict], schema: list[str] | None) -> dict[str, str] | None:
        item: dict[str, str] = {}

        url_field = "url"
        title_field = "title"
        if schema:
            for field in schema:
                if field.lower() in ("url", "href", "link"):
                    url_field = field
                if field.lower() in ("title", "name", "text"):
                    title_field = field

        def node_priority(node: dict) -> tuple:
            href = node.get("href", "")
            is_external = href.startswith("http://") or href.startswith("https://")
            is_noise = any(
                pattern in href
                for pattern in ["vote?", "from?site=", "goto=news", "hide?", "flag?"]
            )
            text_len = len(node.get("text", ""))
            return (
                0 if is_external and not is_noise else (2 if is_noise else 1),
                -text_len,
            )

        sorted_nodes = sorted(nodes, key=node_priority)

        source_node = None
        for node in sorted_nodes:
            href = node.get("href", "")
            normalized_url = self._normalize_url(href, node.get("url", "")) if href else ""
            if normalized_url:
                item[url_field] = normalized_url
                source_node = node
                break

        if source_node:
            item.setdefault("detail_url", item.get(url_field, ""))
            item.setdefault("href", source_node.get("href", ""))
            item.setdefault("item_ref", source_node.get("ref", ""))
            item.setdefault("source_page_url", source_node.get("url", ""))
            item.setdefault("text", source_node.get("text") or source_node.get("name", ""))

        title_candidates = []
        for node in sorted_nodes:
            text = node.get("text") or node.get("name", "")
            if text and len(text) > 3:
                title_candidates.append(text)

        if title_candidates:
            item[title_field] = max(title_candidates, key=len)

        for node in sorted_nodes:
            text = node.get("text") or node.get("name", "")
            role = node.get("role", "")

            if "author" in (schema or []) and not item.get("author"):
                if role == "link" and text and len(text) <= 30 and " " not in text:
                    item["author"] = text

            if "points" in (schema or []) and not item.get("points"):
                if text and ("point" in text.lower() or re.match(r"\d+\s*points?", text, re.IGNORECASE)):
                    item["points"] = text

        has_url = bool(item.get(url_field))
        has_title = bool(item.get(title_field)) and len(item[title_field]) > 5

        if not has_url and not has_title:
            return None

        if item.get(url_field) and not item.get(title_field):
            item[title_field] = item[url_field]

        return item

    def _normalize_url(self, href: str, base_url: str = "") -> str:
        value = urljoin(base_url or "", href)
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        return value


class TokenBudgetEnforcer:
    def __init__(self, max_tokens: int = 1500):
        self.max_tokens = max_tokens

    def enforce(self, summary: AgentSummary) -> tuple[AgentSummary, RecoveryInfo]:
        current_tokens = self._estimate_tokens(summary)
        if current_tokens <= self.max_tokens:
            return summary, RecoveryInfo()
        for region in summary.repeated_regions:
            while self._estimate_tokens(summary) > self.max_tokens and len(region.get("sample_item_names", [])) > 1:
                region["sample_item_names"].pop()
        while self._estimate_tokens(summary) > self.max_tokens and len(summary.visible_focus) > 1:
            summary.visible_focus.pop()
        critical_roles = {"textbox", "searchbox", "button", "link"}
        while self._estimate_tokens(summary) > self.max_tokens and len(summary.global_actions) > 1:
            removed = False
            for i, action in enumerate(summary.global_actions):
                if action.get("role") not in critical_roles:
                    summary.global_actions.pop(i)
                    removed = True
                    break
            if not removed:
                summary.global_actions.pop()
        recovery = RecoveryInfo(
            truncated=True,
            truncation_reason="token_budget_exceeded",
            truncation_threshold=self.max_tokens,
            expand_candidates=[r.get("ref") for r in summary.visible_focus],
        )
        return summary, recovery

    def _estimate_tokens(self, summary: AgentSummary) -> int:
        json_str = json.dumps({
            "global_actions": summary.global_actions,
            "visible_focus": summary.visible_focus,
            "repeated_regions": summary.repeated_regions,
        }, ensure_ascii=False)
        return len(json_str) // 4


class RecoveryProjector:
    def project(self, nodes: list[dict], truncated: bool = False) -> RecoveryInfo:
        offscreen = [n for n in nodes if n["ref_type"] == "element" and not n["visibility"]["in_viewport"]]
        return RecoveryInfo(
            offscreen_actionable_count=len(offscreen),
            truncated=truncated,
        )
