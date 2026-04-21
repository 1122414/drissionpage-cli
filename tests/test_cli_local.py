from __future__ import annotations

import json

from drissionpage_cli.session import SessionManager
from tests.support import cleanup_session, run_cli


def test_local_cli_workflow(local_fixture_server, local_session):
    try:
        opened = run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")
        assert opened["ok"] is True
        assert opened["data"]["page"]["title"] == "DrissionPage CLI Fixture"

        snapshot = run_cli("snapshot", "--session", local_session, "--headless")
        assert snapshot["ok"] is True
        assert snapshot["data"]["count"] >= 4
        assert snapshot["data"]["page_identity"]["runtime_id"]
        assert snapshot["data"]["page_identity"]["page_id"]
        assert snapshot["data"]["page_identity"]["snapshot_id"]

        button_match = run_cli("find", "--session", local_session, "--headless", "--text", "Primary Action")
        assert button_match["ok"] is True
        button_ref = button_match["data"]["elements"][0]["ref"]
        assert button_match["data"]["elements"][0]["runtime_id"] == button_match["data"]["page_identity"]["runtime_id"]
        assert button_match["data"]["elements"][0]["page_id"] == button_match["data"]["page_identity"]["page_id"]

        input_match = run_cli("find", "--session", local_session, "--headless", "--locator", "#name-input")
        assert input_match["ok"] is True
        input_ref = input_match["data"]["elements"][0]["ref"]

        typed = run_cli("type", "--session", local_session, "--headless", "--ref", input_ref, "--text", "Agentic CLI")
        assert typed["ok"] is True

        clicked = run_cli("click", "--session", local_session, "--headless", "--ref", button_ref)
        assert clicked["ok"] is True

        final_snapshot = run_cli("snapshot", "--session", local_session, "--headless")
        elements = final_snapshot["data"]["elements"]
        button = next(item for item in elements if item["id"] == "primary-action")
        text_input = next(item for item in elements if item["id"] == "name-input")
        assert button["text"] == "Clicked 1"
        assert text_input["value"] == "Agentic CLI"
    finally:
        cleanup_session(local_session)


def test_ref_becomes_stale_after_page_changes(local_fixture_server, local_session):
    try:
        opened = run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")
        assert opened["ok"] is True

        found = run_cli("find", "--session", local_session, "--headless", "--text", "Primary Action")
        ref = found["data"]["elements"][0]["ref"]

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
        assert reopened["data"]["page"]["title"] == "DrissionPage CLI Fixture"
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

        clicked = run_cli("find", "--session", local_session, "--headless", "--text", "Primary Action")
        assert clicked["ok"] is True

        after = json.loads(meta_path.read_text(encoding="utf-8"))
        assert after["headless"] is False
    finally:
        cleanup_session(local_session)


def test_session_inspect_returns_agent_friendly_identity(local_fixture_server, local_session):
    try:
        opened = run_cli("open", local_fixture_server.url, "--session", local_session, "--headless")
        assert opened["ok"] is True

        inspected = run_cli("session", "inspect", "--session", local_session, "--headless")
        assert inspected["ok"] is True
        data = inspected["data"]
        assert data["session_name"] == local_session
        assert data["session_id"]
        assert data["runtime"]["runtime_id"]
        assert data["runtime"]["status"] == "running"
        assert data["page"]["page_id"]
        assert data["page"]["url"] == local_fixture_server.url
    finally:
        cleanup_session(local_session)
