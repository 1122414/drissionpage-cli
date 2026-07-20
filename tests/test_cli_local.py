from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from dp_cli.cli import build_parser
from dp_cli.projector import ExtractProjector
from dp_cli.session import SessionManager
from dp_cli.service import CliService
from tests.support import (
    HOT_DONE_TEXT,
    MOVIES_LINK_ID,
    NEXT_PAGE_ID,
    SEARCH_BUTTON_ID,
    SEARCH_CONTAINER_ROLE,
    SEARCH_DONE_NAME,
    SEARCH_INPUT_ID,
    assert_search_state,
    cleanup_session,
    run_cli,
    run_local_workflow,
    run_task_agent_loop,
    select_node,
    snapshot_nodes,
)


def test_semantic_snapshot_and_min_agent_loop(local_fixture_server, local_session):
    try:
        results = run_local_workflow(local_session, local_fixture_server.url)
        opened = results["opened"]
        assert opened["ok"] is True
        assert opened["data"]["page"]["title"] == "dp_cli Fixture"

        root_snapshot = results["root_snapshot"]
        assert root_snapshot["ok"] is True
        assert root_snapshot["data"]["mode"] == "agent_summary"
        assert root_snapshot["data"]["artifact_file"]
        assert Path(root_snapshot["data"]["artifact_file"]).exists()
        assert "index" in root_snapshot["data"]

        nodes = snapshot_nodes(root_snapshot)
        search_containers = [node for node in nodes if node.get("ref_type") == "container" and node.get("role") == SEARCH_CONTAINER_ROLE]
        navigation_nodes = [node for node in nodes if node.get("ref_type") == "element" and node.get("role") == "link" and ((node.get("name") or "").lower() == "movies" or (node.get("text") or "").lower() == "movies")]
        pagination_nodes = [node for node in nodes if node.get("ref_type") == "element" and node.get("role") == "button" and "next" in (node.get("name") or "").lower()]
        assert len(search_containers) >= 1
        assert len(navigation_nodes) == 1
        assert len(pagination_nodes) == 1

        search_container = results["search_container"]
        assert search_container.get("in_viewport") is True

        subtree_snapshot = results["subtree_snapshot"]
        assert subtree_snapshot["ok"] is True
        assert subtree_snapshot["data"]["mode"] == "full"
        assert subtree_snapshot["data"]["root_ref"] == search_container["ref"]
        assert subtree_snapshot["data"]["scope"] == "subtree"
        assert "nodes" in subtree_snapshot["data"]

        assert results["typed"]["ok"] is True
        assert results["clicked"]["ok"] is True

        post_nodes = snapshot_nodes(results["post_snapshot"])
        search_button, _, hot_button = assert_search_state(post_nodes, "Agentic CLI")
        assert search_button["name"] == SEARCH_DONE_NAME
        assert hot_button["text"] == HOT_DONE_TEXT
    finally:
        cleanup_session(local_session)


def test_snapshot_ref_expands_selected_container(local_fixture_server, local_session):
    try:
        run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")
        root_snapshot = run_cli("snapshot", "--session", local_session, "--headless")
        search_container = select_node(root_snapshot, ref_type="container", role=SEARCH_CONTAINER_ROLE)

        expanded = run_cli(
            "snapshot",
            search_container["ref"],
            "--session",
            local_session,
            "--headless",
            "--depth",
            "3",
            "--view",
            "full",
        )
        assert expanded["ok"] is True
        assert expanded["data"]["mode"] == "full"
        assert expanded["data"]["root_ref"] == search_container["ref"]
        expanded_nodes = snapshot_nodes(expanded)
        assert any(item.get("id") == SEARCH_BUTTON_ID for item in expanded_nodes)
        assert any(item.get("id") == SEARCH_INPUT_ID for item in expanded_nodes)
    finally:
        cleanup_session(local_session)


def test_click_and_type_reject_container_refs(local_fixture_server, local_session):
    try:
        run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")
        root_snapshot = run_cli("snapshot", "--session", local_session, "--headless")
        search_container = select_node(root_snapshot, ref_type="container", role=SEARCH_CONTAINER_ROLE)

        click_container = run_cli("click", "--session", local_session, "--headless", "--ref", search_container["ref"], check=False)
        assert click_container["ok"] is False
        assert click_container["error"]["code"] == "invalid_ref_type"

        type_container = run_cli(
            "type",
            "--session",
            local_session,
            "--headless",
            "--ref",
            search_container["ref"],
            "--text",
            "bad ref",
            check=False,
        )
        assert type_container["ok"] is False
        assert type_container["error"]["code"] == "invalid_ref_type"
    finally:
        cleanup_session(local_session)


def test_ref_becomes_stale_after_page_changes(local_fixture_server, local_session):
    try:
        opened = run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")
        assert opened["ok"] is True

        found = run_cli("find", "--session", local_session, "--headless", "--text", "Movies")
        ref = found["data"]["nodes"][0]["ref"]

        navigated = run_cli("open", "about:blank", "--session", local_session, "--headless")
        assert navigated["ok"] is True

        stale = run_cli("click", "--session", local_session, "--headless", "--ref", ref, check=False)
        assert stale["ok"] is False
        assert stale["error"]["code"] == "ref_stale"
    finally:
        cleanup_session(local_session)


def test_open_recovers_from_stale_saved_tab_id(local_fixture_server, local_session):
    manager = SessionManager()
    try:
        opened = run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")
        assert opened["ok"] is True

        state_path = manager.session_paths(local_session).state_file
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["last_tab_id"] = "BROKEN-TAB-ID"
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

        reopened = run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")
        assert reopened["ok"] is True
        assert reopened["data"]["page"]["title"] == "dp_cli Fixture"
    finally:
        cleanup_session(local_session)


def test_click_target_blank_switches_runtime_to_new_tab(local_fixture_server, local_session):
    try:
        opened = run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")
        assert opened["ok"] is True

        found = run_cli("find", "--session", local_session, "--headless", "--text", "Open detail in new tab")
        link = select_node(found, ref_type="element", role="link", name_contains="Open detail")

        clicked = run_cli("click", "--session", local_session, "--headless", "--ref", link["ref"])
        assert clicked["ok"] is True
        assert clicked["data"]["tab_transition"]["opened_new_tab"] is True
        assert clicked["data"]["page"]["url"].endswith("/detail.html")

        snapshot = run_cli("snapshot", "--session", local_session, "--headless")
        assert snapshot["ok"] is True
        assert snapshot["data"]["page"]["title"] == "Detail Fixture"
        assert snapshot["data"]["page"]["url"].endswith("/detail.html")

        found_detail = run_cli("find", "--session", local_session, "--headless", "--text", "New tab detail content")
        assert found_detail["ok"] is True
        assert found_detail["data"]["page"]["title"] == "Detail Fixture"
        assert found_detail["data"]["count"] >= 1

        evaluated = run_cli("eval", "document.title", "--session", local_session, "--headless")
        assert evaluated["ok"] is True
        assert evaluated["data"]["result"] == "Detail Fixture"
        assert evaluated["data"]["page"]["url"] == found_detail["data"]["page"]["url"]
        assert evaluated["data"]["page_identity"]["page_id"]
    finally:
        cleanup_session(local_session)


