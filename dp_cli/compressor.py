from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


@dataclass
class CompressionConfig:
    min_group_size: int = 3
    max_sample_items: int = 3
    require_parent_semantic: bool = True
    structure_similarity_threshold: float = 0.85
    action_pattern_consistency: bool = True


@dataclass
class CompressedGroup:
    representative_ref: str
    member_refs: list[str]
    member_indices: list[int]
    count: int
    xpath_template: str
    tag: str
    role: str | None


class StructuralHasher:
    def compute(self, node: dict, child_hashes: list[str]) -> str:
        return self._build_hash(
            node.get("tag", ""),
            node.get("role", ""),
            node.get("input_type", ""),
            node.get("id", ""),
            child_hashes,
        )

    def _build_hash(self, tag: str, role: str, input_type: str, element_id: str, child_hashes: list[str]) -> str:
        parts = []
        parts.append(tag or "unknown")
        if role:
            parts.append(f"role={role}")
        child_signatures = []
        for ch in child_hashes:
            child_signatures.append(ch.split(":")[0])
        parts.append("children=" + "|".join(child_signatures))
        if tag == "input" and input_type:
            parts.append(f"type={input_type}")
        if element_id and not element_id.startswith(("_", "temp")):
            parts.append(f"id={element_id}")
        raw_key = "|".join(parts)
        return hashlib.sha256(raw_key.encode()).hexdigest()[:16]

class DOMCompressor:
    def __init__(self, config: CompressionConfig | None = None) -> None:
        self.config = config or CompressionConfig()
        self.hasher = StructuralHasher()

    def compress(self, nodes: list[dict]) -> list[CompressedGroup]:
        children_map = self._build_children_map(nodes)
        lookup = {node["ref"]: node for node in nodes}
        compressed_groups = []
        for parent_ref, children in children_map.items():
            if len(children) < self.config.min_group_size:
                continue
            hashes = [self._node_hash(c, children_map.get(c["ref"], [])) for c in children]
            groups = self._group_by_hash(children, hashes)
            for group in groups:
                if not self._should_compress(group, lookup, children_map):
                    continue
                cg = self._create_compressed_group(group)
                compressed_groups.append(cg)
        return compressed_groups

    def _build_children_map(self, nodes: list[dict]) -> dict[str, list[dict]]:
        mapping: dict[str, list[dict]] = {}
        for node in nodes:
            parent_ref = node.get("parent_ref")
            if not parent_ref:
                continue
            mapping.setdefault(parent_ref, []).append(node)
        return mapping

    def _node_hash(self, node: dict, children: list[dict]) -> str:
        child_hashes = [self._node_hash(c, []) for c in children]
        return self.hasher.compute(node, child_hashes)

    def _group_by_hash(self, children: list[dict], hashes: list[str]) -> list[list[dict]]:
        if not children:
            return []
        groups = []
        current = [children[0]]
        current_hash = hashes[0]
        for i in range(1, len(children)):
            if hashes[i] == current_hash:
                current.append(children[i])
            else:
                groups.append(current)
                current = [children[i]]
                current_hash = hashes[i]
        groups.append(current)
        return groups

    def _should_compress(self, group: list[dict], lookup: dict, children_map: dict) -> bool:
        if len(group) < self.config.min_group_size:
            return False
        parent_ref = group[0].get("parent_ref")
        if self.config.require_parent_semantic and parent_ref:
            parent = lookup.get(parent_ref)
            if parent and parent.get("role") not in {
                "list", "table", "grid", "tree", "region", "main", "navigation"
            }:
                if parent.get("tag") not in {"ul", "ol", "table", "div", "section"}:
                    return False
        if self.config.action_pattern_consistency:
            roles = [c.get("role") for c in group if c.get("ref_type") == "element"]
            if roles:
                from collections import Counter
                most_common = Counter(roles).most_common(1)[0][1]
                if most_common / len(roles) < self.config.structure_similarity_threshold:
                    return False
        return True

    def _create_compressed_group(self, group: list[dict]) -> CompressedGroup:
        template = group[0]
        base_xpath = template.get("xpath", "")
        xpath_template = re.sub(r"\[\d+\]$", "[{i}]", base_xpath)
        indices = []
        for item in group:
            xpath = item.get("xpath", "")
            match = re.search(r"\[(\d+)\]$", xpath)
            if match:
                indices.append(int(match.group(1)))
            else:
                indices.append(-1)
        return CompressedGroup(
            representative_ref=template["ref"],
            member_refs=[c["ref"] for c in group[: self.config.max_sample_items]],
            member_indices=indices,
            count=len(group),
            xpath_template=xpath_template,
            tag=template.get("tag", ""),
            role=template.get("role") or None,
        )
