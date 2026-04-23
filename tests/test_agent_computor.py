from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_openai import ChatOpenAI


DEFAULT_CONFIG = {
    "api_key": os.getenv("OPENAI_API_KEY", ""),
    "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
}

# Test scenarios
SCENARIOS = {
    "automation": "打开 https://www.baidu.com，在搜索框输入'python tutorial'，点击搜索按钮",
    "crawler_list": "访问 https://news.ycombinator.com，提取前5条新闻的标题和链接",
    "crawler_pagination": "访问 https://quotes.toscrape.com，抓取所有名言和作者，处理分页",
    "hybrid": "打开 https://github.com/trending，找到Python趋势项目列表，提取前3个项目名和星数",
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
        return str(content).strip()

    def extract_json(self, text: str) -> dict[str, Any]:
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if match:
                return json.loads(match.group(0))
            raise ValueError(f"No JSON found in: {text[:200]}")


class DPCLIExecutor:
    def __init__(self, session: str = "agent-test", headless: bool = True):
        self.session = session
        self.headless = headless
        self.base_cmd = ["python", "-m", "dp_cli"]
        if headless:
            self.base_cmd.append("--headless")
        self.base_cmd.extend(["--session", session])

    def _run(self, *args) -> dict[str, Any]:
        import subprocess
        cmd = [*self.base_cmd, *args]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
            )
            if result.returncode != 0:
                return {
                    "ok": False,
                    "error": result.stderr or f"Exit code {result.returncode}",
                    "stdout": result.stdout,
                }
            return json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Timeout after 30s"}
        except json.JSONDecodeError:
            return {"ok": False, "error": "Invalid JSON output", "raw": result.stdout}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open(self, url: str) -> dict[str, Any]:
        return self._run("open", url)

    def snapshot(self, mode: str = "agent_summary", ref: str | None = None, depth: int | None = None) -> dict[str, Any]:
        args = ["snapshot", "--mode", mode]
        if ref:
            args.extend([ref])
        if depth is not None:
            args.extend(["--depth", str(depth)])
        return self._run(*args)

    def expand(self, ref: str, depth: int = 2) -> dict[str, Any]:
        return self._run("expand", ref, "--depth", str(depth))

    def list_items(self, group_ref: str, sample_size: int = 3) -> dict[str, Any]:
        return self._run("list-items", group_ref, "--sample-size", str(sample_size))

    def extract(self, target_ref: str, schema: list[str] | None = None, sample_only: bool = False) -> dict[str, Any]:
        args = ["extract", target_ref]
        if schema:
            args.extend(["--schema", *schema])
        if sample_only:
            args.append("--sample-only")
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
        text = self.llm.invoke(prompt)
        return self.llm.extract_json(text)

    def decide_action(self, goal: str, current_state: dict[str, Any]) -> dict[str, Any]:
        prompt = (
            "You are controlling a browser via dp_cli v0.5.\n"
            "Available skills: open, snapshot, expand, find, click, type, list-items, extract, resolve-locator, eval\n"
            "Choose the next action based on the current state and goal.\n\n"
            "Return JSON with:\n"
            '- "thought": your reasoning\n'
            '- "action": {"skill": "skill_name", "params": {...}, "reason": "..."}\n\n'
            f"Goal: {goal}\n\n"
            f"Current state:\n{json.dumps(current_state, ensure_ascii=False, indent=2)}\n\n"
            f"History:\n{json.dumps(self.history[-3:], ensure_ascii=False, indent=2)}\n\n"
            "Return ONLY JSON."
        )
        text = self.llm.invoke(prompt)
        result = self.llm.extract_json(text)
        return result

    def execute_skill(self, skill: str, params: dict[str, Any]) -> dict[str, Any]:
        if skill == "open":
            return self.executor.open(params["url"])
        elif skill == "snapshot":
            return self.executor.snapshot(
                mode=params.get("mode", "agent_summary"),
                ref=params.get("ref"),
                depth=params.get("depth"),
            )
        elif skill == "expand":
            return self.executor.expand(params["ref"], params.get("depth", 2))
        elif skill == "find":
            return self.executor.find(text=params.get("text"), locator=params.get("locator"))
        elif skill == "click":
            return self.executor.click(ref=params.get("ref"), locator=params.get("locator"))
        elif skill == "type":
            return self.executor.type_text(params["ref"], params["text"])
        elif skill == "list-items":
            return self.executor.list_items(params["group_ref"], params.get("sample_size", 3))
        elif skill == "extract":
            return self.executor.extract(
                params["target_ref"],
                schema=params.get("schema"),
                sample_only=params.get("sample_only", False),
            )
        elif skill == "resolve-locator":
            return self.executor.resolve_locator(params["ref"])
        elif skill == "eval":
            return self.executor.eval_js(params["js"])
        else:
            return {"ok": False, "error": f"Unknown skill: {skill}"}

    def compact_state(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        data = snapshot.get("data", {})
        page = data.get("page", {})
        result = {
            "url": page.get("url"),
            "title": page.get("title"),
            "mode": data.get("mode"),
        }

        if "summary" in data:
            summary = data["summary"]
            result["summary"] = {
                "global_actions_count": len(summary.get("global_actions", [])),
                "visible_focus_count": len(summary.get("visible_focus", [])),
                "repeated_regions_count": len(summary.get("repeated_regions", [])),
            }
            if summary.get("repeated_regions"):
                result["summary"]["first_region"] = summary["repeated_regions"][0]

        if "groups" in data:
            groups = data["groups"]
            result["groups_count"] = len(groups)
            if groups:
                result["first_group"] = groups[0]

        if "recovery" in data:
            recovery = data["recovery"]
            result["recovery"] = {
                "truncated": recovery.get("truncated", False),
                "expand_candidates_count": len(recovery.get("expand_candidates", [])),
            }

        return result

    def run(self, goal: str, max_steps: int = 10) -> AgentReport:
        report = AgentReport(scenario="", goal=goal)
        start_time = time.time()

        try:
            # Step 0: Plan
            plan = self.plan_goal(goal)
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

            # Main loop
            for step in range(1, max_steps + 1):
                step_start = time.time()

                # Get snapshot
                snapshot = self.executor.snapshot(mode="agent_summary")
                state = self.compact_state(snapshot)

                # Decide action
                decision = self.decide_action(goal, state)
                action = decision.get("action", {})
                skill = action.get("skill", "stop")

                if skill == "stop":
                    print(f"[Agent] Stopping: {action.get('reason', 'Goal complete')}")
                    report.success = True
                    break

                # Execute
                print(f"[Agent] Step {step}: {skill} - {action.get('reason', '')}")
                result = self.execute_skill(skill, action.get("params", {}))

                # Record
                agent_step = AgentStep(
                    step=step,
                    thought=decision.get("thought", ""),
                    action=action,
                    result=result,
                    duration_ms=(time.time() - step_start) * 1000,
                )
                report.steps.append(agent_step)
                self.history.append({"skill": skill, "params": action.get("params"), "result": result})

                # Analyze result
                if skill == "extract" and result.get("ok"):
                    items = result.get("data", {}).get("items", [])
                    report.items_extracted = len(items)
                    if items:
                        report.success = True
                        break

                if skill == "click" and result.get("ok"):
                    pass

            else:
                report.error = "Max steps reached"

        except Exception as e:
            report.error = str(e)
            import traceback
            traceback.print_exc()

        report.total_duration_ms = (time.time() - start_time) * 1000
        return report


class TestRunner:
    def __init__(self, api_key: str, base_url: str, model: str):
        self.llm = LLMClient(api_key, base_url, model)
        self.results: list[AgentReport] = []

    def run_scenario(self, name: str, goal: str, max_steps: int = 10) -> AgentReport:
        print(f"\n{'='*60}")
        print(f"Scenario: {name}")
        print(f"Goal: {goal}")
        print(f"{'='*60}")

        executor = DPCLIExecutor(session=f"test-{name}", headless=True)
        agent = DPCLIAgent(self.llm, executor)

        report = agent.run(goal, max_steps=max_steps)
        self.results.append(report)

        # Print summary
        print(f"\n[Result] Success: {report.success}")
        print(f"[Result] Steps: {len(report.steps)}")
        print(f"[Result] Duration: {report.total_duration_ms:.0f}ms")
        if report.error:
            print(f"[Result] Error: {report.error}")
        if report.items_extracted > 0:
            print(f"[Result] Items extracted: {report.items_extracted}")

        return report

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
        description="Test dp_cli v0.5 capabilities with natural language commands via LangChain + OpenAI",
    )
    parser.add_argument("--api-key", default=DEFAULT_CONFIG["api_key"], help="OpenAI API key")
    parser.add_argument("--base-url", default=DEFAULT_CONFIG["base_url"], help="OpenAI-compatible base URL")
    parser.add_argument("--model", default=DEFAULT_CONFIG["model"], help="Model name")
    parser.add_argument("--scenario", choices=list(SCENARIOS.keys()), help="Run a specific scenario")
    parser.add_argument("--goal", help="Custom natural language goal")
    parser.add_argument("--max-steps", type=int, default=10, help="Max steps per scenario")
    parser.add_argument("--headed", action="store_true", help="Run with visible browser")
    parser.add_argument("--output", help="Save results to JSON file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.api_key:
        print("Error: API key required. Set OPENAI_API_KEY or pass --api-key")
        return 1

    runner = TestRunner(
        api_key=args.api_key,
        base_url=args.base_url,
        model=args.model,
    )

    # Run scenarios
    if args.goal:
        runner.run_scenario("custom", args.goal, args.max_steps)
    elif args.scenario:
        goal = SCENARIOS[args.scenario]
        runner.run_scenario(args.scenario, goal, args.max_steps)
    else:
        # Run all scenarios
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


if __name__ == "__main__":
    raise SystemExit(main())
