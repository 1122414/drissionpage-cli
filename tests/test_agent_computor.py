from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_openai import ChatOpenAI

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


DEFAULT_CONFIG = {
    "api_key": os.getenv("OPENAI_API_KEY", ""),
    "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
}

# Test scenarios
SCENARIOS = {
    # 要添加新测试场景，在此字典中添加键值对：
    #   "场景名": "自然语言描述的目标"
    # 然后运行：python tests/test_agent_computor.py --scenario 场景名
    # 或使用 --dry-run 快速查看页面 index 结构而不调用 LLM
    # "automation": "打开 https://www.baidu.com，在搜索框输入'python tutorial'，点击搜索按钮",
    # "crawler_list": "访问 https://news.ycombinator.com，提取前5条新闻的标题和链接",
    # "hybrid": "去 https://www.libvio.mov/ 搜索进击的巨人，并播放第一季的第五集",
    "hybrid": "去 https://www.mtyy1.com/ 搜索进击的巨人，并播放第一季的第五集",
}


@dataclass
class AgentStep:
    step: int
    thought: str
    action: dict[str, Any]
    result: dict[str, Any] = field(default_factory=dict)
    tokens_used: int = 0
    duration_ms: float = 0.0


@dataclass
class AgentReport:
    scenario: str
    goal: str
    steps: list[AgentStep] = field(default_factory=list)
    success: bool = False
    error: str | None = None
    total_tokens: int = 0
    total_duration_ms: float = 0.0
    compression_ratio: float = 0.0
    groups_found: int = 0
    items_extracted: int = 0
    extracted_data: dict[str, Any] = field(default_factory=dict)
    full_history: list[dict[str, Any]] = field(default_factory=list)
    session_name: str = ""


class LLMClient:
    def __init__(self, api_key: str, base_url: str, model: str):
        if not api_key:
            raise ValueError("API key is required. Set OPENAI_API_KEY env var.")
        self.llm = ChatOpenAI(
            api_key=api_key,
            base_url=base_url or None,
            model=model,
            temperature=0,
            timeout=60,
        )
        self.model = model

    def invoke(self, prompt: str) -> str:
        response = self.llm.invoke(prompt)
        content = getattr(response, "content", response)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(parts).strip()
        return str(content) .strip()

    def extract_json(self, text: str) -> dict[str, Any]:
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except json.JSONDecodeError:
                    raise ValueError(f"No valid JSON found in: {text[:200]}")
            else:
                raise ValueError(f"No JSON found in: {text[:200]}")
        if not isinstance(parsed, dict):
            raise ValueError(f"Expected JSON object, got {type(parsed).__name__}: {str(parsed)[:200]}")
        return parsed


class DPCLIExecutor:
    def __init__(self, session: str = "agent-test", headless: bool = False):
        self.session = session
        self.headless = headless

    def _run(self, *args) -> dict[str, Any]:
        import subprocess
        cmd = ["python", "-m", "dp_cli", *args]
        if self.headless:
            cmd.append("--headless")
        cmd.extend(["--session", self.session])
        print(f"[DEBUG] cmd: {' '.join(cmd)}")
        result = None
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
            )
            print(f"[DEBUG] returncode: {result.returncode}")
            stdout_preview = result.stdout[:500] if result.stdout is not None else 'None'
            stderr_preview = result.stderr[:500] if result.stderr is not None else 'None'
            print(f"[DEBUG] stdout: {stdout_preview}")
            print(f"[DEBUG] stderr: {stderr_preview}")
            if result.returncode != 0:
                return {
                    "ok": False,
                    "error": result.stderr or f"Exit code {result.returncode}",
                    "stdout": result.stdout,
                }
            if result.stdout is None:
                return {"ok": False, "error": "No output from command (stdout is None)"}
            parsed = json.loads(result.stdout)
            if not isinstance(parsed, dict):
                return {"ok": False, "error": f"Expected JSON object, got {type(parsed).__name__}", "raw": result.stdout}
            return parsed
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Timeout after 30s"}
        except json.JSONDecodeError:
            return {"ok": False, "error": "Invalid JSON output", "raw": result.stdout if result else None}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open(self, url: str) -> dict[str, Any]:
        return self._run("open", url)

    def snapshot(self, mode: str = "agent_summary", ref: str | None = None, depth: int | None = None) -> dict[str, Any]:
        args = ["snapshot", "--mode", mode or "agent_summary"]
        if ref:
            args.extend([ref])
        if depth is not None:
            args.extend(["--depth", str(depth)])
        return self._run(*args)

    def expand(self, ref: str, depth: int = 2) -> dict[str, Any]:
        return self._run("expand", ref, "--depth", str(depth))

    def list_items(self, group_ref: str, sample_size: int = 3) -> dict[str, Any]:
        return self._run("list-items", group_ref, "--sample-size", str(sample_size))

    def extract(self, target_ref: str, schema: list[str] | None = None, limit: int | None = None) -> dict[str, Any]:
        args = ["extract", target_ref]
        if schema:
            args.extend(["--schema", *schema])
        if limit is not None and limit > 0:
            args.extend(["--limit", str(limit)])
        return self._run(*args)

    def find(self, text: str | None = None, locator: str | None = None) -> dict[str, Any]:
        args = ["find"]
        if text:
            args.extend(["--text", text])
        if locator:
            args.extend(["--locator", locator])
        return self._run(*args)

    def click(self, ref: str | None = None, locator: str | None = None) -> dict[str, Any]:
        args = ["click"]
        if ref:
            args.extend(["--ref", ref])
        if locator:
            args.extend(["--locator", locator])
        return self._run(*args)

    def type_text(self, ref: str, text: str) -> dict[str, Any]:
        return self._run("type", "--ref", ref, "--text", text)

    def resolve_locator(self, ref: str) -> dict[str, Any]:
        return self._run("resolve-locator", "--ref", ref)

    def eval_js(self, js: str) -> dict[str, Any]:
        return self._run("eval", js)

    def session_inspect(self) -> dict[str, Any]:
        return self._run("session", "inspect")