def test_live_session_rejects_headless_mode_change(local_fixture_server, local_session):
    manager = SessionManager()
    try:
        opened = run_cli("open", local_fixture_server.url, "--session", local_session)
        assert opened["ok"] is True

        meta_path = manager.session_paths(local_session).meta_file
        before = json.loads(meta_path.read_text(encoding="utf-8"))
        assert before["headless"] is False

        found = run_cli(
            "find",
            "--session",
            local_session,
            "--headless",
            "--text",
            "Movies",
            check=False,
        )
        assert found["ok"] is False
        assert found["error"]["code"] == "browser_config_error"
        assert found["error"]["details"]["active_headless"] is False
        assert found["error"]["details"]["requested_headless"] is True

        after = json.loads(meta_path.read_text(encoding="utf-8"))
        assert after["headless"] is False
    finally:
        cleanup_session(local_session)


def test_session_inspect_returns_agent_friendly_identity(local_fixture_server, local_session):
    try:
        opened = run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")
        assert opened["ok"] is True

        run_cli("snapshot", "--session", local_session, "--headless")
        inspected = run_cli("session", "inspect", "--session", local_session, "--headless")
        assert inspected["ok"] is True
        data = inspected["data"]
        assert data["session_name"] == local_session
        assert data["session_id"]
        assert data["runtime"]["runtime_id"]
        assert data["runtime"]["status"] == "running"
        assert data["page"]["page_id"]
        assert data["page"]["url"] == local_fixture_server.url
        assert data["container_ref_count"] >= 1
        assert data["last_snapshot_file"]
        assert data["last_snapshot_mode"] == "agent_summary"
    finally:
        cleanup_session(local_session)


def test_live_headless_session_keeps_mode_when_flag_is_omitted(local_fixture_server, local_session):
    manager = SessionManager()
    try:
        opened = run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")
        assert opened["ok"] is True

        inspected = run_cli("session", "inspect", "--session", local_session)
        assert inspected["ok"] is True
        assert inspected["data"]["runtime"]["headless"] is True

        meta = json.loads(
            manager.session_paths(local_session).meta_file.read_text(encoding="utf-8")
        )
        assert meta["headless"] is True
    finally:
        cleanup_session(local_session)


def test_session_close_stops_browser(local_fixture_server, local_session):
    manager = SessionManager()
    try:
        opened = run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")
        assert opened["ok"] is True

        closed = run_cli("session", "close", "--session", local_session)
        assert closed["ok"] is True
        assert closed["action"] == "session.close"
        assert closed["data"]["closed"] is True
        assert closed["data"]["was_running"] is True

        meta = json.loads(
            manager.session_paths(local_session).meta_file.read_text(encoding="utf-8")
        )
        assert meta["runtime_status"] == "stopped"
        assert meta["browser_pid"] is None
    finally:
        cleanup_session(local_session)


def test_session_close_waits_for_debug_port_release(monkeypatch, tmp_path):
    import dp_cli.session as session_module

    manager = SessionManager(base_dir=tmp_path / ".dpcli")
    manager.load_meta(session="close-race", headless=True)
    listening_states = iter([True, True, False])
    quit_calls = []

    class FakeChromium:
        def __init__(self, _options):
            pass

        def quit(self, timeout=5, force=False):
            quit_calls.append({"timeout": timeout, "force": force})

    monkeypatch.setattr(
        session_module,
        "port_is_listening",
        lambda _port: next(listening_states),
    )
    monkeypatch.setattr(session_module, "Chromium", FakeChromium)
    monkeypatch.setattr(session_module.time, "sleep", lambda _seconds: None)

    closed = manager.close_session("close-race")

    assert closed["closed"] is True
    assert closed["was_running"] is True
    assert closed["forced"] is False
    assert quit_calls == [{"timeout": 5, "force": False}]


