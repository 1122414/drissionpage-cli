from __future__ import annotations

import hashlib


class NodeFingerprint:
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
