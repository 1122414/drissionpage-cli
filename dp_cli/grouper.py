from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GroupSchema:
    group_ref: str
    group_kind: str
    name: str
    item_refs: list[str] = field(default_factory=list)
    sample_fields: list[str] = field(default_factory=list)
    entry_action_refs: list[str] = field(default_factory=list)
    next_page_ref: str | None = None


class GroupKindDetector:
    def detect(self, compressed_group: dict, nodes: list[dict]) -> str:
        role = compressed_group.get("role", "")
        tag = compressed_group.get("tag", "")
        if role in {"list", "listitem"} or tag in {"ul", "ol"}:
            return "list"
        if role in {"table", "row", "rowgroup"} or tag in {"table", "tbody"}:
            return "table"
        if role in {"grid", "card"}:
            return "grid"
        if role in {"tree", "treeitem"}:
            return "tree"
        return "list"


class FieldSchemaExtractor:
    def extract(self, item_refs: list[str], nodes: list[dict]) -> dict[str, dict]:
        fields: dict[str, dict] = {}
        for ref in item_refs[:3]:
            item_nodes = [n for n in nodes if n.get("item_ref") == ref or n["ref"] == ref]
            for node in item_nodes:
                if node["ref_type"] != "element":
                    continue
                key = self._infer_field_name(node)
                if key and key not in fields:
                    fields[key] = {
                        "selector": f"[{node['tag']}][role='{node['role']}']",
                        "type": "text" if node["role"] != "link" else "href",
                    }
        return fields

    def _infer_field_name(self, node: dict) -> str:
        role = node.get("role", "")
        text = node.get("text", "")
        name = node.get("name", "")
        if role == "link" and node.get("href"):
            return "detail_link"
        if "price" in text.lower() or "price" in name.lower():
            return "price"
        if "title" in name.lower() or len(text) > 20:
            return "title"
        if role == "textbox" or role == "text":
            return "text"
        return role


class PaginationDetector:
    def detect(self, nodes: list[dict]) -> str | None:
        pagination_keywords = {"next", "more", "load more", "下一页"}
        for node in nodes:
            if node["ref_type"] != "element":
                continue
            text = (node.get("text") or node.get("name") or "").lower()
            if any(kw in text for kw in pagination_keywords):
                return node["ref"]
        return None
