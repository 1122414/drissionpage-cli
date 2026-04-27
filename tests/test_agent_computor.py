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


MAX_STEPS = 50
LLM_TIMEOUT = 60
SUBPROCESS_TIMEOUT = 60
LLM_TEMPERATURE = 0
COMPACT_HISTORY_LIMIT = 10
LAST_RESULTS_LIMIT = 10
RECENT_ACTIONS_LIMIT = 10
DEFAULT_EXPAND_DEPTH = 10
DEFAULT_SAMPLE_SIZE = 10

ENABLE_DUPLICATE_ACTION_BLOCKING = False

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
    # "hybrid": "去 https://www.libvio.life/ 搜索进击的巨人，并播放第一季的第五集",
    # "hybrid": "去 https://www.mtyy1.com/ 搜索进击的巨人，并播放第一季的第五集",

    # 注册已经成功，register_13
    # "register": "去 https://miankoutupian.com/image/free_copy_right 注册一个账号，昵称：yyyyb，手机号：17827062314；密码：Aa123456"

    # 成功，scrawl_5.json
    # "scrawl": "去这个网站，https://www.wangfei.la/，进入左侧电影栏目，爬取前两页的电影信息，并存储为json文件",
    "scrawl_info": "去这个网站，https://www.wangfei.la/，进入左侧电影栏目，爬取前两页的电影信息，注意要点进每一部电影去获取其详情信息，并存储为json文件"
    
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
            temperature=LLM_TEMPERATURE,
            timeout=LLM_TIMEOUT,
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
                timeout=SUBPROCESS_TIMEOUT,
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
            return {"ok": False, "error": f"Timeout after {SUBPROCESS_TIMEOUT}s"}
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
        self.extracted_pages = 0
        self.extracted_keys: set[str] = set()
        self.last_extracted_page_url: str | None = None
        self.target_pages = 1
        self.recent_actions: list[dict[str, Any]] = []
        self.last_results: list[dict[str, Any]] = []

    def _record_result(self, skill: str, result: dict[str, Any]) -> None:
        if not result.get("ok"):
            return
        data = result.get("data") or {}
        record: dict[str, Any] = {"skill": skill}
        if skill == "find":
            nodes = data.get("nodes", [])
            record["count"] = data.get("count", 0)
            record["nodes"] = [
                {"ref": n.get("ref"), "role": n.get("role"), "name": n.get("name")}
                for n in nodes[:5]
                if isinstance(n, dict)
            ]
        elif skill == "click":
            record["target"] = data.get("target", {})
            target_state = data.get("target_state")
            if isinstance(target_state, dict):
                record["target_state"] = {
                    key: target_state.get(key)
                    for key in ("checked", "selected", "expanded", "value", "visible", "in_viewport", "interactable_now")
                    if key in target_state
                }
        elif skill == "type":
            record["typed_text"] = data.get("typed_text", "")
            record["target"] = data.get("target", {})
            target_state = data.get("target_state")
            if isinstance(target_state, dict):
                record["target_state"] = {
                    key: target_state.get(key)
                    for key in ("checked", "selected", "expanded", "value", "visible", "in_viewport", "interactable_now")
                    if key in target_state
                }
        elif skill == "extract":
            items = data.get("items", [])
            record["item_count"] = len(items)
            record["fields"] = data.get("fields", [])
        elif skill == "eval":
            record["result_preview"] = str(data.get("result", ""))[:120]
        elif skill == "snapshot":
            record["url"] = (data.get("page") or {}).get("url")
            record["title"] = (data.get("page") or {}).get("title")
        if len(record) > 1:
            self.last_results.append(record)
            if len(self.last_results) > LAST_RESULTS_LIMIT:
                self.last_results.pop(0)

    def _record_action(self, skill: str, params: dict[str, Any], result: dict[str, Any]) -> None:
        ok = result.get("ok", False)
        self.recent_actions.append({
            "skill": skill,
            "params": self._safe_params(skill, params),
            "ok": ok,
        })
        if len(self.recent_actions) > RECENT_ACTIONS_LIMIT:
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
        if skill == "extract" and "target_ref" in params:
            safe["target_ref"] = params["target_ref"]
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
            "CRAWLER / EXTRACTION RULES:\n"
            "- current_state may contain data_regions. These are the preferred containers for list extraction.\n"
            "- If data_regions is present, use extract with data_regions[0].ref and schema [\"title\", \"url\"] before expanding/clicking unrelated navigation containers.\n"
            "- Do not extract from sidebar, category, filter, ranking, or navigation containers when a data_region exists.\n"
            "- For goals asking for multiple pages, extract the current list page, navigate to the next page, then extract again. Do not stop after the first successful extract unless extraction_progress.pages_extracted has reached extraction_progress.target_pages.\n"
            "- If you need the next page control and it is not visible, use find with text \"next\" or the site's next-page label, then click the returned link/button.\n\n"
            "REGISTRATION FORM RULES:\n"
            "- The main loop already gives you a fresh page-level snapshot before every decision. Do NOT choose page-level snapshot just to confirm a prior click/type; use current_state and last_results instead.\n"
            "- Use field names, placeholder, label, input_type, and filled/checked state to map fields. Never type a password into a verification/captcha/code field.\n"
            "- If last_results shows a successful checkbox click with target_state.checked=true, treat that checkbox as checked and move on.\n"
            "- If an agreement/terms/privacy checkbox is visible and unchecked, click it before requesting a verification code or final submit.\n"
            "- If a phone/SMS verification button such as '获取验证码', '发送验证码', or 'Get code' is visible, click it after the phone field is filled and the agreement checkbox has been handled.\n"
            "- NEVER invent or guess a verification/SMS/captcha code. Do not type dummy codes such as 123456, 000000, 111111, or test codes unless the user explicitly provided that exact code in the goal.\n"
            "- If the verification code is not provided and the get-code button has already been clicked, stop and report that external verification is required.\n"
            "- Submit/register only after required text fields are filled, required checkbox is checked, and any visible verification-code step has been handled.\n\n"
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
            "recent_actions shows your last 3 actions. If the last action was type/click with ok=true, you MUST choose a DIFFERENT next action (do NOT repeat).\n"
            "last_results shows the outcomes of your last 5 successful operations. If you previously used find and got results, those refs are still valid — you do NOT need to find again. Use the refs from last_results directly.\n\n"
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
            return self.executor.expand(ref, depth if depth is not None else DEFAULT_EXPAND_DEPTH)
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
            return self.executor.list_items(group_ref, sample_size if sample_size is not None else DEFAULT_SAMPLE_SIZE)
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

    def _compact_history(self, limit: int = COMPACT_HISTORY_LIMIT) -> list[dict[str, Any]]:
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
                self._compact_node_for_prompt(item)
                for item in interactable[:15]
                if isinstance(item, dict)
            ]

        surface = index.get("surface_index", [])
        if isinstance(surface, list) and surface:
            result["surface_index"] = [
                self._compact_node_for_prompt(item, include_child_count=True)
                for item in surface[:20]
                if isinstance(item, dict)
            ]

        data_regions = index.get("data_regions", [])
        if isinstance(data_regions, list) and data_regions:
            result["data_regions"] = [
                self._compact_node_for_prompt(item, include_child_count=True)
                for item in data_regions[:5]
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

        if self.last_results:
            result["last_results"] = self.last_results[-5:]

        if self.target_pages > 1 or self.extracted_pages or self.collected_items:
            result["extraction_progress"] = {
                "target_pages": self.target_pages,
                "pages_extracted": self.extracted_pages,
                "items_collected": len(self.collected_items),
                "last_extracted_page_url": self.last_extracted_page_url,
            }

        return result

    def _compact_node_for_prompt(self, item: dict[str, Any], include_child_count: bool = False) -> dict[str, Any]:
        compact: dict[str, Any] = {
            "ref": item.get("ref"),
            "tag": item.get("tag"),
            "role": item.get("role"),
            "name": item.get("name"),
        }
        for key in ("text", "placeholder", "label", "input_type"):
            value = item.get(key)
            if value:
                compact[key] = value
        if "checked" in item:
            compact["checked"] = item.get("checked")
        if "selected" in item:
            compact["selected"] = item.get("selected")
        value = item.get("value")
        if value:
            compact["filled"] = True
            if item.get("input_type") != "password":
                compact["value"] = value
        elif item.get("role") == "textbox":
            compact["filled"] = False
        if include_child_count:
            compact["child_count"] = item.get("child_count")
        item_count = item.get("item_count") or item.get("_data_region_item_count")
        if item_count:
            compact["item_count"] = item_count
        sample_items = item.get("sample_items")
        if isinstance(sample_items, list) and sample_items:
            compact["sample_items"] = sample_items[:3]
        return {k: v for k, v in compact.items() if v not in (None, "", [], {})}

    def _is_verification_field(self, item: dict[str, Any] | None) -> bool:
        if not item:
            return False
        haystack = " ".join(
            str(item.get(key) or "")
            for key in ("name", "text", "placeholder", "label", "aria_label")
        ).lower()
        return any(token in haystack for token in ("验证码", "verification", "captcha", "sms code", "code"))

    def _is_verification_button(self, item: dict[str, Any]) -> bool:
        if item.get("role") != "button":
            return False
        haystack = " ".join(str(item.get(key) or "") for key in ("name", "text", "label")).lower()
        return any(token in haystack for token in ("获取验证码", "发送验证码", "重新发送", "get code", "send code", "verification"))

    def _goal_provides_code(self, goal: str, code: str) -> bool:
        if not code:
            return False
        return code in goal and any(token in goal for token in ("验证码", "短信", "code", "captcha", "verification"))

    def _state_nodes(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        for section in ("data_regions", "interactable_elements", "surface_index"):
            values = state.get(section)
            if isinstance(values, list):
                nodes.extend(item for item in values if isinstance(item, dict))
        for result in state.get("last_results") or []:
            if not isinstance(result, dict):
                continue
            values = result.get("nodes")
            if isinstance(values, list):
                nodes.extend(item for item in values if isinstance(item, dict))
        return nodes

    def _target_page_count(self, goal: str) -> int:
        lower_goal = goal.lower()
        if any(token in lower_goal for token in ("two pages", "first two pages", "2 pages")):
            return 2
        if any(token in goal for token in ("\u524d\u4e24\u9875", "\u4e24\u9875", "2\u9875")):
            return 2
        patterns = (
            r"\u524d\s*(\d+)\s*\u9875",
            r"(\d+)\s*\u9875",
            r"first\s+(\d+)\s+pages?",
        )
        for pattern in patterns:
            match = re.search(pattern, lower_goal)
            if match:
                return max(1, int(match.group(1)))
        return 1

    def _goal_requests_extraction(self, goal: str) -> bool:
        lower_goal = goal.lower()
        if any(token in lower_goal for token in ("extract", "crawl", "scrape", "json", "items", "movies")):
            return True
        return any(
            token in goal
            for token in (
                "\u63d0\u53d6",
                "\u722c\u53d6",
                "\u722c",
                "\u5b58\u50a8",
                "\u4fdd\u5b58",
                "\u7535\u5f71",
                "\u4fe1\u606f",
            )
        )

    def _best_data_region_ref(self, state: dict[str, Any]) -> str | None:
        regions = state.get("data_regions")
        if not isinstance(regions, list):
            return None
        best_ref = None
        best_count = -1
        for item in regions:
            if not isinstance(item, dict):
                continue
            ref = item.get("ref")
            item_count = item.get("item_count") or item.get("_data_region_item_count") or 0
            if ref and item_count > best_count:
                best_ref = ref
                best_count = int(item_count)
        return best_ref

    def _guard_extraction_action(
        self,
        state: dict[str, Any],
        skill: str,
        params: dict[str, Any],
    ) -> tuple[str, dict[str, Any], bool]:
        if skill != "extract":
            return skill, params, False
        best_ref = self._best_data_region_ref(state)
        if not best_ref:
            return skill, params, False
        requested_ref = params.get("target_ref") or params.get("ref")
        if requested_ref == best_ref:
            return skill, params, False
        guarded_params = dict(params)
        guarded_params.pop("ref", None)
        guarded_params["target_ref"] = best_ref
        guarded_params.setdefault("schema", ["title", "url"])
        return skill, guarded_params, True

    def _item_key(self, item: dict[str, Any]) -> str:
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or item.get("name") or "").strip()
        return url or title or json.dumps(item, ensure_ascii=False, sort_keys=True)

    def _remember_extracted_items(self, data: dict[str, Any], page_url: str | None) -> int:
        items = data.get("items", []) if isinstance(data, dict) else []
        if not isinstance(items, list) or not items:
            return 0
        added = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            key = self._item_key(item)
            if key in self.extracted_keys:
                continue
            self.extracted_keys.add(key)
            self.collected_items.append(item)
            added += 1
        if page_url and page_url == self.last_extracted_page_url:
            return added
        self.extracted_pages += 1
        self.last_extracted_page_url = page_url
        return added

    def _extracted_payload(self) -> dict[str, Any]:
        return {
            "item_count": len(self.collected_items),
            "pages_extracted": self.extracted_pages,
            "target_pages": self.target_pages,
            "items": self.collected_items,
        }

    def _find_next_page_ref(self, state: dict[str, Any]) -> str | None:
        tokens = (
            "\u4e0b\u4e00\u9875",
            "\u4e0b\u9875",
            "next",
            "next page",
            ">",
            "\u203a",
            "\u00bb",
        )
        for item in self._state_nodes(state):
            ref = item.get("ref")
            if not ref:
                continue
            role = str(item.get("role") or "").lower()
            tag = str(item.get("tag") or "").lower()
            if role not in ("link", "button") and tag not in ("a", "button"):
                continue
            haystack = " ".join(
                str(item.get(key) or "")
                for key in ("name", "text", "label", "title", "aria_label")
            ).lower()
            if any(token in haystack for token in tokens):
                return ref
        return None

    def _continuation_action_for_extraction(self, goal: str, state: dict[str, Any]) -> dict[str, Any] | None:
        if not (self._goal_requests_extraction(goal) or self.collected_items or self.target_pages > 1):
            return None
        if self.extracted_pages >= self.target_pages:
            return None
        current_url = state.get("url")
        if self.extracted_pages == 0 or current_url != self.last_extracted_page_url:
            region_ref = self._best_data_region_ref(state)
            if region_ref:
                return {
                    "skill": "extract",
                    "params": {"target_ref": region_ref, "schema": ["title", "url"]},
                    "reason": "Continue crawler goal by extracting the detected data region.",
                }
        next_ref = self._find_next_page_ref(state)
        if next_ref:
            return {
                "skill": "click",
                "params": {"ref": next_ref},
                "reason": "Continue crawler goal by moving to the next page.",
            }
        return {
            "skill": "find",
            "params": {"text": "\u4e0b\u4e00\u9875"},
            "reason": "Find the next-page control before extracting the next page.",
        }

    def _find_state_node(self, state: dict[str, Any], ref: str | None) -> dict[str, Any] | None:
        if not ref:
            return None
        for item in self._state_nodes(state):
            if item.get("ref") == ref:
                return item
        return None

    def _find_verification_button_ref(self, state: dict[str, Any]) -> str | None:
        recent_click_refs = {
            action.get("params", {}).get("ref")
            for action in self.recent_actions[-5:]
            if action.get("skill") == "click"
        }
        fallback = None
        for item in self._state_nodes(state):
            if not self._is_verification_button(item):
                continue
            ref = item.get("ref")
            if not fallback:
                fallback = ref
            if ref and ref not in recent_click_refs:
                return ref
        return fallback

    def _find_unhandled_agreement_checkbox_ref(self, state: dict[str, Any]) -> str | None:
        recent_click_refs = {
            action.get("params", {}).get("ref")
            for action in self.recent_actions[-5:]
            if action.get("skill") == "click"
        }
        for item in self._state_nodes(state):
            if item.get("role") != "checkbox":
                continue
            haystack = " ".join(str(item.get(key) or "") for key in ("name", "text", "label")).lower()
            is_agreement = any(token in haystack for token in ("协议", "隐私", "条款", "同意", "接受", "agree", "terms", "privacy"))
            if not is_agreement:
                continue
            ref = item.get("ref")
            if item.get("checked") is False and ref not in recent_click_refs:
                return ref
        return None

    def _guard_verification_action(
        self,
        goal: str,
        state: dict[str, Any],
        skill: str,
        params: dict[str, Any],
    ) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
        target = self._find_state_node(state, params.get("ref"))
        if skill == "click" and target and self._is_verification_button(target):
            checkbox_ref = self._find_unhandled_agreement_checkbox_ref(state)
            if checkbox_ref:
                return "click", {"ref": checkbox_ref}, None
            return skill, params, None
        if skill != "type":
            return skill, params, None
        if not self._is_verification_field(target):
            return skill, params, None
        text = str(params.get("text") or "")
        if self._goal_provides_code(goal, text):
            return skill, params, None
        button_ref = self._find_verification_button_ref(state)
        if button_ref:
            return "click", {"ref": button_ref}, None
        return skill, params, {
            "ok": False,
            "error": {
                "code": "verification_code_required",
                "message": "Verification code is required and was not provided; refusing to guess or type a dummy code.",
                "details": {"target_ref": params.get("ref")},
            },
        }

    def run(
        self,
        goal: str,
        max_steps: int = MAX_STEPS,
        on_step: Any = None,
    ) -> AgentReport:
        report = AgentReport(scenario="", goal=goal)
        start_time = time.time()
        self.target_pages = self._target_page_count(goal)

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
                    continuation = self._continuation_action_for_extraction(goal, state)
                    if continuation:
                        action = continuation
                        skill = action["skill"]
                        print(f"[Agent] Override stop: {action.get('reason')}")
                    else:
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
                original_skill, original_params = skill, dict(params)
                skill, params, guarded_result = self._guard_verification_action(goal, state, skill, params)
                skill, params, extraction_guarded = self._guard_extraction_action(state, skill, params)
                if skill != original_skill or params != original_params:
                    action = dict(action)
                    action["skill"] = skill
                    action["params"] = params
                    if extraction_guarded:
                        action["reason"] = "Guard: use detected data_region for extraction instead of a navigation/sidebar container."
                    else:
                        action["reason"] = "Guard: click verification-code button instead of guessing a code."
                if guarded_result is not None:
                    print(f"[Agent] Guarded verification action: {guarded_result.get('error')}")
                    result = guarded_result
                elif ENABLE_DUPLICATE_ACTION_BLOCKING and self._is_duplicate_action(skill, params):
                    print(f"[Agent] BLOCKED duplicate action: {skill} with same params. Forcing snapshot to refresh state.")
                    result = self.executor.snapshot(mode="agent_summary")
                    skill = "snapshot"
                    params = {"mode": "agent_summary"}
                else:
                    result = self.execute_skill(skill, params)
                self._record_action(skill, params, result)
                self._record_result(skill, result)

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
                    if callable(on_step):
                        report.total_duration_ms = (time.time() - start_time) * 1000
                        report.full_history = list(self.history)
                        on_step(report)
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
                    if callable(on_step):
                        report.total_duration_ms = (time.time() - start_time) * 1000
                        report.full_history = list(self.history)
                        on_step(report)
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

                if callable(on_step):
                    report.total_duration_ms = (time.time() - start_time) * 1000
                    report.full_history = list(self.history)
                    on_step(report)

                # Analyze result
                if skill == "extract" and result.get("ok"):
                    data = result.get("data") or {}
                    items = data.get("items", []) if isinstance(data, dict) else []
                    added = self._remember_extracted_items(data, state.get("url"))
                    report.items_extracted = len(self.collected_items)
                    report.extracted_data = self._extracted_payload()
                    if items:
                        print(
                            f"[Agent] Extracted page {self.extracted_pages}/{self.target_pages}, "
                            f"added {added} new items (total: {len(self.collected_items)})"
                        )
                    if items and self.extracted_pages >= self.target_pages:
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
                    report.success = self.extracted_pages >= self.target_pages
                    if not report.success:
                        report.error = f"Max steps reached after extracting {self.extracted_pages}/{self.target_pages} pages"
                else:
                    report.error = "Max steps reached"

        except Exception as e:
            report.error = str(e)
            import traceback
            traceback.print_exc()

        if self.collected_items:
            report.extracted_data = self._extracted_payload()
            report.items_extracted = len(self.collected_items)
            if self.extracted_pages >= self.target_pages:
                report.success = True

        report.total_duration_ms = (time.time() - start_time) * 1000
        report.full_history = list(self.history)
        return report


class TestRunner:
    def __init__(self, api_key: str, base_url: str, model: str, headless: bool = False):
        self.llm = LLMClient(api_key, base_url, model)
        self.results: list[AgentReport] = []
        self.headless = headless

    def run_scenario(self, name: str, goal: str, max_steps: int = MAX_STEPS) -> AgentReport:
        print(f"\n{'='*60}")
        print(f"Scenario: {name}")
        print(f"Goal: {goal}")
        print(f"{'='*60}")

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
        log_path = str(log_dir / f"{name}_{max_index + 1}.json")

        executor = DPCLIExecutor(session=f"test-{name}", headless=self.headless)
        agent = DPCLIAgent(self.llm, executor)

        def _on_step(report: AgentReport) -> None:
            self._save_execution_log(name, report, log_path)
            print(f"[Log] Step {len(report.steps)} saved to {log_path}")

        report = agent.run(goal, max_steps=max_steps, on_step=_on_step)
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

        self._save_execution_log(name, report, log_path)
        print(f"[Result] Execution log saved to: {log_path}")

        return report

    def _save_execution_log(
        self, name: str, report: AgentReport, log_path: str | None = None
    ) -> str:
        log_dir = Path("log")
        log_dir.mkdir(exist_ok=True)

        if log_path is None:
            existing = list(log_dir.glob(f"{name}_*.json"))
            max_index = 0
            for f in existing:
                try:
                    idx = int(f.stem.rsplit("_", 1)[-1])
                    max_index = max(max_index, idx)
                except ValueError:
                    continue
            log_path = str(log_dir / f"{name}_{max_index + 1}.json")

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

        Path(log_path).write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")
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
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS, help="Max steps per scenario")
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
    mock_executor.expand.assert_called_with("r1", DEFAULT_EXPAND_DEPTH)
    print(f"  PASSED: expand with depth=null defaults to {DEFAULT_EXPAND_DEPTH}")

    result = agent.execute_skill("list-items", {"group_ref": "r1", "sample_size": None})
    mock_executor.list_items.assert_called_with("r1", DEFAULT_SAMPLE_SIZE)
    print(f"  PASSED: list-items with sample_size=null defaults to {DEFAULT_SAMPLE_SIZE}")

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
