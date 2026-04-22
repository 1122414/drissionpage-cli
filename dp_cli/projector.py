from __future__ import annotations

import json
from dataclasses import dataclass, field

from dp_cli.models import RecoveryInfo


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

    def _select_visible_focus(self, nodes: list[dict], groups: list[dict]) -> list[dict]:
        focus = []
        for group in groups:
            focus.append({
                "ref": group.get("representative_ref", group.get("ref")),
                "kind": "group",
                "name": group.get("name", ""),
                "item_count": group.get("count", 0),
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

    def _build_repeated_regions(self, groups: list[dict]) -> list[dict]:
        regions = []
        for group in groups:
            regions.append({
                "group_ref": group.get("representative_ref", group.get("ref")),
                "group_kind": group.get("group_kind", "list"),
                "name": group.get("name", ""),
                "sample_item_names": [],
                "entry_action_refs": [],
                "next_page_ref": None,
            })
        return regions


class ExtractProjector:
    def project(self, group: dict, nodes: list[dict], schema: list[str] | None = None) -> dict:
        item_refs = group.get("item_refs", [])
        items = []
        for ref in item_refs:
            item_nodes = [n for n in nodes if n.get("item_ref") == ref or n["ref"] == ref]
            fields = {}
            for node in item_nodes:
                if node["ref_type"] == "element":
                    key = node.get("role", "text")
                    if key == "link" and node.get("href"):
                        fields["href"] = node["href"]
                    else:
                        fields[key] = node.get("text", node.get("name", ""))
            items.append({
                "item_ref": ref,
                "fields": fields,
            })
        return {
            "target": {
                "group_ref": group.get("group_ref", group.get("representative_ref")),
                "group_kind": group.get("group_kind", "list"),
            },
            "items": items,
            "schema": schema or list(fields.keys()) if items else [],
        }


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
