from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_SESSION = "default"
DEFAULT_PORT_START = 9333
DEFAULT_PORT_END = 9433
INTERACTIVE_LOCATOR = (
    'css:a,button,input,textarea,select,summary,option,'
    '[role="button"],[role="link"],[role="textbox"],[role="checkbox"],'
    '[role="radio"],[role="tab"],[role="switch"],[onclick],[contenteditable="true"]'
)
SNAPSHOT_DEFAULT_DEPTH = 6


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

    def locator(self) -> str:
        return f"xpath:{self.xpath}"

    def to_output(self, ref: str) -> dict:
        return {
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

    def to_output(self) -> dict:
        return asdict(self)


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


@dataclass
class SessionPaths:
    root: Path
    meta_file: Path
    state_file: Path
    profile_dir: Path