def test_session_close_forces_exit_only_after_wait_timeout(monkeypatch, tmp_path):
    import dp_cli.session as session_module

    manager = SessionManager(base_dir=tmp_path / ".dpcli")
    manager.load_meta(session="close-force", headless=True)
    listening_states = iter([True, True, False])
    monotonic_values = iter([0.0, 6.0, 10.0])
    quit_calls = []

    class FakeChromium:
        def __init__(self, _options):
            pass

        def quit(self, timeout=5, force=False):
            quit_calls.append({"timeout": timeout, "force": force})

    monkeypatch.setattr(
        session_module,
        "port_is_listening",
        lambda _port: next(listening_states),
    )
    monkeypatch.setattr(session_module, "Chromium", FakeChromium)
    monkeypatch.setattr(
        session_module.time,
        "monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(session_module.time, "sleep", lambda _seconds: None)

    closed = manager.close_session("close-force")

    assert closed["closed"] is True
    assert closed["forced"] is True
    assert quit_calls == [
        {"timeout": 5, "force": False},
        {"timeout": 5, "force": True},
    ]


def test_runtime_persist_keeps_meta_and_state_identity(local_fixture_server, local_session):
    manager = SessionManager()
    try:
        run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")
        run_cli("snapshot", "--session", local_session, "--headless")

        paths = manager.session_paths(local_session)
        meta = json.loads(paths.meta_file.read_text(encoding="utf-8"))
        state = json.loads(paths.state_file.read_text(encoding="utf-8"))

        assert meta["session_id"]
        assert meta["runtime_id"]
        assert meta["runtime_status"] == "running"
        assert state["session_id"] == meta["session_id"]
        assert state["runtime_id"] == meta["runtime_id"]
        assert state["active_page"]["page_id"]
        assert state["active_page"]["snapshot_id"]
        assert state["container_refs"]
        assert state["last_snapshot_file"]
        assert state["last_snapshot_mode"] == "agent_summary"
    finally:
        cleanup_session(local_session)


def test_task_agent_loop_executes_text_driven_steps(local_fixture_server, local_session):
    try:
        results = run_task_agent_loop(
            session=local_session,
            url=local_fixture_server.url,
            steps=[
                {"kind": "click_text", "text": "Movies", "description": "Click the Movies navigation link"},
                {
                    "kind": "repeat_click_text",
                    "candidates": ["Next page"],
                    "repeat": 2,
                    "description": "Advance the next page button twice",
                },
            ],
            headless=True,
        )
        assert results["opened"]["ok"] is True
        assert results["initial_snapshot"]["ok"] is True
        assert len(results["steps"]) == 2
        assert results["steps"][0]["clicked"]["ok"] is True
        assert len(results["steps"][1]["repeats"]) == 2
        assert all(item["clicked"]["ok"] is True for item in results["steps"][1]["repeats"])

        final_nodes = snapshot_nodes(results["final_snapshot"])
        next_page = select_node(final_nodes, ref_type="element", role="button", name_contains="Next")
        movies_link = select_node(final_nodes, ref_type="element", role="link", name_contains="Movies")
        assert next_page.get("in_viewport") is True
        assert movies_link.get("name") == "Movies" or movies_link.get("text") == "Movies"
    finally:
        cleanup_session(local_session)


def test_snapshot_index_keeps_navigation_and_pagination_visible_to_agent(local_fixture_server, local_session):
    try:
        run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")
        planner_snapshot = run_cli("snapshot", "--session", local_session, "--headless")
        data = planner_snapshot["data"]

        assert "index" in data
        index = data["index"]
        surface = index["surface_index"]
        interactable = index["interactable_elements"]
        stats = index["stats"]

        all_index_nodes = [*surface, *interactable]
        assert any(node.get("role") == "link" and "movies" in (node.get("name") or "").lower() for node in all_index_nodes)

        found = run_cli("find", "--session", local_session, "--headless", "--text", "Next page")
        assert found["ok"] is True
        assert found["data"]["count"] >= 1

        assert stats["total_nodes"] >= len(snapshot_nodes(planner_snapshot))

        full_snapshot = run_cli("snapshot", "--session", local_session, "--headless", "--view", "full")
        assert full_snapshot["data"]["count"] >= len(snapshot_nodes(planner_snapshot))
    finally:
        cleanup_session(local_session)


def test_snapshot_index_prioritizes_dialog_controls_for_agent_summary():
    def node(
        ref: str,
        *,
        ref_type: str = "element",
        tag: str = "button",
        role: str = "button",
        name: str = "",
        text: str = "",
        parent_ref: str | None = None,
        interactable: bool = True,
    ) -> dict:
        return {
            "ref": ref,
            "ref_type": ref_type,
            "tag": tag,
            "role": role,
            "name": name,
            "text": text,
            "parent_ref": parent_ref,
            "visibility": {
                "visible": True,
                "in_viewport": True,
                "interactable_now": interactable,
            },
            "states": {
                "disabled": False,
                "checked": False,
                "selected": False,
                "expanded": False,
            },
            "semantic_level": "surface",
        }

    background = [node(f"e{i}", tag="a", role="link", name=f"image {i}") for i in range(1, 40)]
    dialog = node(
        "r1",
        ref_type="container",
        tag="div",
        role="dialog",
        name="dialog",
        text="Login Register",
        interactable=False,
    )
    tablist = node("e40", tag="div", role="tablist", name="Login Register", text="Login Register", parent_ref="r1")
    login = node("e41", tag="span", role="tab", name="Login", text="Login", parent_ref="e40")
    register = node("e42", tag="span", role="tab", name="Register", text="Register", parent_ref="e40")

    index = CliService()._build_index([*background, dialog, tablist, login, register])

    top_interactable = index["interactable_elements"][:5]
    assert any(item["ref"] == "e42" and item["role"] == "tab" for item in top_interactable)
    assert any(item["ref"] == "e41" and item["role"] == "tab" for item in top_interactable)
    assert any(item["ref"] == "e42" for item in index["surface_index"][:10])


def test_snapshot_index_keeps_form_field_and_checkbox_metadata_visible():
    def node(
        ref: str,
        *,
        role: str,
        tag: str = "input",
        name: str = "",
        text: str = "",
        input_type: str = "",
        value: str = "",
        checked: bool = False,
        parent_ref: str | None = "r1",
    ) -> dict:
        return {
            "ref": ref,
            "ref_type": "element",
            "tag": tag,
            "role": role,
            "name": name,
            "text": text,
            "value": value,
            "placeholder": "",
            "label": "",
            "input_type": input_type,
            "parent_ref": parent_ref,
            "visibility": {
                "visible": True,
                "in_viewport": True,
                "interactable_now": True,
            },
            "states": {
                "disabled": False,
                "checked": checked,
                "selected": False,
                "expanded": False,
            },
            "semantic_level": "surface",
        }

    dialog = {
        "ref": "r1",
        "ref_type": "container",
        "tag": "div",
        "role": "dialog",
        "name": "dialog",
        "text": "",
        "parent_ref": None,
        "visibility": {
            "visible": True,
            "in_viewport": True,
            "interactable_now": False,
        },
        "states": {
            "disabled": False,
            "checked": False,
            "selected": False,
            "expanded": False,
        },
        "semantic_level": "surface",
    }
    index = CliService()._build_index(
        [
            dialog,
            node("e1", role="textbox", name="Nickname", input_type="text", value="yyyyb"),
            node("e2", role="textbox", name="Phone", input_type="text"),
            node("e3", role="textbox", name="Code", input_type="text"),
            node("e4", role="textbox", name="Password", input_type="password"),
            node("e5", role="button", tag="button", name="Register", text="Register"),
            node("e6", role="checkbox", tag="span", name="I agree to terms", checked=False),
            node("e7", role="button", tag="span", name="Get code", text="Get code"),
        ]
    )

    interactable = index["interactable_elements"]
    checkbox = next(item for item in interactable if item["ref"] == "e6")
    password = next(item for item in interactable if item["ref"] == "e4")
    nickname = next(item for item in interactable if item["ref"] == "e1")
    assert interactable.index(checkbox) < interactable.index(next(item for item in interactable if item["ref"] == "e5"))
    assert checkbox["role"] == "checkbox"
    assert checkbox["checked"] is False
    assert password["input_type"] == "password"
    assert nickname["value"] == "yyyyb"


def test_custom_checkbox_click_returns_post_checked_state(local_fixture_server, local_session):
    try:
        run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")
        before = run_cli("snapshot", "--session", local_session, "--headless")
        checkbox = next(
            item
            for item in before["data"]["index"]["interactable_elements"]
            if item.get("role") == "checkbox" and "I agree" in item.get("name", "")
        )
        assert checkbox["checked"] is False

        clicked = run_cli("click", "--session", local_session, "--headless", "--ref", checkbox["ref"])
        assert clicked["ok"] is True
        assert clicked["data"]["target_state"]["checked"] is True

        after = run_cli("snapshot", "--session", local_session, "--headless")
        checked = next(
            item
            for item in after["data"]["index"]["interactable_elements"]
            if item.get("ref") == checkbox["ref"]
        )
        assert checked["checked"] is True
    finally:
        cleanup_session(local_session)


def test_snapshot_index_promotes_extractable_data_regions():
    def container(ref: str, xpath: str, depth: int, text: str = "") -> dict:
        return {
            "ref": ref,
            "ref_type": "container",
            "tag": "div",
            "role": "",
            "name": "Movie list",
            "text": text,
            "xpath": xpath,
            "depth": depth,
            "visibility": {"visible": True, "in_viewport": True, "interactable_now": False},
            "states": {"disabled": False, "checked": False, "selected": False, "expanded": False},
            "semantic_level": "surface",
        }

    def movie_link(index: int) -> dict:
        return {
            "ref": f"e{index}",
            "ref_type": "element",
            "tag": "a",
            "role": "link",
            "name": f"Movie {index}",
            "text": f"Movie {index}",
            "href": f"/vod-detail-id-{index}.html",
            "url": "https://example.test/vod-show-id-3.html",
            "xpath": f"/html/body/div/main/div/a[{index}]",
            "visibility": {"visible": True, "in_viewport": True, "interactable_now": True},
            "states": {"disabled": False, "checked": False, "selected": False, "expanded": False},
            "semantic_level": "surface",
        }

    nodes = [
        container("r1", "/html/body/div", 1, "Home Filter Movie 1 Movie 2 Movie 3"),
        container("r2", "/html/body/div/main/div", 3, "Movie 1 Movie 2 Movie 3"),
        *[movie_link(i) for i in range(1, 5)],
    ]

    index = CliService()._build_index(nodes)

    assert index["data_regions"][0]["ref"] == "r2"
    assert index["data_regions"][0]["item_count"] == 4
    assert index["surface_index"][0]["ref"] == "r2"
    assert index["surface_index"][0]["item_count"] == 4


def test_snapshot_index_detects_generic_content_links_without_detail_tokens():
    def container(ref: str, xpath: str, depth: int, role: str = "") -> dict:
        return {
            "ref": ref,
            "ref_type": "container",
            "tag": "section",
            "role": role,
            "name": "Products",
            "text": "Alpha Beta Gamma Delta",
            "xpath": xpath,
            "depth": depth,
            "visibility": {"visible": True, "in_viewport": True, "interactable_now": False},
            "states": {"disabled": False, "checked": False, "selected": False, "expanded": False},
            "semantic_level": "surface",
        }

    def product_link(index: int) -> dict:
        return {
            "ref": f"e{index}",
            "ref_type": "element",
            "tag": "a",
            "role": "link",
            "name": f"Product {index}",
            "text": f"Product {index}",
            "href": f"/products/{index}",
            "url": "https://example.test/catalog",
            "xpath": f"/html/body/main/section/article[{index}]/a",
            "visibility": {"visible": True, "in_viewport": True, "interactable_now": True},
            "states": {"disabled": False, "checked": False, "selected": False, "expanded": False},
            "semantic_level": "surface",
        }

    nodes = [
        container("r1", "/html/body/nav", 1, role="navigation"),
        container("r2", "/html/body/main/section", 3),
        *[product_link(i) for i in range(1, 5)],
    ]

    index = CliService()._build_index(nodes)

    assert index["data_regions"][0]["ref"] == "r2"
    assert index["data_regions"][0]["kind"] in {"card_grid", "repeated_structure"}
    assert index["data_regions"][0]["sample_items"][0]["url"] == "https://example.test/products/1"


def test_snapshot_index_rejects_script_and_navigation_links_from_data_region():
    service = CliService()
    assert service._is_extractable_item_link(
        {
            "ref_type": "element",
            "role": "link",
            "href": "javascript:void(0)",
            "text": "展开更多",
        }
    ) is False
    assert service._is_extractable_item_link(
        {
            "ref_type": "element",
            "role": "link",
            "href": "/author/42",
            "text": "作者甲",
        }
    ) is False
    assert service._is_extractable_item_link(
        {
            "ref_type": "element",
            "role": "link",
            "href": "category/books/travel_2/index.html",
            "text": "Travel",
        }
    ) is False
    assert service._is_extractable_item_link(
        {
            "ref_type": "element",
            "role": "link",
            "href": "/products/42",
            "text": "Product 42",
        }
    ) is True


def test_snapshot_index_prefers_content_list_over_relative_category_sidebar():
    def container(ref: str, tag: str, xpath: str, name: str) -> dict:
        return {
            "ref": ref,
            "ref_type": "container",
            "tag": tag,
            "role": "",
            "name": name,
            "text": name,
            "xpath": xpath,
            "depth": 3,
            "visibility": {
                "visible": True,
                "in_viewport": True,
                "interactable_now": False,
            },
            "states": {
                "disabled": False,
                "checked": False,
                "selected": False,
                "expanded": False,
            },
            "semantic_level": "surface",
        }

    def link(ref: str, href: str, text: str, xpath: str) -> dict:
        return {
            "ref": ref,
            "ref_type": "element",
            "tag": "a",
            "role": "link",
            "name": text,
            "text": text,
            "href": href,
            "url": "https://books.example/catalogue/page-2.html",
            "xpath": xpath,
            "visibility": {
                "visible": True,
                "in_viewport": True,
                "interactable_now": True,
            },
            "states": {
                "disabled": False,
                "checked": False,
                "selected": False,
                "expanded": False,
            },
            "semantic_level": "surface",
        }

    category_links = [
        link(
            f"e{i}",
            f"category/books/category-{i}/index.html",
            f"Category {i}",
            f"/html/body/aside/ul/li[{i}]/a",
        )
        for i in range(1, 7)
    ]
    product_links = [
        link(
            f"e{i + 10}",
            f"book-{i}/index.html",
            f"Book {i}",
            f"/html/body/main/ol/li[{i}]/article/a",
        )
        for i in range(1, 5)
    ]
    nodes = [
        container("r1", "ul", "/html/body/aside/ul", "Categories"),
        container("r2", "ol", "/html/body/main/ol", "Books"),
        *category_links,
        *product_links,
    ]

    index = CliService()._build_index(nodes)

    assert index["data_regions"][0]["ref"] == "r2"
    assert all(region["ref"] != "r1" for region in index["data_regions"])


def test_snapshot_index_rejects_taxonomy_and_tracking_regions():
    def container(ref: str, xpath: str, depth: int, name: str) -> dict:
        return {
            "ref": ref,
            "ref_type": "container",
            "tag": "div",
            "role": "",
            "name": name,
            "text": name,
            "xpath": xpath,
            "depth": depth,
            "visibility": {
                "visible": True,
                "in_viewport": True,
                "interactable_now": False,
            },
            "states": {
                "disabled": False,
                "checked": False,
                "selected": False,
                "expanded": False,
            },
            "semantic_level": "surface",
        }

    def link(ref: str, href: str, text: str, xpath: str) -> dict:
        return {
            "ref": ref,
            "ref_type": "element",
            "tag": "a",
            "role": "link",
            "name": text,
            "text": text,
            "href": href,
            "url": "https://movie.example/chart",
            "xpath": xpath,
            "visibility": {
                "visible": True,
                "in_viewport": True,
                "interactable_now": True,
            },
            "states": {
                "disabled": False,
                "checked": False,
                "selected": False,
                "expanded": False,
            },
            "semantic_level": "surface",
        }

    taxonomy = [
        link(
            f"e{i}",
            f"/typerank?type={i}",
            f"类型{i}",
            f"/html/body/main/aside/a[{i}]",
        )
        for i in range(1, 7)
    ]
    tracking = [
        link(
            f"t{i}",
            f"https://track.example/promotion?link={i}",
            f"合作媒体{i}",
            f"/html/body/main/div[1]/a[{i}]",
        )
        for i in range(1, 7)
    ]
    content = [
        link(
            f"c{i}",
            f"/subject/{i}/",
            f"真实内容标题 {i}",
            f"/html/body/main/section/article[{i}]/a",
        )
        for i in range(1, 7)
    ]
    nodes = [
        container("r1", "/html/body/main/aside", 4, "类型筛选"),
        container("r2", "/html/body/main/div[1]", 5, "合作媒体"),
        container("r3", "/html/body/main/section", 3, "主要内容"),
        *taxonomy,
        *tracking,
        *content,
    ]

    index = CliService()._build_index(nodes)

    assert index["data_regions"][0]["ref"] == "r3"
    assert all(region["ref"] not in {"r1", "r2"} for region in index["data_regions"])


def test_projection_item_refs_prefer_detail_links_over_taxonomy_links():
    nodes = [
        {
            "ref": "category-1",
            "ref_type": "element",
            "role": "link",
            "href": "/typerank?type=1",
            "text": "剧情片",
        },
        {
            "ref": "promotion-1",
            "ref_type": "element",
            "role": "link",
            "href": "/promotion/partner",
            "text": "合作推广",
        },
        *[
            {
                "ref": f"trailer-{index}",
                "ref_type": "element",
                "role": "link",
                "href": f"/subject/{index}/trailer",
                "text": "link",
            }
            for index in range(1, 6)
        ],
        *[
            {
                "ref": f"movie-{index}",
                "ref_type": "element",
                "role": "link",
                "href": f"/subject/{index}/",
                "text": f"真实电影标题 {index}",
            }
            for index in range(1, 6)
        ],
        {
            "ref": "movie-description",
            "ref_type": "element",
            "role": "",
            "href": "",
            "text": "电影简介",
        },
    ]

    refs = CliService()._projection_item_refs(nodes)

    assert refs == [f"movie-{index}" for index in range(1, 6)]


def test_snapshot_index_rejects_div_based_footer_navigation_regions():
    footer_text = (
        "Contact us Privacy Policy Site navigation Follow us "
        "Copyright 2006-2026 ICP report email"
    )
    nodes = [
        {
            "ref": "footer-root",
            "ref_type": "container",
            "tag": "div",
            "role": "",
            "name": "footer",
            "text": footer_text,
            "xpath": "/html/body[1]/div[3]",
            "depth": 1,
        },
        {
            "ref": "footer-nav",
            "ref_type": "container",
            "tag": "dl",
            "role": "",
            "name": "site navigation",
            "text": "Site navigation Features News Community Follow us",
            "xpath": "/html/body[1]/div[3]/div[1]/dl[1]",
            "depth": 3,
        },
        *[
            {
                "ref": f"footer-link-{index}",
                "ref_type": "element",
                "tag": "a",
                "role": "link",
                "name": title,
                "text": title,
                "href": href,
                "url": "https://example.test/",
                "xpath": (
                    "/html/body[1]/div[3]/div[1]/dl[1]"
                    f"/dd[{index}]/a[1]"
                ),
                "depth": 5,
            }
            for index, (title, href) in enumerate(
                [
                    ("Feature planning", "/feature/"),
                    ("Global news", "/news/"),
                    ("Community", "/community/"),
                    ("Follow us", "/follow/"),
                    ("Feedback", "/feedback/"),
                ],
                1,
            )
        ],
    ]

    regions = CliService()._detect_data_regions(nodes)

    assert regions == []


def test_snapshot_index_prefers_article_titles_over_short_menu_links():
    def container(ref: str, xpath: str, depth: int) -> dict:
        return {
            "ref": ref,
            "ref_type": "container",
            "tag": "div",
            "role": "",
            "name": ref,
            "text": ref,
            "xpath": xpath,
            "depth": depth,
            "visibility": {
                "visible": True,
                "in_viewport": True,
                "interactable_now": False,
            },
            "states": {
                "disabled": False,
                "checked": False,
                "selected": False,
                "expanded": False,
            },
            "semantic_level": "surface",
        }

    def link(ref: str, href: str, text: str, xpath: str) -> dict:
        return {
            "ref": ref,
            "ref_type": "element",
            "tag": "a",
            "role": "link",
            "name": text,
            "text": text,
            "href": href,
            "url": "https://example.test/",
            "xpath": xpath,
            "visibility": {
                "visible": True,
                "in_viewport": True,
                "interactable_now": True,
            },
            "states": {
                "disabled": False,
                "checked": False,
                "selected": False,
                "expanded": False,
            },
            "semantic_level": "surface",
        }

    menu = [
        link(
            f"m{i}",
            f"/menu-{i}/",
            text,
            f"/html/body/main/div[1]/a[{i}]",
        )
        for i, text in enumerate(("精华", "候选", "订阅", "分类", "标签"), 1)
    ]
    articles = [
        link(
            f"a{i}",
            f"/writer-name/p/{i}",
            f"这是第{i}篇具有实际语义的中文文章标题",
            f"/html/body/main/div[2]/article[{i}]/a",
        )
        for i in range(1, 6)
    ]
    nodes = [
        container("r1", "/html/body/main/div[1]", 5),
        container("r2", "/html/body/main/div[2]", 3),
        *menu,
        *articles,
    ]

    index = CliService()._build_index(nodes)

    assert index["data_regions"][0]["ref"] == "r2"


def test_extract_projector_outputs_relative_detail_links_with_schema():
    nodes = [
        {
            "ref": f"e{i}",
            "ref_type": "element",
            "tag": "a",
            "role": "link",
            "name": f"Movie {i}",
            "text": f"Movie {i}",
            "href": f"/vod-detail-id-{i}.html",
            "url": "https://example.test/vod-show-id-3.html",
        }
        for i in range(1, 4)
    ]

    result = ExtractProjector().project(
        {"representative_ref": "r1", "item_refs": [node["ref"] for node in nodes]},
        nodes,
        ["title", "url"],
    )

    assert result["item_count"] == 3
    assert result["items"][0] == {
        "url": "https://example.test/vod-detail-id-1.html",
        "title": "Movie 1",
    }


def test_extract_projector_deduplicates_cover_and_title_links_by_url():
    nodes = [
        {
            "ref": "e1",
            "ref_type": "element",
            "tag": "a",
            "role": "link",
            "name": "Cover image",
            "text": "Cover image",
            "href": "/catalogue/book-one/index.html",
            "url": "https://example.test/",
        },
        {
            "ref": "e2",
            "ref_type": "element",
            "tag": "a",
            "role": "link",
            "name": "Book One",
            "text": "Book One",
            "href": "/catalogue/book-one/index.html",
            "url": "https://example.test/",
        },
    ]

    result = ExtractProjector().project(
        {"representative_ref": "r1", "item_refs": ["e1", "e2"]},
        nodes,
        ["title", "url"],
    )

    assert result["item_count"] == 1
    assert result["items"][0]["url"] == "https://example.test/catalogue/book-one/index.html"
    assert result["items"][0]["title"] == "Book One"


def test_extract_projector_keeps_click_navigation_metadata_without_schema():
    nodes = [
        {
            "ref": "e1",
            "ref_type": "element",
            "tag": "a",
            "role": "link",
            "name": "Product One",
            "text": "Product One",
            "href": "/products/one",
            "url": "https://example.test/catalog",
        },
        {
            "ref": "e2",
            "ref_type": "element",
            "tag": "a",
            "role": "link",
            "name": "Product Two",
            "text": "Product Two",
            "href": "/products/two",
            "url": "https://example.test/catalog",
        },
        {
            "ref": "e3",
            "ref_type": "element",
            "tag": "a",
            "role": "link",
            "name": "Product Three",
            "text": "Product Three",
            "href": "/products/three",
            "url": "https://example.test/catalog",
        },
    ]

    result = ExtractProjector().project(
        {"representative_ref": "r1", "item_refs": [node["ref"] for node in nodes]},
        nodes,
    )

    assert result["items"][0]["detail_url"] == "https://example.test/products/one"
    assert result["items"][0]["item_ref"] == "e1"
    assert result["items"][0]["source_page_url"] == "https://example.test/catalog"


def test_common_wait_time_argument_is_available_to_batch_detail_command():
    args = build_parser().parse_args(
        [
            "batch-detail-extract",
            "--items-json",
            "[]",
            "--wait-time",
            "1.25",
            "--wait-jitter",
            "0.5",
            "--max-retries",
            "2",
            "--schema",
            "price",
            "url",
            "--extractor",
            "auto",
            "--navigation-mode",
            "direct",
            "--item-timeout",
            "120",
            "--ai-timeout",
            "30",
            "--output-file",
            "log/out.json",
            "--progress-file",
            "log/progress.jsonl",
        ]
    )

    assert args.wait_time == 1.25
    assert args.wait_jitter == 0.5
    assert args.max_retries == 2
    assert args.schema == ["price", "url"]
    assert args.extractor == "auto"
    assert args.navigation_mode == "direct"
    assert args.item_timeout == 120
    assert args.ai_timeout == 30
    assert args.output_file == "log/out.json"
    assert args.progress_file == "log/progress.jsonl"
    assert args.headless is None


def test_session_close_parser_does_not_require_browser_mode():
    args = build_parser().parse_args(["session", "close", "--session", "unit"])
    assert args.command == "session"
    assert args.session_command == "close"
    assert args.session == "unit"


def test_batch_detail_ai_extractor_uses_generic_page_package(monkeypatch):
    class FakeAdapter:
        def page_info(self, tab):
            return {"url": tab.url, "title": "Detail"}

        def open_url(self, tab, url):
            tab.url = url
            return self.page_info(tab)

        def detail_page_package(self, tab):
            return {"url": tab.url, "title": "Detail", "body_text": "Price: 12"}

    class FakeTab:
        url = "https://example.test/catalog"

    class FakeRuntime:
        def __init__(self):
            self.tab = FakeTab()
            self.state = type("State", (), {"active_page": type("Page", (), {"url": self.tab.url})()})()

        def sync_page_identity(self):
            self.state.active_page.url = self.tab.url

        def persist(self):
            pass

    class FakeAiExtractor:
        def extract(self, page_package, schema=None):
            return {
                "detail_info": {"price": "12"},
                "fields": ["price"],
                "confidence": 0.9,
                "warnings": [],
                "template": {"extract_strategy": "fake_ai"},
            }

    service = CliService(adapter=FakeAdapter())
    runtime = FakeRuntime()

    @contextmanager
    def fake_runtime(*args, **kwargs):
        yield runtime

    monkeypatch.setattr(service, "_with_runtime", fake_runtime)
    monkeypatch.setattr("dp_cli.service.AiDetailExtractor", FakeAiExtractor)

    result = service.batch_extract_detail_pages(
        [{"title": "One", "url": "https://example.test/item/one"}],
        extractor="ai",
        navigation_mode="direct",
    )

    assert result["items"][0]["detail_ok"] is True
    assert result["items"][0]["detail_info"] == {"price": "12"}
    assert result["detail_template"] == {"extract_strategy": "fake_ai"}


def test_batch_detail_writes_incremental_output_and_progress(monkeypatch, tmp_path):
    class FakeAdapter:
        def page_info(self, tab):
            return {"url": tab.url, "title": "Detail"}

        def open_url(self, tab, url):
            tab.url = url
            return self.page_info(tab)

        def detail_page_package(self, tab):
            return {"url": tab.url, "title": "Detail", "body_text": f"Detail for {tab.url}"}

    class FakeTab:
        url = "https://example.test/catalog"

    class FakeRuntime:
        def __init__(self):
            self.tab = FakeTab()
            self.state = type("State", (), {"active_page": type("Page", (), {"url": self.tab.url})()})()

        def sync_page_identity(self):
            self.state.active_page.url = self.tab.url

        def persist(self):
            pass

    class FakeAiExtractor:
        def __init__(self, *args, **kwargs):
            self.timeout = kwargs.get("timeout")

        def extract(self, page_package, schema=None):
            return {
                "detail_info": {"url": page_package["url"], "schema": ",".join(schema or [])},
                "fields": ["url", "schema"],
                "confidence": 1,
                "warnings": [],
                "template": {"extract_strategy": "fake_ai"},
            }

    service = CliService(adapter=FakeAdapter())
    runtime = FakeRuntime()

    @contextmanager
    def fake_runtime(*args, **kwargs):
        yield runtime

    monkeypatch.setattr(service, "_with_runtime", fake_runtime)
    monkeypatch.setattr("dp_cli.service.AiDetailExtractor", FakeAiExtractor)

    output_file = tmp_path / "detail-output.json"
    progress_file = tmp_path / "detail-progress.jsonl"
    result = service.batch_extract_detail_pages(
        [
            {"title": "One", "url": "https://example.test/item/one"},
            {"title": "Missing"},
            {"title": "Two", "url": "https://example.test/item/two"},
        ],
        extractor="ai",
        navigation_mode="direct",
        schema=["url"],
        item_timeout=30,
        ai_timeout=12,
        output_file=str(output_file),
        progress_file=str(progress_file),
    )

    assert result["partial"] is False
    assert result["item_count"] == 3
    assert result["detail_pages_extracted"] == 2
    assert result["output_file"] == str(output_file)
    assert result["progress_file"] == str(progress_file)
    assert result["ai_timeout"] == 12
    assert result["item_timeout"] == 30

    saved = json.loads(output_file.read_text(encoding="utf-8"))
    assert saved["partial"] is False
    assert saved["item_count"] == 3
    assert saved["items"][0]["detail_ok"] is True
    assert saved["items"][1]["detail_error"] == "Missing detail URL."

    progress_entries = [
        json.loads(line)
        for line in progress_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [entry["index"] for entry in progress_entries] == [1, 2, 3]
    assert progress_entries[0]["total"] == 3
    assert progress_entries[1]["detail_ok"] is False
    assert progress_entries[2]["detail_info"]["url"].endswith("/two")


def test_batch_detail_rejects_non_http_url_without_opening_it(monkeypatch):
    opened_urls = []

    class FakeAdapter:
        def page_info(self, tab):
            return {"url": tab.url, "title": "Detail"}

        def open_url(self, tab, url):
            opened_urls.append(url)
            tab.url = url
            return self.page_info(tab)

        def detail_page_package(self, tab):
            return {"url": tab.url, "title": "Detail", "body_text": "Price: 12"}

    class FakeTab:
        url = "https://example.test/catalog"

    class FakeRuntime:
        def __init__(self):
            self.tab = FakeTab()
            self.state = type("State", (), {"active_page": type("Page", (), {"url": self.tab.url})()})()

        def sync_page_identity(self):
            self.state.active_page.url = self.tab.url

        def persist(self):
            pass

    class FakeAiExtractor:
        def extract(self, page_package, schema=None):
            return {
                "detail_info": {"price": "12"},
                "fields": ["price"],
                "confidence": 0.9,
                "warnings": [],
                "template": {"extract_strategy": "fake_ai"},
            }

    service = CliService(adapter=FakeAdapter())
    runtime = FakeRuntime()

    @contextmanager
    def fake_runtime(*args, **kwargs):
        yield runtime

    monkeypatch.setattr(service, "_with_runtime", fake_runtime)
    monkeypatch.setattr("dp_cli.service.AiDetailExtractor", FakeAiExtractor)

    result = service.batch_extract_detail_pages(
        [
            {"title": "Bad", "url": "javascript:void(0)"},
            {"title": "Good", "url": "https://example.test/item/good"},
        ],
        extractor="ai",
        navigation_mode="direct",
    )

    assert result["items"][0]["detail_ok"] is False
    assert "HTTP" in result["items"][0]["detail_error"]
    assert result["items"][0]["requested_url"] == "javascript:void(0)"
    assert opened_urls == ["https://example.test/item/good"]
    assert result["items"][1]["final_url"] == "https://example.test/item/good"


def test_batch_detail_legacy_js_extracts_semantic_fixture(local_fixture_server, local_session):
    detail_url = local_fixture_server.url.replace("index.html", "detail.html")
    try:
        result = run_cli(
            "batch-detail-extract",
            "--session",
            local_session,
            "--headless",
            "--items-json",
            json.dumps([{"title": "Detail", "url": detail_url}]),
            "--source-url",
            local_fixture_server.url,
            "--extractor",
            "legacy-js",
            "--navigation-mode",
            "direct",
            "--schema",
            "title",
            "description",
        )

        assert result["ok"] is True
        row = result["data"]["items"][0]
        assert row["detail_ok"] is True
        assert row["requested_url"] == detail_url
        assert row["final_url"] == detail_url
        assert row["detail_info"]["title"] == "Structured Detail Fixture"
        assert "Visible detail description" in row["detail_info"]["description"]
    finally:
        cleanup_session(local_session)


def test_snapshot_index_prefers_leaf_text_button_over_wide_parent():
    def node(
        ref: str,
        *,
        tag: str,
        role: str,
        name: str,
        text: str,
        parent_ref: str | None = "r1",
    ) -> dict:
        return {
            "ref": ref,
            "ref_type": "element",
            "tag": tag,
            "role": role,
            "name": name,
            "text": text,
            "value": "",
            "placeholder": "",
            "label": "",
            "input_type": "",
            "parent_ref": parent_ref,
            "visibility": {
                "visible": True,
                "in_viewport": True,
                "interactable_now": True,
            },
            "states": {
                "disabled": False,
                "checked": False,
                "selected": False,
                "expanded": False,
            },
            "semantic_level": "surface",
        }

    dialog = {
        "ref": "r1",
        "ref_type": "container",
        "tag": "div",
        "role": "dialog",
        "name": "dialog",
        "text": "",
        "parent_ref": None,
        "visibility": {
            "visible": True,
            "in_viewport": True,
            "interactable_now": False,
        },
        "states": {
            "disabled": False,
            "checked": False,
            "selected": False,
            "expanded": False,
        },
        "semantic_level": "surface",
    }
    index = CliService()._build_index(
        [
            dialog,
            node("e1", tag="div", role="button", name="Get code", text="Get code"),
            node("e2", tag="div", role="button", name="Get code", text="Get code", parent_ref="e1"),
            node("e3", tag="span", role="button", name="Get code", text="Get code", parent_ref="e2"),
        ]
    )

    assert index["interactable_elements"][0]["ref"] == "e3"


def test_find_locator_finds_icon_search_element(local_fixture_server, local_session):
    try:
        run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")

        found = run_cli("find", "--session", local_session, "--headless", "--locator", ".icon-search")
        assert found["ok"] is True
        assert found["data"]["count"] >= 1
        icon_elements = [
            node for node in found["data"]["nodes"]
            if node.get("tag") == "i" and "icon-search" in (node.get("id") or "")
        ]
        assert len(icon_elements) >= 1
        icon = icon_elements[0]
        assert icon["ref_type"] == "element"
        assert icon.get("role") in ("link", "button", "generic", "")
    finally:
        cleanup_session(local_session)


def test_find_and_click_can_operate_on_offscreen_pagination(local_fixture_server, local_session):
    try:
        run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")

        found = run_cli("find", "--session", local_session, "--headless", "--text", "Next page")
        assert found["ok"] is True
        next_page = found["data"]["nodes"][0]
        assert next_page["id"] == NEXT_PAGE_ID
        assert next_page["visibility"]["in_viewport"] is False

        clicked = run_cli("click", "--session", local_session, "--headless", "--ref", next_page["ref"])
        assert clicked["ok"] is True

        planner_snapshot = run_cli("snapshot", "--session", local_session, "--headless")
        planner_nodes = snapshot_nodes(planner_snapshot)
        next_page_after = select_node(planner_nodes, ref_type="element", role="button", name_contains="Next")
        assert next_page_after.get("in_viewport") is True
    finally:
        cleanup_session(local_session)


def test_find_text_matches_leading_zero_episode(local_fixture_server, local_session):
    try:
        run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")

        found = run_cli("find", "--session", local_session, "--headless", "--text", "第5集")
        assert found["ok"] is True
        assert found["data"]["count"] >= 1
        episode_nodes = [
            node for node in found["data"]["nodes"]
            if "第05集" in (node.get("text") or "") or "第05集" in (node.get("name") or "")
        ]
        assert len(episode_nodes) >= 1
        episode = episode_nodes[0]
        assert episode["ref_type"] == "element"
    finally:
        cleanup_session(local_session)


def test_type_submit_applies_search_filter(local_fixture_server, local_session):
    try:
        run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")
        found = run_cli(
            "find",
            "--session",
            local_session,
            "--headless",
            "--locator",
            "#search-input",
        )
        search_input = select_node(
            found["data"]["nodes"],
            ref_type="element",
            element_id=SEARCH_INPUT_ID,
        )

        typed = run_cli(
            "type",
            "--session",
            local_session,
            "--headless",
            "--ref",
            search_input["ref"],
            "--text",
            "Boston",
            "--submit",
            "--wait-time",
            "0.1",
        )
        status = run_cli(
            "eval",
            "document.querySelector('#search-status').textContent",
            "--session",
            local_session,
            "--headless",
        )

        assert typed["ok"] is True
        assert typed["data"]["submitted"] is True
        assert typed["data"]["typed_text"] == "Boston"
        assert status["data"]["result"] == "Searching: Boston"
    finally:
        cleanup_session(local_session)


def test_scroll_command_returns_before_after_metrics(local_fixture_server, local_session):
    try:
        run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")

        scrolled = run_cli(
            "scroll",
            "--session",
            local_session,
            "--headless",
            "--direction",
            "down",
            "--amount",
            "700",
        )
        bottom = run_cli(
            "scroll",
            "--session",
            local_session,
            "--headless",
            "--to",
            "bottom",
        )

        assert scrolled["ok"] is True
        assert scrolled["data"]["direction"] == "down"
        assert scrolled["data"]["amount"] == 700
        assert scrolled["data"]["after"]["y"] >= scrolled["data"]["before"]["y"]
        assert bottom["data"]["to"] == "bottom"
        assert bottom["data"]["after"]["y"] >= scrolled["data"]["after"]["y"]
        assert bottom["data"]["after"]["at_bottom"] is True
    finally:
        cleanup_session(local_session)


def test_request_id_replays_browser_action_without_second_side_effect(
    local_fixture_server,
    local_session,
):
    try:
        run_cli(
            "open",
            local_fixture_server.url,
            "--session",
            local_session,
            "--headless",
        )
        request_id = f"{local_session}-scroll-once"
        first = run_cli(
            "scroll",
            "--direction",
            "down",
            "--amount",
            "700",
            "--session",
            local_session,
            "--headless",
            "--request-id",
            request_id,
        )
        second = run_cli(
            "scroll",
            "--direction",
            "down",
            "--amount",
            "700",
            "--session",
            local_session,
            "--headless",
            "--request-id",
            request_id,
        )

        assert first["data"]["_idempotency"] == {
            "request_id": request_id,
            "replayed": False,
        }
        assert second["data"]["_idempotency"] == {
            "request_id": request_id,
            "replayed": True,
        }
        assert second["data"]["before"] == first["data"]["before"]
        assert second["data"]["after"] == first["data"]["after"]
    finally:
        cleanup_session(local_session)


def test_batch_detail_resumes_successful_rows_from_progress_file(
    local_fixture_server,
    local_session,
    tmp_path,
):
    detail_url = local_fixture_server.url.replace("index.html", "detail.html")
    items = [
        {"title": "First", "url": f"{detail_url}?item=1"},
        {"title": "Second", "url": f"{detail_url}?item=2"},
    ]
    progress_file = tmp_path / "detail-progress.jsonl"
    output_file = tmp_path / "detail-output.json"
    common_args = (
        "batch-detail-extract",
        "--session",
        local_session,
        "--headless",
        "--items-json",
        json.dumps(items),
        "--source-url",
        local_fixture_server.url,
        "--extractor",
        "legacy-js",
        "--navigation-mode",
        "direct",
        "--schema",
        "title",
        "description",
        "--progress-file",
        str(progress_file),
        "--output-file",
        str(output_file),
    )
    try:
        first = run_cli(*common_args)
        second = run_cli(*common_args)

        assert first["ok"] is True
        assert first["data"]["processed_count"] == 2
        assert first["data"]["resumed_count"] == 0
        assert second["ok"] is True
        assert second["data"]["processed_count"] == 0
        assert second["data"]["resumed_count"] == 2
        assert second["data"]["detail_pages_extracted"] == 2
        assert len(second["data"]["items"]) == 2
    finally:
        cleanup_session(local_session)


def test_find_text_recognizes_tab_item_span_as_element(local_fixture_server, local_session):
    try:
        run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")

        found = run_cli("find", "--session", local_session, "--headless", "--text", "注册")
        assert found["ok"] is True
        assert found["data"]["count"] >= 1

        # Find the exact "注册" span node (not the parent container)
        register_nodes = [
            node for node in found["data"]["nodes"]
            if node.get("name") == "注册" and node.get("tag") == "span"
        ]
        assert len(register_nodes) >= 1, "Expected span with name='注册' to be found"
        register = register_nodes[0]
        assert register["ref_type"] == "element"
        assert register.get("exact_match") is True
    finally:
        cleanup_session(local_session)


def test_snapshot_index_structure_meets_design_criteria(local_fixture_server, local_session):
    try:
        run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")
        snapshot = run_cli("snapshot", "--session", local_session, "--headless")
        data = snapshot["data"]
        assert data["schema_version"] == "0.6"
        index = data["index"]
        stats = index["stats"]
        total = stats["total_nodes"]
        surface = stats["surface_count"]
        deep = stats["deep_count"]

        # 1. surface + deep = total (mutual exclusivity + completeness)
        assert surface + deep == total

        # 2. surface index should be <= 70% of total (design guideline; fixture page is small so ratio is higher)
        assert surface / total <= 0.70

        # 3. No empty string values in interactable_elements
        for item in index.get("interactable_elements", []):
            for key, value in item.items():
                assert value != "", f"Empty string in interactable_elements.{key}: {item}"

        # 4. No null values at top level of index fields
        for item in index.get("surface_index", []):
            assert item.get("ref") is not None
            assert item.get("ref_type") is not None

        # 5. Tree structure consistency
        tree = index.get("tree", {})
        children_map = tree.get("children_map", {})
        parent_map = tree.get("parent_map", {})
        for child_ref, parent_ref in parent_map.items():
            assert parent_ref in children_map, f"Parent {parent_ref} not in children_map"
            assert child_ref in children_map.get(parent_ref, []), f"Child {child_ref} not in parent's children"

        # 6. surface_index and deep_index are mutually exclusive (no shared refs)
        surface_refs = {item["ref"] for item in index.get("surface_index", [])}
        deep_refs = {item["ref"] for item in index.get("deep_index", [])}
        assert not surface_refs & deep_refs, f"Shared refs between surface and deep: {surface_refs & deep_refs}"

        # 7. deep_index has no empty string values either
        for item in index.get("deep_index", []):
            for key, value in item.items():
                assert value != "", f"Empty string in deep_index.{key}: {item}"

        # 8. deep_index name/text truncation (max 40/60 chars)
        for item in index.get("deep_index", []):
            name = item.get("name", "")
            text = item.get("text", "")
            assert len(name) <= 40, f"deep_index name too long ({len(name)} chars): {name[:50]}"
            assert len(text) <= 60, f"deep_index text too long ({len(text)} chars): {text[:70]}"
    finally:
        cleanup_session(local_session)
