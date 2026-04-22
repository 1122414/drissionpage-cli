from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from contextlib import closing
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dp_cli.session import SessionManager

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "site"

SEARCH_CONTAINER_ROLE = "search"
SEARCH_INPUT_ID = "search-input"
SEARCH_BUTTON_ID = "search-button"
HOT_BUTTON_ID = "hot-button"
NEXT_PAGE_ID = "next-page"
MOVIES_LINK_ID = "movies-link"
SEARCH_DONE_NAME = "Search complete"
HOT_DONE_TEXT = "Searched"


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LocalFixtureServer:
    def __init__(self) -> None:
        self.port = free_port()
        handler = partial(SimpleHTTPRequestHandler, directory=str(FIXTURE_DIR))
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/index.html"

    def __enter__(self) -> "LocalFixtureServer":
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def run_cli(*args: str, check: bool = True) -> dict:
    command = [sys.executable, "-X", "utf8", "-m", "dp_cli", *args]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"CLI command produced invalid JSON: {' '.join(command)}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        ) from exc
    if check and completed.returncode != 0:
        raise AssertionError(
            f"CLI command failed: {' '.join(command)}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        )
    payload["_returncode"] = completed.returncode
    payload["_stderr"] = stderr
    return payload


def snapshot_nodes(snapshot: dict) -> list[dict]:
    data = snapshot["data"]
    if "nodes" in data:
        return data["nodes"]
    planner_view = data.get("planner_view") or {}
    return [
        *planner_view.get("pinned_controls", []),
        *planner_view.get("viewport_nodes", []),
        *planner_view.get("condensed_groups", []),
    ]


def select_node(
    snapshot_or_nodes: dict | list[dict],
    *,
    ref_type: str | None = None,
    role: str | None = None,
    element_id: str | None = None,
    name_contains: str | None = None,
    interactable_now: bool | None = None,
) -> dict:
    nodes = snapshot_nodes(snapshot_or_nodes) if isinstance(snapshot_or_nodes, dict) else snapshot_or_nodes
    for node in nodes:
        if ref_type and node["ref_type"] != ref_type:
            continue
        if role and node["role"] != role:
            continue
        if element_id and node["id"] != element_id:
            continue
        if name_contains:
            haystack = " ".join(
                part
                for part in (
                    node.get("name") or "",
                    node.get("text") or "",
                    node.get("label") or "",
                )
                if part
            )
            if name_contains not in haystack:
                continue
        if interactable_now is not None and node["visibility"]["interactable_now"] != interactable_now:
            continue
        return node
    raise AssertionError(
        f"Could not find node ref_type={ref_type!r} role={role!r} element_id={element_id!r} "
        f"name_contains={name_contains!r} interactable_now={interactable_now!r}"
    )


def assert_search_state(nodes: list[dict], typed_text: str) -> tuple[dict, dict, dict]:
    search_button = select_node(nodes, ref_type="element", element_id=SEARCH_BUTTON_ID)
    search_input = select_node(nodes, ref_type="element", element_id=SEARCH_INPUT_ID)
    hot_button = select_node(nodes, ref_type="element", element_id=HOT_BUTTON_ID)
    assert search_button["name"] == SEARCH_DONE_NAME
    assert search_input["value"] == typed_text
    assert hot_button["text"] == HOT_DONE_TEXT
    return search_button, search_input, hot_button


def run_min_agent_loop(session: str, url: str, typed_text: str = "Agentic CLI") -> dict:
    opened = run_cli("open", url, "--session", session, "--headless")
    root_snapshot = run_cli("snapshot", "--session", session, "--headless")
    search_container = select_node(root_snapshot, ref_type="container", role=SEARCH_CONTAINER_ROLE)
    subtree_snapshot = run_cli(
        "snapshot",
        search_container["ref"],
        "--session",
        session,
        "--headless",
        "--depth",
        "3",
        "--view",
        "full",
    )
    subtree_nodes = snapshot_nodes(subtree_snapshot)
    search_input = select_node(subtree_nodes, ref_type="element", element_id=SEARCH_INPUT_ID, interactable_now=True)
    search_button = select_node(subtree_nodes, ref_type="element", element_id=SEARCH_BUTTON_ID, interactable_now=True)
    typed = run_cli("type", "--session", session, "--headless", "--ref", search_input["ref"], "--text", typed_text)
    clicked = run_cli("click", "--session", session, "--headless", "--ref", search_button["ref"])
    post_snapshot = run_cli(
        "snapshot",
        search_container["ref"],
        "--session",
        session,
        "--headless",
        "--depth",
        "3",
        "--view",
        "full",
    )
    return {
        "opened": opened,
        "root_snapshot": root_snapshot,
        "search_container": search_container,
        "subtree_snapshot": subtree_snapshot,
        "search_input": search_input,
        "search_button": search_button,
        "typed": typed,
        "clicked": clicked,
        "post_snapshot": post_snapshot,
    }


def run_local_workflow(session: str, url: str, typed_text: str = "Agentic CLI") -> dict:
    return run_min_agent_loop(session=session, url=url, typed_text=typed_text)


def run_public_smoke_workflow(session: str, url: str = "https://example.com") -> dict:
    opened = run_cli("open", url, "--session", session, "--headless")
    found = run_cli("find", "--session", session, "--headless", "--locator", "tag:a")
    ref = found["data"]["nodes"][0]["ref"]
    clicked = run_cli("click", "--session", session, "--headless", "--ref", ref)
    return {
        "opened": opened,
        "found": found,
        "ref": ref,
        "clicked": clicked,
    }


def best_text_match(nodes: list[dict], text: str) -> dict:
    target = text.strip().lower()
    ranked: list[tuple[int, dict]] = []
    for node in nodes:
        fields = [
            node.get("name") or "",
            node.get("text") or "",
            node.get("label") or "",
            node.get("id") or "",
            node.get("title") or "",
            node.get("aria_label") or "",
            node.get("context", {}).get("heading") or "",
        ]
        haystack = " ".join(fields).strip().lower()
        if not haystack or target not in haystack:
            continue
        score = 0
        if (node.get("name") or "").strip().lower() == target:
            score += 30
        if (node.get("text") or "").strip().lower() == target:
            score += 25
        if target in (node.get("name") or "").lower():
            score += 15
        if target in (node.get("text") or "").lower():
            score += 10
        if target in (node.get("label") or "").lower():
            score += 8
        if node.get("role") in {"button", "link"}:
            score += 3
        ranked.append((score, node))
    if not ranked:
        raise AssertionError(f"Could not find a matching node for text={text!r}")
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def find_element_by_text_candidates(
    session: str,
    candidates: list[str],
    *,
    headless: bool = True,
    attempts: int = 3,
    delay_seconds: float = 1.0,
) -> dict:
    last_payload: dict | None = None
    base_args = ["--session", session]
    if headless:
        base_args.append("--headless")
    for attempt in range(attempts):
        for candidate in candidates:
            payload = run_cli("find", *base_args, "--text", candidate, check=False)
            last_payload = payload
            if payload.get("ok") and payload["data"]["count"] > 0:
                node = best_text_match(payload["data"]["nodes"], candidate)
                return {"query": candidate, "found": payload, "element": node}
        if attempt < attempts - 1:
            time.sleep(delay_seconds)
    raise AssertionError(f"Could not find any candidate text in {candidates!r}. Last payload={last_payload!r}")


def click_by_text_candidates(
    session: str,
    candidates: list[str],
    *,
    description: str,
    headless: bool = True,
    attempts: int = 3,
    delay_seconds: float = 1.0,
) -> dict:
    found = find_element_by_text_candidates(
        session,
        candidates,
        headless=headless,
        attempts=attempts,
        delay_seconds=delay_seconds,
    )
    click_args = ["click", "--session", session]
    if headless:
        click_args.append("--headless")
    click_args.extend(["--ref", found["element"]["ref"]])
    clicked = run_cli(*click_args)
    return {
        "description": description,
        "query": found["query"],
        "element": found["element"],
        "found": found["found"],
        "clicked": clicked,
    }


def run_task_agent_loop(
    session: str,
    url: str,
    steps: list[dict],
    *,
    headless: bool = True,
) -> dict:
    common_args = ["--session", session]
    if headless:
        common_args.append("--headless")
    opened = run_cli("open", url, *common_args)
    initial_snapshot = run_cli("snapshot", *common_args)
    executed_steps = []
    for step in steps:
        kind = step["kind"]
        if kind == "click_text":
            executed_steps.append(
                click_by_text_candidates(
                    session,
                    [step["text"]],
                    description=step["description"],
                    headless=headless,
                    attempts=step.get("attempts", 3),
                    delay_seconds=step.get("delay_seconds", 1.0),
                )
            )
            continue
        if kind == "repeat_click_text":
            repeats = []
            for index in range(step["repeat"]):
                repeats.append(
                    click_by_text_candidates(
                        session,
                        step["candidates"],
                        description=f"{step['description']} #{index + 1}",
                        headless=headless,
                        attempts=step.get("attempts", 3),
                        delay_seconds=step.get("delay_seconds", 1.0),
                    )
                )
            executed_steps.append({"description": step["description"], "repeats": repeats})
            continue
        raise AssertionError(f"Unsupported task step kind={kind!r}")
    final_snapshot = run_cli("snapshot", *common_args)
    return {
        "opened": opened,
        "initial_snapshot": initial_snapshot,
        "steps": executed_steps,
        "final_snapshot": final_snapshot,
    }


def unique_session(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def cleanup_session(session: str) -> None:
    manager = SessionManager()
    paths = manager.session_paths(session)
    if not paths.meta_file.exists():
        return
    try:
        runtime = manager.open_runtime(session=session)
        runtime.browser.quit()
    except Exception:
        pass
    shutil.rmtree(paths.root, ignore_errors=True)
