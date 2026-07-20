from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_SESSION = "default"
DEFAULT_PORT_START = 9333
DEFAULT_PORT_END = 9433
SNAPSHOT_DEFAULT_DEPTH = 6


def score_text_match(
    node: dict,
    query: str,
    *,
    exact_name_weight: int = 40,
    exact_text_weight: int = 35,
    exact_label_weight: int = 25,
    sub_name_weight: int = 15,
    sub_text_weight: int = 12,
    sub_label_weight: int = 10,
    pinned_bias: int = 0,
    viewport_bias: int = 0,
    interactable_bias: int = 0,
    actionable_bias: int = 0,
    native_tag_bias: int = 0,
) -> int:
    """Score how well a node matches a text query.

    Callers should pass bias flags appropriate to their context.
    Default weights match the service-side scoring in CliService._filter_text_matches.
    """
    def _norm(text: str) -> str:
        t = re.sub(r"\s+", "", (text or "").strip().lower())
        t = re.sub(r"(?<!\d)0+(\d)", r"\1", t)
        return t

    score = 0
    query = _norm(query)
    exact_name = _norm(node.get("name"))
    exact_text = _norm(node.get("text"))
    label_text = _norm(node.get("label"))
    if exact_name == query:
        score += exact_name_weight
    if exact_text == query:
        score += exact_text_weight
    if label_text == query:
        score += exact_label_weight
    if query in exact_name:
        score += sub_name_weight
    if query in exact_text:
        score += sub_text_weight
    if query in label_text:
        score += sub_label_weight
    if pinned_bias and node.get("_pinned"):
        score += pinned_bias
    if viewport_bias and node.get("visibility", {}).get("in_viewport"):
        score += viewport_bias
    if interactable_bias and node.get("visibility", {}).get("interactable_now"):
        score += interactable_bias
    if actionable_bias and node.get("role") in {"button", "link"}:
        score += actionable_bias
    if native_tag_bias and node.get("tag") in {"button", "a", "input"}:
        score += native_tag_bias
    return score


@dataclass
class Bounds:
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0


@dataclass
class Visibility:
    visible: bool = False
    in_viewport: bool = False
    interactable_now: bool = False


@dataclass
class ContextInfo:
    landmark: str = ""
    heading: str = ""
    form: str = ""
    list: str = ""
    dialog: str = ""


@dataclass
class SnapshotNodeRecord:
    xpath: str
    ref_type: str
    tag: str
    role: str = ""
    name: str = ""
    text: str = ""
    value: str = ""
    element_id: str = ""
    placeholder: str = ""
    href: str = ""
    input_type: str = ""
    title: str = ""
    aria_label: str = ""
    alt: str = ""
    label: str = ""
    depth: int = 0
    parent_xpath: str | None = None
    bounds: Bounds = field(default_factory=Bounds)
    visibility: Visibility = field(default_factory=Visibility)
    context: ContextInfo = field(default_factory=ContextInfo)
    disabled: bool = False
    checked: bool = False
    selected: bool = False
    expanded: bool = False
    semantic_level: str = ""
    kind: str = ""
    group_ref: str | None = None
    item_ref: str | None = None
    fingerprint: str = ""
    semantic_fingerprint: str = ""
    fingerprint_version: str = ""
    locator_candidates: list[str] = field(default_factory=list)

    def locator(self) -> str:
        return f"xpath:{self.xpath}"

    def to_output(self, ref: str) -> dict:
        result = {
            "ref": ref,
            "ref_type": self.ref_type,
            "id": self.element_id,
            "tag": self.tag,
            "role": self.role,
            "name": self.name,
            "text": self.text,
            "value": self.value,
            "placeholder": self.placeholder,
            "href": self.href,
            "input_type": self.input_type,
            "title": self.title,
            "aria_label": self.aria_label,
            "alt": self.alt,
            "label": self.label,
            "locator": self.locator(),
            "depth": self.depth,
            "bounds": asdict(self.bounds),
            "visibility": asdict(self.visibility),
            "context": asdict(self.context),
            "states": {
                "disabled": self.disabled,
                "checked": self.checked,
                "selected": self.selected,
                "expanded": self.expanded,
            },
        }
        if self.semantic_level:
            result["semantic_level"] = self.semantic_level
        if self.kind:
            result["kind"] = self.kind
        if self.group_ref:
            result["group_ref"] = self.group_ref
        if self.item_ref:
            result["item_ref"] = self.item_ref
        if self.fingerprint:
            result["fingerprint"] = self.fingerprint
        if self.semantic_fingerprint:
            result["semantic_fingerprint"] = self.semantic_fingerprint
        if self.fingerprint_version:
            result["fingerprint_version"] = self.fingerprint_version
        if self.locator_candidates:
            result["locator_candidates"] = self.locator_candidates
        return result


@dataclass
class SnapshotArtifact:
    page: dict
    page_identity: dict
    mode: str
    scope: str
    root_ref: str | None
    depth: int | None
    nodes: list[dict]
    planner_view: dict | None = None
    schema_version: str = "0.6"
    groups: list[dict] = field(default_factory=list)
    recovery: dict = field(default_factory=dict)
    delta: dict = field(default_factory=dict)

    def to_output(self) -> dict:
        return asdict(self)


@dataclass
class GroupRecord:
    group_ref: str
    group_kind: str
    name: str
    item_refs: list[str] = field(default_factory=list)
    item_count: int = 0
    sample_fields: list[str] = field(default_factory=list)
    entry_action_refs: list[str] = field(default_factory=list)
    next_page_ref: str | None = None
    schema_hints: dict = field(default_factory=dict)


@dataclass
class RecoveryInfo:
    expand_candidates: list[str] = field(default_factory=list)
    offscreen_actionable_count: int = 0
    truncated_regions: list[str] = field(default_factory=list)
    truncation_reason: str | None = None
    truncation_threshold: int | None = None
    total_nodes: int = 0
    truncated: bool = False


@dataclass
class SessionMeta:
    session: str
    session_id: str
    port: int
    browser_path: str
    user_data_dir: str
    headless: bool = False
    runtime_id: str = ""
    runtime_status: str = "stale"
    browser_pid: int | None = None
    last_seen_at: str | None = None


@dataclass
class ActivePage:
    tab_id: str | None = None
    url: str | None = None
    title: str | None = None
    page_id: str | None = None
    snapshot_id: str | None = None
    snapshot_seq: int = 0


@dataclass
class SessionState:
    session: str
    session_id: str = ""
    runtime_id: str = ""
    last_tab_id: str | None = None
    active_page: ActivePage = field(default_factory=ActivePage)
    container_refs: dict[str, dict] = field(default_factory=dict)
    element_refs: dict[str, dict] = field(default_factory=dict)
    next_container_index: int = 1
    next_element_index: int = 1
    last_snapshot_file: str | None = None
    last_snapshot_mode: str | None = None
    last_snapshot_fingerprints: dict[str, str] = field(default_factory=dict)
    last_snapshot_diff: dict = field(default_factory=dict)


@dataclass
class SessionPaths:
    root: Path
    meta_file: Path
    state_file: Path
    profile_dir: Path
