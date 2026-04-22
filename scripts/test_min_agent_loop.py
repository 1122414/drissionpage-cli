from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.support import cleanup_session, run_cli, unique_session  # noqa: E402

DEFAULT_GOAL = "帮我去 https://www.wfei.la/，点击电影栏目，然后点击三次翻页。"
DEFAULT_MAX_STEPS = 8
DEFAULT_INSPECT_DEPTH = 2
DEFAULT_ACTION_NODE_LIMIT = 12
DEFAULT_EXPANSION_NODE_LIMIT = 20
DEFAULT_FIND_NODE_LIMIT = 3

OPENAI_CONFIG = {
    "api_key": "sk-098796cc7d5e46588c56a4d582cfc9b6",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "model": "kimi-k2.5",
}


class PlannerOutputError(RuntimeError):
    pass


class LangChainPlannerClient:
    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:  # pragma: no cover
            raise SystemExit(
                "Missing dependency 'langchain_openai'. Install it first, for example: pip install langchain-openai"
            ) from exc

        self.llm = ChatOpenAI(
            api_key=api_key,
            base_url=base_url or None,
            model=model,
            temperature=0,
            timeout=60,
        )

    def extract_goal(self, goal: str) -> dict[str, Any]:
        prompt = (
            "You are planning a browser task.\n"
            "Extract the start URL and a short execution summary from the user goal.\n"
            "Return strict JSON only.\n"
            'JSON schema: {"url": "...", "goal_summary": "..."}\n\n'
            f"Goal:\n{goal}"
        )
        return self._invoke_json(prompt, required_keys={"url", "goal_summary"})

    def choose_next_action(self, goal: str, llm_view: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
        prompt = (
            "You are controlling a browser agent through dp_cli.\n"
            "Pick the single safest next action.\n"
            "You receive a compact state view and a small list of candidate nodes.\n"
            "Use ref-driven actions whenever a matching target already appears in the current nodes list.\n"
            "Do not use find_text to reconfirm a target that already appears with a ref.\n"
            "A node can still be clicked by ref even if it is not directly interactable yet; dp_cli can scroll before clicking.\n"
            "You may return one of four actions:\n"
            '- {"kind":"click_ref","ref":"e12","text":null,"reason":"..."}\n'
            '- {"kind":"inspect_ref","ref":"r3","text":null,"reason":"..."}\n'
            '- {"kind":"find_text","ref":null,"text":"Next page","reason":"..."}\n'
            '- {"kind":"stop","ref":null,"text":null,"reason":"..."}\n'
            "Rules:\n"
            "- If the target already appears in nodes with a ref, return click_ref.\n"
            "- Use inspect_ref only for container or group nodes when you need a local expansion.\n"
            "- Use find_text only when the target does not appear anywhere in the current nodes list.\n"
            "- Never repeat a completed navigation step if the current URL or history shows it is already done.\n"
            "- If the goal is already complete, return stop.\n"
            "- Return strict JSON only.\n"
            'JSON schema: {"thought":"...", "action":{"kind":"click_ref|inspect_ref|find_text|stop","ref":null,"text":null,"reason":"..."}}\n\n'
            f"Goal:\n{goal}\n\n"
            f"LLM view:\n{json.dumps(llm_view, ensure_ascii=False, indent=2)}\n\n"
            f"History:\n{json.dumps(compact_history(history), ensure_ascii=False, indent=2)}"
        )
        result = self._invoke_json(prompt, required_keys={"thought", "action"})
        action = result.get("action")
        if not isinstance(action, dict):
            raise PlannerOutputError(f"Planner returned invalid action payload: {result}")
        required_action_keys = {"kind", "ref", "text", "reason"}
        if not required_action_keys.issubset(action):
            raise PlannerOutputError(f"Planner action is missing keys: {required_action_keys - set(action)}")
        return result

    def _invoke_json(self, prompt: str, required_keys: set[str]) -> dict[str, Any]:
        response = self.llm.invoke(prompt)
        content = getattr(response, "content", response)
        text = self._normalize_content(content)
        payload = self._parse_json_object(text)
        if not required_keys.issubset(payload):
            raise PlannerOutputError(f"Planner JSON is missing keys: {required_keys - set(payload)}")
        return payload

    def _normalize_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(parts).strip()
        return str(content).strip()

    def _parse_json_object(self, text: str) -> dict[str, Any]:
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not match:
                raise PlannerOutputError(f"Planner did not return valid JSON: {text}")
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise PlannerOutputError(f"Planner returned invalid JSON: {text}") from exc
        if not isinstance(payload, dict):
            raise PlannerOutputError(f"Planner JSON root must be an object: {payload!r}")
        return payload


def prune_empty(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            pruned = prune_empty(item)
            if pruned in ("", None, [], {}):
                continue
            cleaned[key] = pruned
        return cleaned
    if isinstance(value, list):
        cleaned_list = [prune_empty(item) for item in value]
        return [item for item in cleaned_list if item not in ("", None, [], {})]
    return value


def compact_history(history: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for item in history[-limit:]:
        compacted.append(
            prune_empty(
                {
                    "kind": item.get("kind"),
                    "ref": item.get("ref"),
                    "text": item.get("text"),
                    "reason": item.get("reason"),
                }
            )
        )
    return compacted


def state_view(snapshot_payload: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    data = snapshot_payload["data"]
    page = data.get("page") or {}
    page_identity = data.get("page_identity") or {}
    last_action = compact_history(history, limit=1)
    completed_steps = len(history)
    progress = "No actions executed yet."
    if last_action:
        summary = last_action[0]
        progress = (
            f"Completed {completed_steps} action(s). "
            f"Last action: {summary.get('kind')} "
            f"{summary.get('ref') or summary.get('text') or ''}".strip()
        )
    return prune_empty(
        {
            "url": page.get("url"),
            "title": page.get("title"),
            "page_identity": {
                "page_id": page_identity.get("page_id"),
                "snapshot_seq": page_identity.get("snapshot_seq"),
            },
            "goal_progress": progress,
            "last_action": last_action[0] if last_action else None,
        }
    )


def node_context(node: dict[str, Any]) -> dict[str, Any]:
    context = node.get("context")
    if not isinstance(context, dict):
        return {}
    return prune_empty(
        {
            "heading": context.get("heading"),
            "landmark": context.get("landmark"),
            "form": context.get("form"),
            "list": context.get("list"),
        }
    )


def compact_node(node: dict[str, Any], source: str) -> dict[str, Any]:
    visibility = node.get("visibility") or {}
    return prune_empty(
        {
            "ref": node.get("ref"),
            "ref_type": node.get("ref_type"),
            "role": node.get("role"),
            "name": node.get("name"),
            "text": node.get("text"),
            "id": node.get("id"),
            "source": source,
            "interactable_now": visibility.get("interactable_now"),
            "context": node_context(node),
        }
    )


def dedupe_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in nodes:
        ref = node.get("ref")
        if not ref or ref in seen:
            continue
        seen.add(ref)
        unique.append(node)
    return unique


def planner_action_nodes(planner_view: dict[str, Any]) -> list[dict[str, Any]]:
    pinned = [compact_node(item, "pinned") for item in planner_view.get("pinned_controls", [])[:8]]
    viewport = [compact_node(item, "viewport") for item in planner_view.get("viewport_nodes", [])[:4]]
    groups = [compact_node(item, "group") for item in planner_view.get("condensed_groups", [])[:2]]
    nodes = dedupe_nodes([*pinned, *viewport, *groups])
    return nodes[:DEFAULT_ACTION_NODE_LIMIT]


def full_snapshot_nodes(nodes: list[dict[str, Any]], *, source: str, limit: int) -> list[dict[str, Any]]:
    compacted = [compact_node(item, source) for item in nodes]
    return dedupe_nodes(compacted)[:limit]


def compact_snapshot(snapshot_payload: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    data = snapshot_payload["data"]
    llm_view = {
        "state": state_view(snapshot_payload, history),
        "root_ref": data.get("root_ref"),
    }
    if snapshot_payload.get("action") == "find":
        llm_view["nodes"] = full_snapshot_nodes(data.get("nodes") or [], source="find", limit=DEFAULT_FIND_NODE_LIMIT)
        return prune_empty(llm_view)
    if "planner_view" in data:
        llm_view["nodes"] = planner_action_nodes(data["planner_view"])
        return prune_empty(llm_view)
    llm_view["nodes"] = full_snapshot_nodes(data.get("nodes") or [], source="expansion", limit=DEFAULT_EXPANSION_NODE_LIMIT)
    return prune_empty(llm_view)


def fallback_url(goal: str) -> str | None:
    match = re.search(r"https?://[^\s]+", goal)
    if match:
        return match.group(0)
    return None


def log_step(title: str, payload: dict[str, Any]) -> None:
    print(f"[step] {title}")
    if "ok" in payload:
        print(f"  ok={payload['ok']} action={payload['action']}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def choose_top_find_ref(find_payload: dict[str, Any]) -> str:
    nodes = find_payload["data"]["nodes"]
    if not nodes:
        raise RuntimeError("find returned no nodes.")
    return nodes[0]["ref"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="test_min_agent_loop")
    parser.add_argument("--goal", default=DEFAULT_GOAL, help="Natural language browser task.")
    parser.add_argument("--session", help="Optional fixed session name.")
    parser.add_argument("--headed", action="store_false", help="Run with a visible browser window.")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS, help="Maximum planner steps.")
    parser.add_argument("--inspect-depth", type=int, default=DEFAULT_INSPECT_DEPTH, help="Depth for subtree snapshots.")
    parser.add_argument("--api-key", default=OPENAI_CONFIG["api_key"], help="API key.")
    parser.add_argument("--base-url", default=OPENAI_CONFIG["base_url"], help="OpenAI-compatible base URL.")
    parser.add_argument("--model", default=OPENAI_CONFIG["model"], help="Model name.")
    parser.add_argument("--dump-json", help="Optional output path for the final execution log.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.api_key:
        raise SystemExit("Missing API key. Fill OPENAI_CONFIG['api_key'] or pass --api-key.")
    if not args.model:
        raise SystemExit("Missing model name. Fill OPENAI_CONFIG['model'] or pass --model.")

    planner = LangChainPlannerClient(api_key=args.api_key, model=args.model, base_url=args.base_url)
    session = args.session or unique_session("agent-loop")
    history: list[dict[str, Any]] = []
    headless = not args.headed

    goal_spec = planner.extract_goal(args.goal)
    start_url = goal_spec.get("url") or fallback_url(args.goal)
    if not start_url:
        raise SystemExit("Planner did not return a usable start URL.")

    common_args = ["--session", session]
    if headless:
        common_args.append("--headless")

    results: dict[str, Any] = {
        "goal": args.goal,
        "goal_spec": goal_spec,
        "steps": [],
    }

    try:
        opened = run_cli("open", start_url, *common_args)
        results["opened"] = opened
        log_step("open", opened)

        current_snapshot = run_cli("snapshot", *common_args)
        results["initial_snapshot"] = current_snapshot
        log_step("snapshot", current_snapshot)

        for step_index in range(args.max_steps):
            llm_view = compact_snapshot(current_snapshot, history)
            decision = planner.choose_next_action(
                goal=args.goal,
                llm_view=llm_view,
                history=history,
            )
            action = decision["action"]
            print(f"[planner] step={step_index + 1} thought={decision['thought']}")
            print(json.dumps(action, ensure_ascii=False, indent=2))

            if action["kind"] == "stop":
                results["stop"] = action
                print("Agent decided to stop.")
                break

            if action["kind"] == "inspect_ref":
                target_ref = action.get("ref")
                if not target_ref:
                    raise RuntimeError("Planner chose inspect_ref without ref.")
                current_snapshot = run_cli(
                    "snapshot",
                    target_ref,
                    *common_args,
                    "--depth",
                    str(args.inspect_depth),
                    "--view",
                    "full",
                )
                history.append({"kind": "inspect_ref", "ref": target_ref, "reason": action.get("reason")})
                results["steps"].append({"planner": decision, "snapshot": current_snapshot})
                log_step(f"inspect {target_ref}", current_snapshot)
                continue

            if action["kind"] == "click_ref":
                target_ref = action.get("ref")
                if not target_ref:
                    raise RuntimeError("Planner chose click_ref without ref.")
                click_result = run_cli("click", *common_args, "--ref", target_ref)
                history.append({"kind": "click_ref", "ref": target_ref, "reason": action.get("reason")})
                current_snapshot = run_cli("snapshot", *common_args)
                results["steps"].append(
                    {
                        "planner": decision,
                        "execution": {"clicked": click_result, "ref": target_ref},
                        "snapshot": current_snapshot,
                    }
                )
                log_step(f"click ref {target_ref}", click_result)
                continue

            if action["kind"] == "find_text":
                query = action.get("text")
                if not query:
                    raise RuntimeError("Planner chose find_text without text.")
                found = run_cli("find", *common_args, "--text", query)
                nodes = found["data"]["nodes"]
                if not nodes:
                    raise RuntimeError(f"find_text returned no nodes for query={query!r}")
                history.append({"kind": "find_text", "text": query, "reason": action.get("reason")})
                if len(nodes) == 1:
                    target_ref = choose_top_find_ref(found)
                    click_result = run_cli("click", *common_args, "--ref", target_ref)
                    history.append({"kind": "click_ref", "ref": target_ref, "reason": f"Resolved from find_text={query}"})
                    current_snapshot = run_cli("snapshot", *common_args)
                    results["steps"].append(
                        {
                            "planner": decision,
                            "execution": {"find": found, "clicked": click_result, "ref": target_ref},
                            "snapshot": current_snapshot,
                        }
                    )
                    log_step(f"find+click {query}", click_result)
                    continue
                current_snapshot = found
                results["steps"].append({"planner": decision, "find": found})
                log_step(f"find {query}", found)
                continue

            raise RuntimeError(f"Unsupported planner action kind: {action['kind']}")
        else:
            raise RuntimeError("Agent loop reached max steps before stop.")

        results["final_snapshot"] = current_snapshot
        if args.dump_json:
            Path(args.dump_json).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Result JSON written to {args.dump_json}")
        print("Task agent loop finished.")
    finally:
        cleanup_session(session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
