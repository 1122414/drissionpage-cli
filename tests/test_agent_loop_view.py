from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "test_min_agent_loop.py"


def load_agent_loop_module():
    spec = importlib.util.spec_from_file_location("test_min_agent_loop_script", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Could not load script module from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_no_empty_fields(value):
    if isinstance(value, dict):
        for key, item in value.items():
            assert item not in ("", None, [], {}), f"Found empty field at key={key!r}"
            assert_no_empty_fields(item)
    elif isinstance(value, list):
        for item in value:
            assert item not in ("", None, [], {}), "Found empty item in list"
            assert_no_empty_fields(item)


def test_compact_snapshot_builds_small_llm_view_without_raw_planner_groups():
    module = load_agent_loop_module()
    planner_payload = {
        "action": "snapshot",
        "data": {
            "mode": "semantic",
            "scope": "page",
            "page": {"url": "https://www.wfei.la/vod-show-id-3.html", "title": "Movies"},
            "page_identity": {"page_id": "page-1", "snapshot_seq": 3},
            "root_ref": None,
            "planner_view": {
                "pinned_controls": [
                    {
                        "ref": "e150",
                        "ref_type": "element",
                        "role": "link",
                        "name": "电影",
                        "text": "电影",
                        "id": "movies-link",
                        "visibility": {"interactable_now": True, "in_viewport": True},
                        "title": "",
                        "context": {"landmark": "navigation", "heading": "", "list": ""},
                    },
                    {
                        "ref": "e333",
                        "ref_type": "element",
                        "role": "link",
                        "name": "下一页",
                        "text": "下一页",
                        "id": "next-page",
                        "visibility": {"interactable_now": False, "in_viewport": False},
                        "title": "",
                        "context": {"list": "pagination"},
                    },
                ],
                "viewport_nodes": [
                    {
                        "ref": "e401",
                        "ref_type": "element",
                        "role": "button",
                        "name": "热门",
                        "text": "热门",
                        "id": "hot-button",
                        "visibility": {"interactable_now": True, "in_viewport": True},
                        "context": {},
                    }
                ],
                "condensed_groups": [
                    {
                        "ref": "r9",
                        "ref_type": "container",
                        "role": "list",
                        "name": "Movie cards",
                        "text": "",
                        "id": "",
                        "visibility": {"interactable_now": False, "in_viewport": False},
                        "child_count": 24,
                        "context": {"heading": "Popular movies"},
                    }
                ],
                "stats": {"total_nodes": 197},
                "omitted_summary": {"omitted_node_count": 101},
            },
        },
    }

    llm_view = module.compact_snapshot(
        planner_payload,
        history=[{"kind": "click_ref", "ref": "e150", "reason": "Already entered the movies page"}],
    )

    assert "planner_view" not in llm_view
    assert llm_view["state"]["url"] == "https://www.wfei.la/vod-show-id-3.html"
    assert len(llm_view["nodes"]) <= module.DEFAULT_ACTION_NODE_LIMIT
    assert any(node.get("ref") == "e150" for node in llm_view["nodes"])
    assert any(node.get("ref") == "e333" for node in llm_view["nodes"])
    assert all("source" in node for node in llm_view["nodes"])
    assert_no_empty_fields(llm_view)


def test_compact_snapshot_limits_find_results_and_prunes_empty_fields():
    module = load_agent_loop_module()
    find_payload = {
        "action": "find",
        "data": {
            "page": {"url": "https://www.wfei.la/vod-show-id-3-page-3.html", "title": "Movies page 3"},
            "page_identity": {"page_id": "page-3", "snapshot_seq": 8},
            "nodes": [
                {
                    "ref": "e721",
                    "ref_type": "element",
                    "role": "link",
                    "name": "下一页",
                    "text": "下一页",
                    "id": "next-page",
                    "visibility": {"interactable_now": False, "in_viewport": False},
                    "context": {"list": "pagination"},
                    "title": "",
                    "aria_label": "",
                },
                {
                    "ref": "e722",
                    "ref_type": "element",
                    "role": "link",
                    "name": "下一页",
                    "text": "下一页",
                    "id": "another-next-page",
                    "visibility": {"interactable_now": True, "in_viewport": True},
                    "context": {"list": "pagination"},
                    "placeholder": "",
                },
                {
                    "ref": "e723",
                    "ref_type": "element",
                    "role": "link",
                    "name": "尾页",
                    "text": "尾页",
                    "id": "last-page",
                    "visibility": {"interactable_now": True, "in_viewport": True},
                    "context": {"list": "pagination"},
                },
                {
                    "ref": "e724",
                    "ref_type": "element",
                    "role": "link",
                    "name": "首页",
                    "text": "首页",
                    "id": "first-page",
                    "visibility": {"interactable_now": True, "in_viewport": True},
                    "context": {"list": "pagination"},
                },
            ],
        },
    }

    llm_view = module.compact_snapshot(find_payload, history=[])

    assert len(llm_view["nodes"]) == module.DEFAULT_FIND_NODE_LIMIT
    assert all(node.get("source") == "find" for node in llm_view["nodes"])
    assert all(node.get("ref") for node in llm_view["nodes"])
    assert_no_empty_fields(llm_view)
