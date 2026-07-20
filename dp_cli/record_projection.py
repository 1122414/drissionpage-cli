from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, Callable
from urllib.parse import urljoin, urlparse


_INDEXED_SEGMENT_RE = re.compile(r"^(?P<tag>[^\[]+)\[(?P<index>\d+)\]$")
_CELL_INDEX_RE = re.compile(r"\[(\d+)\]$")
_PRICE_RE = re.compile(
    r"^(?:[$£€¥]\s*)?-?\d[\d,]*(?:\.\d{1,2})?$"
)
_QUOTE_RE = re.compile(r"[“\"'‘](.+?)[”\"'’]")
_AUTHOR_RE = re.compile(
    r"\bby\s+(.+?)(?:\s+\(about\)|\s+Tags?:|$)",
    re.IGNORECASE,
)
_TAGS_RE = re.compile(r"\bTags?:\s*(.+)$", re.IGNORECASE)
_PAGINATION_TEXT = {
    "first",
    "last",
    "next",
    "previous",
    "prev",
    "首页",
    "上一页",
    "下一页",
    "尾页",
}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _node_text(node: dict[str, Any]) -> str:
    return _clean(
        node.get("text")
        or node.get("name")
        or node.get("alt")
        or node.get("value")
    )


def _is_below(xpath: str, prefix: str) -> bool:
    return xpath == prefix or xpath.startswith(prefix.rstrip("/") + "/")


def _is_header_group(nodes: list[dict[str, Any]]) -> bool:
    tags = {str(node.get("tag") or "").lower() for node in nodes}
    return "th" in tags and "td" not in tags


def _is_navigation_group(
    prefix: str,
    nodes: list[dict[str, Any]],
) -> bool:
    if any(token in prefix.lower() for token in ("/nav[", "/header[", "/footer[")):
        return True
    meaningful = [_node_text(node) for node in nodes if _node_text(node)]
    if not meaningful:
        return True
    links = [
        node
        for node in nodes
        if str(node.get("role") or "").lower() == "link"
    ]
    if links and len(links) == len(
        [node for node in nodes if node.get("ref_type") == "element"]
    ):
        normalized = {_clean(node.get("text") or node.get("name")).lower() for node in links}
        if normalized and all(
            text in _PAGINATION_TEXT or re.fullmatch(r"\d+", text)
            for text in normalized
        ):
            return True
    return False


