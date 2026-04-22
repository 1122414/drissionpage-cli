from __future__ import annotations

import json
from pathlib import Path

from dp_cli.session import SessionManager
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
        assert root_snapshot["data"]["mode"] == "semantic"
        assert root_snapshot["data"]["artifact_file"]
        assert Path(root_snapshot["data"]["artifact_file"]).exists()
        assert "planner_view" in root_snapshot["data"]

        nodes = snapshot_nodes(root_snapshot)
        search_containers = [node for node in nodes if node["ref_type"] == "container" and node["role"] == SEARCH_CONTAINER_ROLE]
        navigation_nodes = [node for node in nodes if node["ref_type"] == "element" and node["id"] == MOVIES_LINK_ID]
        pagination_nodes = [node for node in nodes if node["ref_type"] == "element" and node["id"] == NEXT_PAGE_ID]
        assert len(search_containers) >= 1
        assert len(navigation_nodes) == 1
        assert len(pagination_nodes) == 1

        search_container = results["search_container"]
        assert search_container["visibility"]["visible"] is True
        assert search_container["visibility"]["in_viewport"] is True

        subtree_snapshot = results["subtree_snapshot"]
        assert subtree_snapshot["ok"] is True
        assert subtree_snapshot["data"]["mode"] == "semantic"
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
        assert expanded["data"]["mode"] == "semantic"
        assert expanded["data"]["root_ref"] == search_container["ref"]
        expanded_nodes = snapshot_nodes(expanded)
        assert any(item["id"] == SEARCH_BUTTON_ID for item in expanded_nodes)
        assert any(item["id"] == SEARCH_INPUT_ID for item in expanded_nodes)
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


def test_live_session_does_not_flip_headless_mode(local_fixture_server, local_session):
    manager = SessionManager()
    try:
        opened = run_cli("open", local_fixture_server.url, "--session", local_session)
        assert opened["ok"] is True

        meta_path = manager.session_paths(local_session).meta_file
        before = json.loads(meta_path.read_text(encoding="utf-8"))
        assert before["headless"] is False

        found = run_cli("find", "--session", local_session, "--headless", "--text", "Movies")
        assert found["ok"] is True

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
        assert data["last_snapshot_mode"] == "planner"
    finally:
        cleanup_session(local_session)


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
        assert state["last_snapshot_mode"] == "planner"
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
        next_page = select_node(final_nodes, ref_type="element", element_id=NEXT_PAGE_ID)
        movies_link = select_node(final_nodes, ref_type="element", element_id=MOVIES_LINK_ID)
        assert next_page["visibility"]["visible"] is True
        assert movies_link["name"] == "Movies"
    finally:
        cleanup_session(local_session)


def test_snapshot_planner_view_keeps_navigation_and_pagination_visible_to_agent(local_fixture_server, local_session):
    try:
        run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")
        planner_snapshot = run_cli("snapshot", "--session", local_session, "--headless")
        data = planner_snapshot["data"]

        assert "planner_view" in data
        planner_view = data["planner_view"]
        pinned = planner_view["pinned_controls"]
        groups = planner_view["condensed_groups"]

        assert any(node["id"] == MOVIES_LINK_ID for node in pinned)
        assert any(node["id"] == NEXT_PAGE_ID for node in pinned)
        assert len(groups) >= 1
        assert planner_view["stats"]["total_nodes"] > len(snapshot_nodes(planner_snapshot))

        full_snapshot = run_cli("snapshot", "--session", local_session, "--headless", "--view", "full")
        assert full_snapshot["data"]["count"] > len(snapshot_nodes(planner_snapshot))
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
        next_page_after = select_node(planner_nodes, ref_type="element", element_id=NEXT_PAGE_ID)
        assert next_page_after["visibility"]["in_viewport"] is True
    finally:
        cleanup_session(local_session)
