from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit


class NodeFingerprint:
    """Legacy v1 fingerprint. Kept byte-compatible for stored sessions."""

    def compute(self, node: dict) -> str:
        parts = []
        if node.get("role"):
            parts.append(f"role={node['role']}")
        if node.get("name"):
            parts.append(f"name={self._normalize(node['name'])}")
        for attr in ["href", "src", "value", "title"]:
            if node.get(attr):
                parts.append(f"{attr}={node[attr][:50]}")
        ancestor_path = self._get_ancestor_path(node)
        path_sig = " > ".join([f"{p['tag']}[{p.get('role', '')}]" for p in ancestor_path])
        parts.append(f"path={path_sig}")
        if node.get("group_ref") and node.get("item_ref"):
            group_role = self._get_group_role(node["group_ref"])
            group_name = self._get_group_name(node["group_ref"])
            parts.append(f"group_role={group_role}")
            parts.append(f"group_name={group_name[:20]}")
            item_index = self._get_item_index(node)
            group_size = self._get_group_size(node)
            if item_index == 0:
                parts.append("pos=first")
            elif item_index == group_size - 1:
                parts.append("pos=last")
            else:
                parts.append("pos=middle")
        neighbor_text = self._get_neighbor_text(node)
        if neighbor_text:
            parts.append(f"neighbor={neighbor_text[:30]}")
        if node.get("context", {}).get("landmark"):
            parts.append(f"landmark={node['context']['landmark']}")
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _normalize(self, value: str) -> str:
        return value.strip().lower()[:50]

    # NOTE: The following helper methods are intentionally stubbed no-ops.
    # They are called by compute() but return constant empty values.
    # Changing their return values would alter fingerprint hashes,
    # which would invalidate all stored refs in existing .dpcli/ sessions.
    # Only modify these if you also implement a migration strategy for
    # existing session state files.
    def _get_ancestor_path(self, node: dict) -> list[dict]:  # TODO: stubbed no-op
        path = []
        current = node
        while current.get("parent_ref"):
            parent_ref = current["parent_ref"]
            path.insert(0, {"tag": current.get("tag", ""), "role": current.get("role", "")})
            current = {"parent_ref": None}
        return path

    def _get_group_role(self, group_ref: str) -> str:  # TODO: stubbed no-op
        return ""

    def _get_group_name(self, group_ref: str) -> str:  # TODO: stubbed no-op
        return ""

    def _get_item_index(self, node: dict) -> int:  # TODO: stubbed no-op
        return -1

    def _get_group_size(self, node: dict) -> int:  # TODO: stubbed no-op
        return 0

    def _get_neighbor_text(self, node: dict) -> str:  # TODO: stubbed no-op
        return ""


class FingerprintIndex:
    def __init__(self):
        self._index: dict[str, str] = {}

    def add(self, ref: str, fingerprint: str) -> None:
        self._index[fingerprint] = ref

    def find(self, fingerprint: str) -> str | None:
        return self._index.get(fingerprint)

    def clear(self) -> None:
        self._index.clear()


class SemanticFingerprint:
    """Versioned semantic identity that deliberately excludes xpath and classes."""

    VERSION = "2"
    _DYNAMIC_ID = re.compile(
        r"(?:^|[-_])(?:\d{5,}|[a-f0-9]{10,})(?:$|[-_])",
        flags=re.IGNORECASE,
    )

    def features(
        self,
        node: dict,
        lookup: dict[str, dict] | None = None,
    ) -> dict[str, object]:
        lookup = lookup or {}
        context = node.get("context") or {}
        accessible_name = (
            node.get("name")
            or node.get("aria_label")
            or node.get("label")
            or node.get("placeholder")
            or node.get("alt")
            or ""
        )
        element_id = str(node.get("id") or node.get("element_id") or "")
        if self._DYNAMIC_ID.search(element_id):
            element_id = ""
        features: dict[str, object] = {
            "tag": str(node.get("tag") or "").lower(),
            "role": str(node.get("role") or "").lower(),
            "name": self._normalize(accessible_name),
            "id": self._normalize(element_id),
            "href": self._stable_url(node.get("href")),
            "input_type": str(node.get("input_type") or "").lower(),
            "landmark": self._normalize(context.get("landmark")),
            "form": self._normalize(context.get("form")),
            "list": self._normalize(context.get("list")),
            "ancestor_path": self._ancestor_path(node, lookup),
        }
        return {key: value for key, value in features.items() if value}

    def compute(
        self,
        node: dict,
        lookup: dict[str, dict] | None = None,
    ) -> str:
        raw = json.dumps(
            self.features(node, lookup),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        return f"sf{self.VERSION}_{digest}"

    def similarity(
        self,
        left: dict[str, object],
        right: dict[str, object],
    ) -> float:
        weights = {
            "tag": 0.10,
            "role": 0.15,
            "name": 0.35,
            "id": 0.15,
            "href": 0.15,
            "input_type": 0.04,
            "landmark": 0.02,
            "form": 0.02,
            "list": 0.01,
            "ancestor_path": 0.01,
        }
        available = 0.0
        matched = 0.0
        for key, weight in weights.items():
            left_value = left.get(key)
            right_value = right.get(key)
            if not left_value or not right_value:
                continue
            available += weight
            if left_value == right_value:
                matched += weight
        return matched / available if available else 0.0

    @staticmethod
    def _normalize(value: object) -> str:
        text = re.sub(r"\s+", " ", str(value or "").strip().lower())
        return text[:120]

    @staticmethod
    def _stable_url(value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            parsed = urlsplit(text)
        except ValueError:
            return text[:160]
        if parsed.scheme or parsed.netloc:
            return f"{parsed.netloc.lower()}{parsed.path.rstrip('/')}"[:160]
        return parsed.path.rstrip("/")[:160]

    def _ancestor_path(
        self,
        node: dict,
        lookup: dict[str, dict],
    ) -> list[str]:
        path = []
        current = node
        seen = set()
        for _ in range(4):
            parent_key = current.get("parent_ref") or current.get("parent_xpath")
            if not parent_key or parent_key in seen:
                break
            seen.add(parent_key)
            parent = lookup.get(str(parent_key))
            if not isinstance(parent, dict):
                break
            label = (
                parent.get("role")
                or parent.get("tag")
                or parent.get("name")
                or ""
            )
            if label:
                path.insert(0, self._normalize(label))
            current = parent
        return path
