from __future__ import annotations

from types import SimpleNamespace

from dp_cli.fingerprint import NodeFingerprint, SemanticFingerprint
from dp_cli.models import ActivePage, SessionState, SnapshotNodeRecord
from dp_cli.runtime import RuntimeContext


class _Manager:
    def save_meta(self, _meta):
        return None

    def save_state(self, _state):
        return None


class _Tab:
    tab_id = "tab-1"
    url = "https://example.test/products"
    title = "Products"


def _runtime() -> RuntimeContext:
    meta = SimpleNamespace(
        session_id="sess-1",
        runtime_id="rt-1",
        browser_pid=10,
        runtime_status="running",
        last_seen_at=None,
    )
    state = SessionState(
        session="test",
        session_id="sess-1",
        runtime_id="rt-1",
        active_page=ActivePage(
            tab_id="tab-1",
            url=_Tab.url,
            title=_Tab.title,
            page_id="page-1",
            snapshot_id="snap-1",
        ),
    )
    browser = SimpleNamespace(process_id=10, latest_tab=_Tab())
    return RuntimeContext(_Manager(), meta, state, browser, _Tab())


def _button(xpath: str, name: str = "Search") -> SnapshotNodeRecord:
    return SnapshotNodeRecord(
        xpath=xpath,
        parent_xpath="/html/body",
        ref_type="element",
        tag="button",
        role="button",
        name=name,
    )


def test_semantic_fingerprint_ignores_xpath_but_legacy_hash_is_preserved():
    left = {
        "xpath": "/html/body/div[1]/button",
        "tag": "button",
        "role": "button",
        "name": "Search",
    }
    right = {
        **left,
        "xpath": "/html/body/div[9]/button",
    }

    semantic = SemanticFingerprint()
    assert semantic.compute(left) == semantic.compute(right)
    assert NodeFingerprint().compute(left) == NodeFingerprint().compute(right)


def test_runtime_rebinds_ref_after_xpath_shift_and_emits_delta():
    runtime = _runtime()
    first = runtime.upsert_nodes(
        [_button("/html/body/div[1]/button")],
        track_delta=True,
    )
    runtime.state.active_page.snapshot_id = "snap-2"
    second = runtime.upsert_nodes(
        [_button("/html/body/div[2]/button")],
        track_delta=True,
    )

    assert first[0]["ref"] == second[0]["ref"] == "e1"
    assert second[0]["ref_rebound"] is True
    assert second[0]["fingerprint_version"] == "2"
    assert runtime.state.last_snapshot_diff["rebound_refs"] == ["e1"]
    assert runtime.state.last_snapshot_diff["added_refs"] == []
    assert runtime.state.last_snapshot_diff["removed_refs"] == []


def test_runtime_does_not_rebind_low_similarity_replacement_at_same_xpath():
    runtime = _runtime()
    first = runtime.upsert_nodes(
        [_button("/html/body/div[1]/button", "Search")],
        track_delta=True,
    )
    runtime.state.active_page.snapshot_id = "snap-2"
    replacement = SnapshotNodeRecord(
        xpath="/html/body/div[1]/button",
        parent_xpath="/html/body",
        ref_type="element",
        tag="a",
        role="link",
        name="Delete account",
        href="/danger/delete",
    )
    second = runtime.upsert_nodes([replacement], track_delta=True)

    assert first[0]["ref"] == "e1"
    assert second[0]["ref"] == "e2"
    assert runtime.state.last_snapshot_diff["added_refs"] == ["e2"]
    assert runtime.state.last_snapshot_diff["removed_refs"] == ["e1"]
