from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_SESSION = "default"
DEFAULT_PORT_START = 9333
DEFAULT_PORT_END = 9433
INTERACTIVE_LOCATOR = (
    'css:a,button,input,textarea,select,[role="button"],[onclick],[contenteditable="true"]'
)


@dataclass
class ElementRecord:
    xpath: str
    tag: str
    text: str = ""
    value: str = ""
    role: str = ""
    element_id: str = ""
    name: str = ""
    placeholder: str = ""
    href: str = ""
    input_type: str = ""

    def to_output(self, ref: str) -> dict:
        payload = asdict(self)
        payload["ref"] = ref
        payload["id"] = payload.pop("element_id")
        payload["locator"] = f"xpath:{self.xpath}"
        return payload


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
    refs: dict[str, dict] = field(default_factory=dict)
    next_ref_index: int = 1


@dataclass
class SessionPaths:
    root: Path
    meta_file: Path
    state_file: Path
    profile_dir: Path