class DPCLIAgent:
    def __init__(self, llm: LLMClient, executor: DPCLIExecutor):
        self.llm = llm
        self.executor = executor
        self.history: list[dict[str, Any]] = []
        self.total_tokens = 0
        self.collected_items: list[dict] = []
        self.recent_actions: list[dict[str, Any]] = []

    def _record_action(self, skill: str, params: dict[str, Any], result: dict[str, Any]) -> None:
        ok = result.get("ok", False)
        self.recent_actions.append({
            "skill": skill,
            "params": self._safe_params(skill, params),
            "ok": ok,
        })
        if len(self.recent_actions) > 3:
            self.recent_actions.pop(0)

    def _is_duplicate_action(self, skill: str, params: dict[str, Any]) -> bool:
        if skill in ("snapshot", "open"):
            return False
        if not self.recent_actions:
            return False
        safe = self._safe_params(skill, params)
        if not safe:
            return False
        for prev in reversed(self.recent_actions):
            if prev.get("skill") == skill and prev.get("params") == safe:
                return True
        return False

    def _safe_params(self, skill: str, params: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(params, dict):
            return {}
        safe: dict[str, Any] = {}
        if skill in ("type", "click", "expand", "extract", "list-items") and "ref" in params:
            safe["ref"] = params["ref"]
        if skill in ("click", "find") and "locator" in params:
            safe["locator"] = params["locator"]
        if skill == "type" and "text" in params:
            safe["text"] = params["text"]
        if skill == "type" and "locator" in params:
            safe["locator"] = params["locator"]
        if skill == "open" and "url" in params:
            safe["url"] = params["url"]
        if skill == "find" and "text" in params:
            safe["text"] = params["text"]
        return safe

    def plan_goal(self, goal: str) -> dict[str, Any]:
        prompt = (
            "You are a browser automation planner. Analyze the user's goal and break it into steps.\n"
            "Return a JSON object with:\n"
            '- "url": the starting URL (extract from goal if present)\n'
            '- "task_type": "automation" | "crawler" | "hybrid"\n'
            '- "steps": list of high-level steps\n'
            '- "expected_skills": list of dp_cli skills needed (e.g., ["snapshot", "click", "extract"])\n\n'
            f"Goal: {goal}\n\n"
            "Return ONLY JSON."
        )
        try:
            text = self.llm.invoke(prompt)
            return self.llm.extract_json(text)
        except (ValueError, Exception) as e:
            return {"error": f"Failed to parse plan: {e}"}

    def decide_action(self, goal: str, current_state: dict[str, Any]) -> dict[str, Any]:
        prompt = (
            "You are controlling a browser via dp_cli v0.6.\n"
            "Your PRIMARY workflow is: snapshot → check interactable_elements → check surface_index → find → interact by ref. NEVER use 'eval' for basic typing, clicking, or searching.\n\n"
            "Available skills and their REQUIRED parameters:\n\n"
            "| Skill | Required Params | Optional Params | Description |\n"
            "|-------|----------------|-----------------|-------------|\n"
            "| open | url | - | Navigate to URL |\n"
            "| snapshot | - | mode, ref, depth | Capture page structure. Returns index with interactable_elements, surface_index, deep_index, tree |\n"
            "| expand | ref | depth | Expand container subtree (use r* refs) |\n"
            "| find | - | text, locator | Find elements by text or CSS — searches full DOM |\n"
            "| click | - | ref, locator | Click element (ALWAYS prefer ref over locator) |\n"
            "| type | ref, text | - | Type text into element |\n"
            "| extract | target_ref | schema, limit | Extract data from group/container |\n"
            "| eval | js | - | Execute JavaScript — ONLY for complex data extraction when native tools fail |\n\n"
            "MANDATORY RULES (violations cause failures):\n"
            "1. FORBIDDEN: Using 'eval' to fill inputs, click buttons, or submit forms. Use 'find' + 'type'/'click' instead.\n"
            "2. REQUIRED: After 'snapshot', inspect 'interactable_elements' first. If target is there, use its ref directly.\n"
            "3. REQUIRED: If not in interactable_elements, check 'surface_index'. If found, use its ref.\n"
            "4. REQUIRED: If not in surface_index, use 'find' with --text or --locator. find searches ALL visible elements.\n"
            "5. REQUIRED: 'type' and 'click' MUST use 'ref' param whenever possible. Only use 'locator' as last resort.\n"
            "6. If a click causes 'ref_stale' error, the page navigated — take a new snapshot immediately.\n"
            "7. ANTI-REPETITION RULE (prevents infinite loops):\n"
            "   - Definition: 'same action' = same skill AND same key params (ref, text, url, locator).\n"
            "   - If your last action was type --ref e12, your NEXT action CANNOT be type --ref e12 (even if it failed).\n"
            "   - If your last action was click --ref e13, your NEXT action CANNOT be click --ref e13.\n"
            "   - EXCEPTIONS (these ARE allowed to repeat): snapshot, open — you may snapshot multiple times.\n"
            "   - After type, your next step MUST be click (submit/search) or snapshot — NEVER type again.\n"
            "   - If an action fails (ok=false), do NOT blindly retry the same action. Try a different approach (e.g., use find instead of ref).\n\n"
            "STANDARD INTERACTION WORKFLOW (memorize this):\n"
            "Step A: snapshot → get index\n"
            "Step B: Check interactable_elements → click/type by ref if found\n"
            "Step C: Check surface_index → click/type by ref if found\n"
            "Step D: Target not visible → find --text '关键词' or find --locator 'css-selector'\n"
            "Step E: type --ref eXX --text 'keyword' → IMMEDIATELY click --ref eYY (submit/search)\n"
            "Step F: After page change → snapshot again to get fresh refs\n\n"
            "Example: To search for '进击的巨人' on a site:\n"
            "  1. snapshot → check interactable_elements for search input\n"
            "  2. Not found? check surface_index for search-related elements\n"
            "  3. Still not found? find --text '搜索' or find --locator 'input[placeholder*=搜索]'\n"
            "  4. type --ref e13 --text '进击的巨人'\n"
            "  5. IMMEDIATELY click --ref e14 (search/submit button) — do NOT type again\n"
            "  6. snapshot (page changed, get new refs)\n\n"
            "WHEN TO USE eval (rare):\n"
            "- ONLY when you need to extract complex data that 'extract' cannot handle\n"
            "- ONLY when the page has NO semantic structure and 'find' returns nothing\n"
            "- eval js MUST be a single expression (no semicolons)\n\n"
            "Example correct actions:\n"
            '- open: {"skill": "open", "params": {"url": "https://example.com"}}\n'
            '- find: {"skill": "find", "params": {"text": "搜索"}}\n'
            '- type: {"skill": "type", "params": {"ref": "e12", "text": "进击的巨人"}}\n'
            '- click: {"skill": "click", "params": {"ref": "e13"}}\n'
            '- extract: {"skill": "extract", "params": {"target_ref": "r2", "schema": ["title", "url"], "limit": 5}}\n\n'
            "recent_actions shows your last 3 actions. If the last action was type/click with ok=true, you MUST choose a DIFFERENT next action (do NOT repeat).\n\n"
            "Choose the next action based on the current state and goal.\n\n"
            'Return JSON with:\n'
            '- "thought": your reasoning (keep it short, 1-2 sentences)\n'
            '- "action": {"skill": "skill_name", "params": {...}, "reason": "..."}\n\n'
            f"Goal: {goal}\n\n"
            f"Current state:\n{json.dumps(current_state, ensure_ascii=False, indent=2)}\n\n"
            f"History:\n{json.dumps(self._compact_history(), ensure_ascii=False, indent=2)}\n\n"
            "Return ONLY valid JSON. No markdown, no extra text."
        )
        try:
            text = self.llm.invoke(prompt)
            return self.llm.extract_json(text)
        except (ValueError, Exception) as e:
            return {"error": f"Failed to parse decision: {e}"}

    def execute_skill(self, skill: str, params: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(params, dict):
            params = {}
        if skill == "open":
            url = params.get("url")
            if not url:
                return {"ok": False, "error": "open requires 'url' param (e.g., 'https://example.com')"}
            return self.executor.open(url)
        elif skill == "snapshot":
            return self.executor.snapshot(
                mode=params.get("mode") or "agent_summary",
                ref=params.get("ref"),
                depth=params.get("depth"),
            )
        elif skill == "expand":
            ref = params.get("ref")
            if not ref:
                return {"ok": False, "error": "expand requires 'ref' param (container ref like 'r1', 'r2')"}
            depth = params.get("depth")
            return self.executor.expand(ref, depth if depth is not None else 2)
        elif skill == "find":
            return self.executor.find(text=params.get("text"), locator=params.get("locator"))
        elif skill == "click":
            return self.executor.click(ref=params.get("ref"), locator=params.get("locator"))
        elif skill == "type":
            ref = params.get("ref")
            text = params.get("text")
            if not ref:
                return {"ok": False, "error": "type requires 'ref' param (element ref like 'e1', 'e5')"}
            if text is None:
                return {"ok": False, "error": "type requires 'text' param (string to type)"}
            return self.executor.type_text(ref, text)
        elif skill == "list-items":
            group_ref = params.get("group_ref")
            if not group_ref:
                return {"ok": False, "error": "list-items requires 'group_ref' param (container ref like 'r1', 'r2'). Use snapshot to find available group refs."}
            sample_size = params.get("sample_size")
            return self.executor.list_items(group_ref, sample_size if sample_size is not None else 3)
        elif skill == "extract":
            target_ref = params.get("target_ref") or params.get("ref")
            if not target_ref:
                return {"ok": False, "error": "extract requires 'target_ref' or 'ref' param (container ref like 'r1', 'r2')"}
            return self.executor.extract(
                target_ref,
                schema=params.get("schema"),
                limit=params.get("limit"),
            )
        elif skill == "resolve-locator":
            ref = params.get("ref")
            if not ref:
                return {"ok": False, "error": "resolve-locator requires 'ref' param (element or container ref)"}
            return self.executor.resolve_locator(ref)
        elif skill == "eval":
            js = params.get("js")
            if not js:
                return {"ok": False, "error": "eval requires 'js' param (JavaScript code string)"}
            return self.executor.eval_js(js)
        else:
            return {"ok": False, "error": f"Unknown skill: {skill}"}

    def _compact_history(self, limit: int = 2) -> list[dict[str, Any]]:
        compact = []
        for item in self.history[-limit:]:
            result = item.get("result", {})
            compact_item: dict[str, Any] = {
                "skill": item.get("skill"),
                "result_ok": result.get("ok"),
            }
            params = item.get("params", {})
            if params:
                compact_item["params_keys"] = list(params.keys())
            if not result.get("ok"):
                err = result.get("error", "")
                compact_item["error"] = str(err)[:120]
            elif item.get("skill") == "eval":
                data = result.get("data", {})
                js_result = data.get("result")
                if isinstance(js_result, list):
                    compact_item["result_count"] = len(js_result)
                else:
                    compact_item["result_preview"] = str(js_result)[:120]
            elif item.get("skill") == "extract":
                data = result.get("data", {})
                compact_item["items_count"] = len(data.get("items", []))
            compact.append(compact_item)
        return compact

    def print_index(self, snapshot: dict[str, Any]) -> None:
        """Print a human-readable summary of the snapshot index for debugging."""
        if not isinstance(snapshot, dict):
            print(f"Invalid snapshot: {type(snapshot).__name__}")
            return
        data = snapshot.get("data") or {}
        index = data.get("index") or {}
        stats = index.get("stats", {})
        print(f"\n--- Snapshot Index ---")
        print(f"URL: {(data.get('page') or {}).get('url')}")
        print(f"Title: {(data.get('page') or {}).get('title')}")
        print(f"Schema: {data.get('schema_version')}")
        print(f"Total nodes: {stats.get('total_nodes')}")
        print(f"Surface: {stats.get('surface_count')} | Deep: {stats.get('deep_count')}")
        print(f"In viewport: {stats.get('in_viewport')} | Offscreen: {stats.get('offscreen')}")
        print(f"Interactable now: {stats.get('interactable_now')}")
        interactable = index.get("interactable_elements", [])
        if interactable:
            print(f"\nInteractable elements ({len(interactable)}):")
            for item in interactable[:10]:
                print(f"  {item.get('ref')} {item.get('role'):12} {item.get('name', '')[:40]}")
            if len(interactable) > 10:
                print(f"  ... and {len(interactable) - 10} more")
        surface = index.get("surface_index", [])
        if surface:
            print(f"\nSurface index ({len(surface)}):")
            for item in surface[:10]:
                print(f"  {item.get('ref')} {item.get('role'):12} {item.get('name', '')[:40]} (children: {item.get('child_count', 0)})")
            if len(surface) > 10:
                print(f"  ... and {len(surface) - 10} more")
        print("---")

    def compact_state(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(snapshot, dict):
            return {"error": f"Unexpected snapshot format: {type(snapshot).__name__}", "raw": str(snapshot)[:200]}
        raw_data = snapshot.get("data")
        data = raw_data if isinstance(raw_data, dict) else {}
        raw_page = data.get("page")
        page = raw_page if isinstance(raw_page, dict) else {}
        result: dict[str, Any] = {
            "url": page.get("url"),
            "title": page.get("title"),
        }

        index = data.get("index")
        if not isinstance(index, dict):
            return result

        stats = index.get("stats", {})
        if isinstance(stats, dict):
            result["total_nodes"] = stats.get("total_nodes")
            result["surface_count"] = stats.get("surface_count")
            result["interactable_now"] = stats.get("interactable_now")

        interactable = index.get("interactable_elements", [])
        if isinstance(interactable, list) and interactable:
            result["interactable_elements"] = [
                {"ref": item.get("ref"), "role": item.get("role"), "name": item.get("name")}
                for item in interactable[:15]
                if isinstance(item, dict)
            ]

        surface = index.get("surface_index", [])
        if isinstance(surface, list) and surface:
            result["surface_index"] = [
                {
                    "ref": item.get("ref"),
                    "tag": item.get("tag"),
                    "role": item.get("role"),
                    "name": item.get("name"),
                    "child_count": item.get("child_count"),
                }
                for item in surface[:20]
                if isinstance(item, dict)
            ]

        tree = index.get("tree", {})
        if isinstance(tree, dict):
            children_map = tree.get("children_map", {})
            large_containers = [
                {"ref": ref, "child_count": len(children)}
                for ref, children in children_map.items()
                if isinstance(children, list) and len(children) > 20
            ]
        if large_containers:
            result["large_containers"] = large_containers[:5]

        if self.recent_actions:
            result["recent_actions"] = self.recent_actions[-3:]

        return result

    def run(self, goal: str, max_steps: int = 20) -> AgentReport:
        report = AgentReport(scenario="", goal=goal)
        start_time = time.time()

        try:
            # Step 0: Plan
            plan = self.plan_goal(goal)
            if plan.get("error"):
                report.error = f"Plan error: {plan['error']}"
                return report
            report.scenario = plan.get("task_type", "unknown")
            url = plan.get("url")
            if not url:
                report.error = "No URL found in goal"
                return report

            # Step 1: Open URL
            print(f"[Agent] Opening {url}...")
            result = self.executor.open(url)
            self.history.append({"skill": "open", "params": {"url": url}, "result": result})

            if not result.get("ok"):
                report.error = f"Open failed: {result.get('error')}"
                return report

            if max_steps <= 0:
                snapshot = self.executor.snapshot(mode="agent_summary")
                if snapshot.get("ok"):
                    self.print_index(snapshot)
                report.success = True
                report.total_duration_ms = (time.time() - start_time) * 1000
                return report

            # Main loop
            for step in range(1, max_steps + 1):
                step_start = time.time()

                # Get snapshot
                snapshot = self.executor.snapshot(mode="agent_summary")
                if not snapshot.get("ok"):
                    snap_err = snapshot.get("error")
                    if isinstance(snap_err, dict):
                        err_msg = f"{snap_err.get('code', '')}: {snap_err.get('message', '')}"
                    elif isinstance(snap_err, str):
                        err_msg = snap_err
                    else:
                        err_msg = str(snap_err)
                    print(f"[Agent] Snapshot failed: {err_msg}")
                    report.error = f"Snapshot failed: {err_msg}"
                    break
                state = self.compact_state(snapshot)

                # Decide action
                decision = self.decide_action(goal, state)
                if decision.get("error"):
                    error_msg = f"Decision error: {decision['error']}"
                    print(f"[Agent] {error_msg}")
                    report.error = error_msg
                    break

                action = decision.get("action")
                if not isinstance(action, dict):
                    error_msg = f"Invalid action: expected dict, got {type(action).__name__ if action is not None else 'None'}"
                    print(f"[Agent] {error_msg}")
                    report.error = error_msg
                    break

                skill = action.get("skill", "stop")

                if skill == "stop":
                    reason = str(action.get("reason", "Goal complete"))
                    print(f"[Agent] Stopping: {reason}")
                    failure_keywords = ["fail", "error", "unable", "cannot", "impossible", "gave up"]
                    is_failure = any(kw in reason.lower() for kw in failure_keywords)
                    if is_failure:
                        report.error = reason
                    report.success = not is_failure
                    break

                print(f"[Agent] Step {step}: {skill} - {action.get('reason', '')}")
                params = action.get("params") or {}
                if self._is_duplicate_action(skill, params):
                    print(f"[Agent] BLOCKED duplicate action: {skill} with same params. Forcing snapshot to refresh state.")
                    result = self.executor.snapshot(mode="agent_summary")
                    skill = "snapshot"
                    params = {"mode": "agent_summary"}
                else:
                    result = self.execute_skill(skill, params)
                self._record_action(skill, params, result)

                err = result.get("error")
                if isinstance(err, dict):
                    error_str = f"{err.get('code', '')} {err.get('message', '')}"
                elif isinstance(err, str):
                    error_str = err
                else:
                    error_str = ""

                if not result.get("ok") and "requires" in error_str:
                    print(f"[Agent] Parameter error: {result.get('error')}")
                    agent_step = AgentStep(
                        step=step,
                        thought=decision.get("thought", ""),
                        action=action,
                        result=result,
                        duration_ms=(time.time() - step_start) * 1000,
                    )
                    report.steps.append(agent_step)
                    self.history.append({"skill": skill, "params": action.get("params") or {}, "result": result})
                    continue

                recoverable_errors = ["ref_stale", "ref_not_found", "element_not_found", "invalid_ref_type"]
                needs_recovery = not result.get("ok") and any(code in error_str for code in recoverable_errors)

                if needs_recovery:
                    print(f"[Agent] Recoverable error: {result.get('error')}. Refreshing snapshot for re-decision...")
                    print(f"[Agent] Recoverable error: {result.get('error')}. Will retry with fresh snapshot...")
                    agent_step = AgentStep(
                        step=step,
                        thought=decision.get("thought", ""),
                        action=action,
                        result=result,
                        duration_ms=(time.time() - step_start) * 1000,
                    )
                    report.steps.append(agent_step)
                    self.history.append({"skill": skill, "params": action.get("params") or {}, "result": result})
                    continue

                # Record
                agent_step = AgentStep(
                    step=step,
                    thought=decision.get("thought", ""),
                    action=action,
                    result=result,
                    duration_ms=(time.time() - step_start) * 1000,
                )
                report.steps.append(agent_step)
                self.history.append({"skill": skill, "params": action.get("params") or {}, "result": result})

                # Analyze result
                if skill == "extract" and result.get("ok"):
                    data = result.get("data") or {}
                    items = data.get("items", []) if isinstance(data, dict) else []
                    report.items_extracted = len(items)
                    report.extracted_data = data if isinstance(data, dict) else {}
                    if items:
                        report.success = True
                        break

                if skill == "eval" and result.get("ok"):
                    data = result.get("data") or {}
                    js_result = data.get("result")
                    if isinstance(js_result, list) and js_result:
                        new_items = [item for item in js_result if isinstance(item, dict)]
                        self.collected_items.extend(new_items)
                        report.items_extracted = len(self.collected_items)
                        print(f"[Agent] Collected {len(new_items)} items (total: {len(self.collected_items)})")

                if skill == "click" and result.get("ok"):
                    pass

            else:
                if self.collected_items:
                    report.success = True
                else:
                    report.error = "Max steps reached"

        except Exception as e:
            report.error = str(e)
            import traceback
            traceback.print_exc()

        if self.collected_items:
            report.extracted_data = {"items": self.collected_items}
            report.items_extracted = len(self.collected_items)
            report.success = True

        report.total_duration_ms = (time.time() - start_time) * 1000
        report.full_history = list(self.history)
        return report


class TestRunner:
    def __init__(self, api_key: str, base_url: str, model: str, headless: bool = False):
        self.llm = LLMClient(api_key, base_url, model)
        self.results: list[AgentReport] = []
        self.headless = headless

    def run_scenario(self, name: str, goal: str, max_steps: int = 20) -> AgentReport:
        print(f"\n{'='*60}")
        print(f"Scenario: {name}")
        print(f"Goal: {goal}")
        print(f"{'='*60}")

        executor = DPCLIExecutor(session=f"test-{name}", headless=self.headless)
        agent = DPCLIAgent(self.llm, executor)

        report = agent.run(goal, max_steps=max_steps)
        report.session_name = name
        self.results.append(report)

        # Print summary
        print(f"\n[Result] Success: {report.success}")
        print(f"[Result] Steps: {len(report.steps)}")
        print(f"[Result] Duration: {report.total_duration_ms:.0f}ms")
        if report.error:
            print(f"[Result] Error: {report.error}")
        if report.items_extracted > 0:
            print(f"[Result] Items extracted: {report.items_extracted}")

        if report.extracted_data:
            output_file = f"extracted_{name}_{int(time.time())}.json"
            Path(output_file).write_text(
                json.dumps(report.extracted_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"[Result] Extracted data saved to: {output_file}")

        log_path = self._save_execution_log(name, report)
        print(f"[Result] Execution log saved to: {log_path}")

        return report

    def _save_execution_log(self, name: str, report: AgentReport) -> str:
        log_dir = Path("log")
        log_dir.mkdir(exist_ok=True)

        existing = list(log_dir.glob(f"{name}_*.json"))
        max_index = 0
        for f in existing:
            try:
                idx = int(f.stem.rsplit("_", 1)[-1])
                max_index = max(max_index, idx)
            except ValueError:
                continue

        log_path = log_dir / f"{name}_{max_index + 1}.json"

        log_data = {
            "session_name": name,
            "scenario": report.scenario,
            "goal": report.goal,
            "success": report.success,
            "error": report.error,
            "total_duration_ms": report.total_duration_ms,
            "items_extracted": report.items_extracted,
            "steps": [
                {
                    "step": s.step,
                    "thought": s.thought,
                    "action": s.action,
                    "result": s.result,
                    "tokens_used": s.tokens_used,
                    "duration_ms": s.duration_ms,
                }
                for s in report.steps
            ],
            "full_history": report.full_history,
            "extracted_data": report.extracted_data,
        }

        log_path.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(log_path)

    def print_summary(self):
        print(f"\n{'='*60}")
        print("TEST SUMMARY")
        print(f"{'='*60}")

        for report in self.results:
            status = "PASS" if report.success else "FAIL"
            print(f"\n{status}: {report.scenario}")
            print(f"  Goal: {report.goal[:60]}...")
            print(f"  Steps: {len(report.steps)}")
            print(f"  Duration: {report.total_duration_ms:.0f}ms")
            if report.error:
                print(f"  Error: {report.error}")
            if report.items_extracted > 0:
                print(f"  Items extracted: {report.items_extracted}")

        total = len(self.results)
        passed = sum(1 for r in self.results if r.success)
        print(f"\nOverall: {passed}/{total} scenarios passed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="test_agent_computor",
        description="Test dp_cli v0.6 capabilities with natural language commands via LangChain + OpenAI",
    )
    parser.add_argument("--api-key", default=DEFAULT_CONFIG["api_key"], help="OpenAI API key")
    parser.add_argument("--base-url", default=DEFAULT_CONFIG["base_url"], help="OpenAI-compatible base URL")
    parser.add_argument("--model", default=DEFAULT_CONFIG["model"], help="Model name")
    parser.add_argument("--scenario", choices=list(SCENARIOS.keys()), help="Run a specific scenario")
    parser.add_argument("--goal", help="Custom natural language goal")
    parser.add_argument("--max-steps", type=int, default=10, help="Max steps per scenario")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--output", help="Save results to JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Open URL and print snapshot index, then exit without LLM")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.dry_run:
        goal = args.goal or SCENARIOS.get(args.scenario, "")
        if not goal:
            print("Error: --dry-run requires --goal or --scenario")
            return 1
        import re
        url_match = re.search(r'https?://[^\s，。]+', goal)
        url = url_match.group(0) if url_match else None
        if not url:
            print("Error: Could not extract URL from goal")
            return 1
        executor = DPCLIExecutor(session="dry-run", headless=args.headless)
        open_result = executor.open(url)
        if not open_result.get("ok"):
            print(f"Open failed: {open_result.get('error')}")
            return 1
        snapshot = executor.snapshot(mode="agent_summary")
        if not snapshot.get("ok"):
            print(f"Snapshot failed: {snapshot.get('error')}")
            return 1
        agent = DPCLIAgent(llm=None, executor=executor)
        agent.print_index(snapshot)
        compact = agent.compact_state(snapshot)
        print(f"\nCompact state keys: {list(compact.keys())}")
        return 0

    if not args.api_key:
        print("Error: API key required. Set OPENAI_API_KEY or pass --api-key")
        return 1

    runner = TestRunner(
        api_key=args.api_key,
        base_url=args.base_url,
        model=args.model,
        headless=args.headless,
    )

    if args.goal:
        runner.run_scenario("custom", args.goal, args.max_steps)
    elif args.scenario:
        goal = SCENARIOS[args.scenario]
        runner.run_scenario(args.scenario, goal, args.max_steps)
    else:
        for name, goal in SCENARIOS.items():
            runner.run_scenario(name, goal, args.max_steps)

    runner.print_summary()

    # Save results
    if args.output:
        results_data = []
        for report in runner.results:
            results_data.append({
                "scenario": report.scenario,
                "goal": report.goal,
                "success": report.success,
                "error": report.error,
                "steps_count": len(report.steps),
                "total_duration_ms": report.total_duration_ms,
                "items_extracted": report.items_extracted,
                "extracted_data": report.extracted_data,
                "steps": [
                    {
                        "step": s.step,
                        "thought": s.thought,
                        "action": s.action,
                        "result_ok": s.result.get("ok"),
                        "duration_ms": s.duration_ms,
                    }
                    for s in report.steps
                ],
            })
        Path(args.output).write_text(
            json.dumps(results_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nResults saved to {args.output}")

    return 0


def test_parameter_validation():
    from unittest.mock import MagicMock

    mock_executor = MagicMock()
    agent = DPCLIAgent(llm=MagicMock(), executor=mock_executor)

    result = agent.execute_skill("list-items", {})
    assert result["ok"] is False
    assert "group_ref" in result["error"]

    result = agent.execute_skill("open", {})
    assert result["ok"] is False
    assert "url" in result["error"]

    result = agent.execute_skill("type", {"text": "hello"})
    assert result["ok"] is False
    assert "ref" in result["error"]

    result = agent.execute_skill("type", {"ref": "e1"})
    assert result["ok"] is False
    assert "text" in result["error"]

    result = agent.execute_skill("resolve-locator", {})
    assert result["ok"] is False
    assert "ref" in result["error"]

    result = agent.execute_skill("eval", {})
    assert result["ok"] is False
    assert "js" in result["error"]

    print("All parameter validation tests passed!")


def test_extract_json_validation():
    llm = LLMClient(api_key="test-key", base_url="", model="test")

    result = llm.extract_json('{"skill": "open", "url": "https://example.com"}')
    assert result == {"skill": "open", "url": "https://example.com"}

    result = llm.extract_json('```json\n{"skill": "open"}\n```')
    assert result == {"skill": "open"}

    try:
        llm.extract_json("not json")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "No JSON found" in str(e)

    try:
        llm.extract_json('["array", "not", "dict"]')
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Expected JSON object" in str(e)

    try:
        llm.extract_json('"just a string"')
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Expected JSON object" in str(e)

    try:
        llm.extract_json('123')
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Expected JSON object" in str(e)

    try:
        llm.extract_json('null')
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Expected JSON object" in str(e)

    print("All extract_json validation tests passed!")


def test_params_null_handling():
    from unittest.mock import MagicMock

    mock_executor = MagicMock()
    agent = DPCLIAgent(llm=MagicMock(), executor=mock_executor)

    result = agent.execute_skill("open", None)
    assert result["ok"] is False
    assert "url" in result["error"]
    print("  PASSED: open with params=None")

    result = agent.execute_skill("expand", {"ref": "r1", "depth": None})
    mock_executor.expand.assert_called_with("r1", 2)
    print("  PASSED: expand with depth=null defaults to 2")

    result = agent.execute_skill("list-items", {"group_ref": "r1", "sample_size": None})
    mock_executor.list_items.assert_called_with("r1", 3)
    print("  PASSED: list-items with sample_size=null defaults to 3")

    print("All params null handling tests passed!")


def test_structured_error_handling():
    from unittest.mock import MagicMock

    mock_executor = MagicMock()
    mock_executor.find.return_value = {
        "ok": False,
        "error": {"code": "ref_stale", "message": "Ref is stale", "details": {"ref": "e1"}}
    }
    agent = DPCLIAgent(llm=MagicMock(), executor=mock_executor)

    result = agent.execute_skill("find", {"text": "test"})
    assert result["ok"] is False
    error = result.get("error")
    assert isinstance(error, dict)
    assert error["code"] == "ref_stale"
    print("  PASSED: structured error dict preserved")

    print("All structured error handling tests passed!")


def test_compact_state_non_dict():
    agent = DPCLIAgent(llm=None, executor=None)
    result = agent.compact_state(["not", "a", "dict"])
    assert "error" in result
    assert "Unexpected snapshot format" in result["error"]
    print("  PASSED: compact_state handles non-dict snapshot")

    result = agent.compact_state({"ok": False, "data": None, "error": "browser crashed"})
    assert result.get("url") is None
    assert result.get("mode") is None
    print("  PASSED: compact_state handles data:null")

    result = agent.compact_state({
        "ok": True,
        "data": {
            "page": None,
            "summary": None,
            "groups": None,
            "recovery": None,
        }
    })
    assert result.get("url") is None
    assert "summary" not in result
    assert "groups" not in result
    assert "recovery" not in result
    print("  PASSED: compact_state handles nested nulls")

    print("All compact_state guard tests passed!")


def test_is_duplicate_action():
    from unittest.mock import MagicMock

    agent = DPCLIAgent(llm=MagicMock(), executor=MagicMock())

    # Empty recent_actions
    assert agent._is_duplicate_action("type", {"ref": "e12", "text": "hello"}) is False
    print("  PASSED: empty history is not duplicate")

    # Same action repeated
    agent._record_action("type", {"ref": "e12", "text": "hello"}, {"ok": True})
    assert agent._is_duplicate_action("type", {"ref": "e12", "text": "hello"}) is True
    print("  PASSED: same type action is duplicate")

    # Different text
    assert agent._is_duplicate_action("type", {"ref": "e12", "text": "world"}) is False
    print("  PASSED: different text is not duplicate")

    # Different ref
    assert agent._is_duplicate_action("type", {"ref": "e13", "text": "hello"}) is False
    print("  PASSED: different ref is not duplicate")

    # Different skill
    assert agent._is_duplicate_action("click", {"ref": "e12"}) is False
    print("  PASSED: different skill is not duplicate")

    # Snapshot is exempt
    agent._record_action("snapshot", {"mode": "agent_summary"}, {"ok": True})
    assert agent._is_duplicate_action("snapshot", {"mode": "agent_summary"}) is False
    print("  PASSED: snapshot is exempt")

    # Open is exempt
    assert agent._is_duplicate_action("open", {"url": "https://example.com"}) is False
    print("  PASSED: open is exempt")

    # Locator-only click is not duplicate if locator differs
    agent.recent_actions.clear()
    agent._record_action("click", {"locator": "#btn1"}, {"ok": True})
    assert agent._is_duplicate_action("click", {"locator": "#btn2"}) is False
    print("  PASSED: different locator is not duplicate")

    # Same locator is duplicate
    assert agent._is_duplicate_action("click", {"locator": "#btn1"}) is True
    print("  PASSED: same locator is duplicate")

    # Type with same text but different locator is not duplicate
    agent.recent_actions.clear()
    agent._record_action("type", {"locator": "#inputA", "text": "hello"}, {"ok": True})
    assert agent._is_duplicate_action("type", {"locator": "#inputB", "text": "hello"}) is False
    print("  PASSED: type with same text but different locator is not duplicate")

    # Type with same locator is duplicate
    assert agent._is_duplicate_action("type", {"locator": "#inputA", "text": "world"}) is True
    print("  PASSED: type with same locator is duplicate")

    # Bypass via snapshot in between (should still detect across last 3)
    agent.recent_actions.clear()
    agent._record_action("type", {"ref": "e12", "text": "hello"}, {"ok": True})
    agent._record_action("snapshot", {"mode": "agent_summary"}, {"ok": True})
    assert agent._is_duplicate_action("type", {"ref": "e12", "text": "hello"}) is True
    print("  PASSED: type after snapshot is still duplicate")

    print("All _is_duplicate_action tests passed!")


if __name__ == "__main__":
    test_parameter_validation()
    test_extract_json_validation()
    test_params_null_handling()
    test_structured_error_handling()
    test_compact_state_non_dict()
    raise SystemExit(main())