def _record_candidates(
    nodes: list[dict[str, Any]],
    root_xpath: str,
) -> list[tuple[tuple[int, int, int], list[tuple[str, list[dict[str, Any]]]]]]:
    root = root_xpath.rstrip("/")
    candidates: dict[
        tuple[str, str, int],
        dict[int, str],
    ] = defaultdict(dict)
    scoped = [
        node
        for node in nodes
        if _is_below(str(node.get("xpath") or ""), root)
    ]

    for node in scoped:
        xpath = str(node.get("xpath") or "")
        if xpath == root:
            continue
        relative = xpath[len(root):].strip("/")
        if not relative:
            continue
        segments = relative.split("/")
        before = root
        for position, segment in enumerate(segments):
            match = _INDEXED_SEGMENT_RE.fullmatch(segment)
            if match:
                key = (before, match.group("tag").lower(), position)
                index = int(match.group("index"))
                candidates[key][index] = f"{before}/{segment}"
            before = f"{before}/{segment}"

    ranked = []
    for (_before, _tag, position), indexed_prefixes in candidates.items():
        if len(indexed_prefixes) < 3:
            continue
        groups = []
        coverage = 0
        for index, prefix in sorted(indexed_prefixes.items()):
            members = [
                node
                for node in scoped
                if _is_below(str(node.get("xpath") or ""), prefix)
            ]
            if not members or _is_navigation_group(prefix, members):
                continue
            groups.append((prefix, members))
            coverage += len(members)
        if len(groups) < 3:
            continue
        score = (len(groups), coverage, -position)
        ranked.append((score, groups))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def group_record_nodes(
    nodes: list[dict[str, Any]],
    root_xpath: str,
    *,
    include_table_header: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Return the strongest repeated record dimension below ``root_xpath``."""
    ranked = _record_candidates(nodes, root_xpath)
    if not ranked:
        return {}
    groups = ranked[0][1]
    return {
        prefix: members
        for prefix, members in groups
        if include_table_header or not _is_header_group(members)
    }


class StructuredRecordProjector:
    """Project repeated cards, quotes, products, and tables into schema rows."""

    _TABLE_ALIASES = {
        "team": {"team", "teamname", "name"},
        "year": {"year", "season"},
        "wins": {"wins", "win"},
        "losses": {"losses", "loss"},
    }

    def __init__(
        self,
        normalize_url: Callable[[str, str], str] | None = None,
    ) -> None:
        self._normalize_url = normalize_url or self._default_normalize_url

    def project(
        self,
        nodes: list[dict[str, Any]],
        schema: list[str],
        root_xpath: str,
    ) -> list[dict[str, Any]]:
        normalized_schema = [
            str(field).strip()
            for field in schema
            if str(field).strip()
        ]
        if not normalized_schema or not root_xpath:
            return []

        table_items = self._project_table(
            nodes,
            normalized_schema,
            root_xpath,
        )
        if table_items:
            return table_items

        groups = group_record_nodes(nodes, root_xpath)
        items = []
        seen = set()
        for members in groups.values():
            item = self._project_record(members, normalized_schema)
            if not item or not self._has_required_coverage(
                item,
                normalized_schema,
            ):
                continue
            signature = self._item_signature(item)
            if signature in seen:
                continue
            seen.add(signature)
            items.append(item)
        return items

    def _project_table(
        self,
        nodes: list[dict[str, Any]],
        schema: list[str],
        root_xpath: str,
    ) -> list[dict[str, Any]]:
        if not any(
            str(node.get("tag") or "").lower() in {"table", "tr", "td", "th"}
            for node in nodes
        ):
            return []
        groups = group_record_nodes(
            nodes,
            root_xpath,
            include_table_header=True,
        )
        if not groups:
            return []

        header_values: list[str] = []
        data_rows: list[list[str]] = []
        for members in groups.values():
            header_cells = self._sorted_cells(members, "th")
            data_cells = self._sorted_cells(members, "td")
            if header_cells and not data_cells:
                header_values = [_node_text(node) for node in header_cells]
            elif data_cells:
                data_rows.append([_node_text(node) for node in data_cells])
        if not data_rows:
            return []

        header_lookup = {
            self._normalize_header(value): index
            for index, value in enumerate(header_values)
            if value
        }
        projected = []
        for values in data_rows:
            item: dict[str, Any] = {}
            for fallback_index, field in enumerate(schema):
                index = self._table_field_index(
                    field,
                    header_lookup,
                    fallback_index,
                )
                if 0 <= index < len(values) and _clean(values[index]):
                    item[field] = _clean(values[index])
            if self._has_required_coverage(item, schema):
                projected.append(item)
        return projected

    def _project_record(
        self,
        nodes: list[dict[str, Any]],
        schema: list[str],
    ) -> dict[str, Any]:
        item: dict[str, Any] = {}
        for field in schema:
            normalized = field.lower()
            value: Any = None
            if normalized in {"url", "href", "link"}:
                value = self._url(nodes)
            elif normalized in {"title", "name"}:
                value = self._title(nodes)
            elif normalized == "price":
                value = self._price(nodes)
            elif normalized == "text":
                value = self._quote_text(nodes)
            elif normalized in {"author", "writer"}:
                value = self._author(nodes)
            elif normalized in {"tags", "tag"}:
                value = self._tags(nodes)
            elif normalized in {"description", "summary"}:
                value = self._description(nodes)
            else:
                value = self._labelled_value(nodes, normalized)
            if self._meaningful(value):
                item[field] = value
        return item

    def _url(self, nodes: list[dict[str, Any]]) -> str:
        candidates = []
        for node in nodes:
            href = _clean(node.get("href"))
            if not href or self._is_auxiliary_link(node):
                continue
            normalized = self._normalize_url(
                href,
                _clean(node.get("url")),
            )
            if not normalized:
                continue
            text = _node_text(node).lower()
            score = 0
            if str(node.get("role") or "").lower() == "link":
                score += 50
            if text not in {"link", "image", "cover", "cover image"}:
                score += 40
            score += min(len(text), 80)
            candidates.append((score, normalized))
        return max(candidates, default=(0, ""))[1]

    def _title(self, nodes: list[dict[str, Any]]) -> str:
        candidates = []
        for node in nodes:
            text = _node_text(node)
            if len(text) < 2 or text.lower() in {
                "link",
                "image",
                "cover",
                "cover image",
                "details",
                "read more",
            }:
                continue
            tag = str(node.get("tag") or "").lower()
            role = str(node.get("role") or "").lower()
            score = min(len(text), 120)
            if tag in {"h1", "h2", "h3", "h4"} or role == "heading":
                score += 1000
            elif role == "link" and not self._is_auxiliary_link(node):
                score += 700
            elif tag == "img":
                score += 500
            elif tag in {"article", "li"}:
                score += 100
            if len(text) > 180:
                score -= 600
            candidates.append((score, text))
        return max(candidates, default=(0, ""))[1]

    @staticmethod
    def _price(nodes: list[dict[str, Any]]) -> str:
        candidates = []
        for node in nodes:
            text = _node_text(node)
            if not _PRICE_RE.fullmatch(text):
                continue
            score = 0
            if re.search(r"[$£€¥]", text):
                score += 100
            if re.search(r"\.\d{2}$", text):
                score += 80
            tag = str(node.get("tag") or "").lower()
            if tag in {"span", "div", "p"}:
                score += 20
            candidates.append((score, text))
        return max(candidates, default=(0, ""))[1]

    @staticmethod
    def _quote_text(nodes: list[dict[str, Any]]) -> str:
        candidates = []
        for node in nodes:
            text = _node_text(node)
            if len(text) < 8:
                continue
            tag = str(node.get("tag") or "").lower()
            has_quote = bool(_QUOTE_RE.search(text))
            score = min(len(text), 600)
            if has_quote:
                score += 1000
            if tag in {"q", "blockquote", "span", "p"}:
                score += 300
            if re.search(r"\bby\s+.+Tags?:", text, re.IGNORECASE):
                score -= 700
            candidates.append((score, text))
        return max(candidates, default=(0, ""))[1]

    @staticmethod
    def _author(nodes: list[dict[str, Any]]) -> str:
        small = [
            _node_text(node)
            for node in nodes
            if str(node.get("tag") or "").lower() == "small"
            and _node_text(node)
        ]
        if small:
            return min(small, key=len)
        matches = []
        for node in nodes:
            match = _AUTHOR_RE.search(_node_text(node))
            if match:
                matches.append(_clean(match.group(1)))
        return min(matches, key=len) if matches else ""

    @staticmethod
    def _tags(nodes: list[dict[str, Any]]) -> list[str]:
        values = []
        for node in nodes:
            href = _clean(node.get("href")).lower()
            if "/tag/" in href:
                text = _node_text(node)
                if text:
                    values.append(text)
        if not values:
            matches = []
            for node in nodes:
                match = _TAGS_RE.search(_node_text(node))
                if match:
                    matches.append(_clean(match.group(1)))
            if matches:
                values = min(matches, key=len).split()
        return list(dict.fromkeys(value for value in values if value))

    @staticmethod
    def _description(nodes: list[dict[str, Any]]) -> str:
        values = [
            _node_text(node)
            for node in nodes
            if len(_node_text(node)) >= 20
            and str(node.get("tag") or "").lower()
            in {"p", "div", "section", "article"}
        ]
        return max(values, key=len) if values else ""

    @staticmethod
    def _labelled_value(
        nodes: list[dict[str, Any]],
        field: str,
    ) -> str:
        for node in nodes:
            label = " ".join(
                _clean(node.get(key)).lower()
                for key in ("label", "aria_label", "name", "id")
            )
            if field in label:
                value = _clean(node.get("value") or node.get("text"))
                if value:
                    return value
        return ""

    @staticmethod
    def _sorted_cells(
        nodes: list[dict[str, Any]],
        tag: str,
    ) -> list[dict[str, Any]]:
        cells = [
            node
            for node in nodes
            if str(node.get("tag") or "").lower() == tag
        ]

        def index(node: dict[str, Any]) -> int:
            match = _CELL_INDEX_RE.search(str(node.get("xpath") or ""))
            return int(match.group(1)) if match else 10_000

        return sorted(cells, key=index)

    def _table_field_index(
        self,
        field: str,
        header_lookup: dict[str, int],
        fallback: int,
    ) -> int:
        normalized = self._normalize_header(field)
        aliases = self._TABLE_ALIASES.get(normalized, {normalized})
        for alias in aliases:
            if alias in header_lookup:
                return header_lookup[alias]
        return fallback if not header_lookup else -1

    @staticmethod
    def _normalize_header(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", _clean(value).lower())

    @staticmethod
    def _is_auxiliary_link(node: dict[str, Any]) -> bool:
        href = _clean(node.get("href")).lower()
        text = _node_text(node).lower()
        parsed = urlparse(href)
        path = parsed.path.lower()
        return (
            any(
                token in path
                for token in (
                    "/tag/",
                    "/author/",
                    "/authors/",
                    "/category/",
                    "/categories/",
                    "/login",
                    "/register",
                )
            )
            or text in _PAGINATION_TEXT
            or bool(re.fullmatch(r"\d+", text))
        )

    @staticmethod
    def _default_normalize_url(href: str, base_url: str = "") -> str:
        value = urljoin(base_url, href)
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        return value

    @staticmethod
    def _meaningful(value: Any) -> bool:
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, tuple, dict, set)):
            return bool(value)
        return value is not None

    @classmethod
    def _has_required_coverage(
        cls,
        item: dict[str, Any],
        schema: list[str],
    ) -> bool:
        if not schema:
            return bool(item)
        present = sum(
            cls._meaningful(item.get(field))
            for field in schema
        )
        return present / len(schema) >= 0.8

    @staticmethod
    def _item_signature(item: dict[str, Any]) -> str:
        for field in ("url", "href", "link"):
            if item.get(field):
                return f"url:{str(item[field]).rstrip('/')}"
        return json.dumps(item, ensure_ascii=False, sort_keys=True)
