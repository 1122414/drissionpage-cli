from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable

from dp_cli.errors import CliError
from dp_cli.models import DEFAULT_SESSION
from dp_cli.service import CliService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dp-cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    open_parser = subparsers.add_parser("open", help="Open a page in the session browser.")
    open_parser.add_argument("url")
    _add_common_args(open_parser)

    snapshot_parser = subparsers.add_parser("snapshot", help="Return a structured page snapshot.")
    snapshot_parser.add_argument("ref", nargs="?")
    snapshot_parser.add_argument("--depth", type=int)
    snapshot_parser.add_argument("--view", choices=("planner", "full"), default=None,
                                 help="Deprecated: use --mode instead.")
    snapshot_parser.add_argument("--mode", choices=("full", "agent_summary", "extract"), default="agent_summary")
    _add_common_args(snapshot_parser)

    find_parser = subparsers.add_parser("find", help="Find elements by locator or text.")
    _add_common_args(find_parser)
    group = find_parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--locator")
    group.add_argument("--text")

    click_parser = subparsers.add_parser("click", help="Click an element by ref or locator.")
    _add_common_args(click_parser)
    target_group = click_parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--ref")
    target_group.add_argument("--locator")

    type_parser = subparsers.add_parser("type", help="Type text into an element by ref or locator.")
    _add_common_args(type_parser)
    target_group = type_parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--ref")
    target_group.add_argument("--locator")
    type_parser.add_argument("--text", required=True)

    expand_parser = subparsers.add_parser("expand", help="Expand a container subtree.")
    expand_parser.add_argument("ref")
    expand_parser.add_argument("--depth", type=int, default=2)
    _add_common_args(expand_parser)

    list_items_parser = subparsers.add_parser("list-items", help="List items in a group.")
    list_items_parser.add_argument("group_ref")
    list_items_parser.add_argument("--sample-size", type=int, default=3)
    _add_common_args(list_items_parser)

    extract_parser = subparsers.add_parser("extract", help="Extract structured data from a group.")
    extract_parser.add_argument("target_ref")
    extract_parser.add_argument("--schema", nargs="+", default=None)
    extract_parser.add_argument("--limit", type=int, default=None, help="Max number of items to extract")
    extract_parser.add_argument("--sample-only", action="store_true", help="(Deprecated: use --limit) Extract first 3 items only")
    _add_common_args(extract_parser)

    resolve_parser = subparsers.add_parser("resolve-locator", help="Resolve ref to locator candidates.")
    resolve_parser.add_argument("--ref", required=True)
    _add_common_args(resolve_parser)

    eval_parser = subparsers.add_parser("eval", help="Evaluate JavaScript on the page.")
    eval_parser.add_argument("js")
    _add_common_args(eval_parser)

    batch_detail_parser = subparsers.add_parser(
        "batch-detail-extract",
        help="Open a batch of detail URLs and extract detail fields with deterministic page logic.",
    )
    input_group = batch_detail_parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--items-json", help="JSON array of list items with url/detail_url fields.")
    input_group.add_argument("--items-file", help="Path to a JSON file containing a list of list items.")
    batch_detail_parser.add_argument("--source-url")
    batch_detail_parser.add_argument("--target-pages", type=int)
    batch_detail_parser.add_argument("--list-pages-extracted", type=int)
    batch_detail_parser.add_argument("--limit", type=int)
    batch_detail_parser.add_argument("--schema", nargs="+", default=None)
    batch_detail_parser.add_argument("--extractor", choices=("ai", "legacy-js", "auto"), default="ai")
    batch_detail_parser.add_argument("--navigation-mode", choices=("click", "direct"), default="click")
    batch_detail_parser.add_argument("--fallback-mode", choices=("direct", "skip"), default="direct")
    batch_detail_parser.add_argument("--wait-jitter", type=float, default=0.0)
    batch_detail_parser.add_argument("--max-retries", type=int, default=1)
    batch_detail_parser.add_argument("--item-timeout", type=float, default=None)
    batch_detail_parser.add_argument("--ai-timeout", type=float, default=None)
    batch_detail_parser.add_argument("--output-file", default=None)
    batch_detail_parser.add_argument("--progress-file", default=None)
    _add_common_args(batch_detail_parser)

    session_parser = subparsers.add_parser("session", help="Inspect session runtime and page identity.")
    session_subparsers = session_parser.add_subparsers(dest="session_command", required=True)
    inspect_parser = session_subparsers.add_parser("inspect", help="Return agent-friendly session state.")
    _add_common_args(inspect_parser)

    return parser


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--wait-time", type=float, default=0.0)


def print_json(payload: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def success(session: str, action: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "session": session, "action": action, "data": data, "error": None}


def failure(session: str, action: str, error: CliError | Exception) -> dict[str, Any]:
    if isinstance(error, CliError):
        payload = {"code": error.code, "message": error.message, "details": error.details}
    else:
        payload = {"code": "unexpected_error", "message": str(error), "details": {}}
    return {"ok": False, "session": session, "action": action, "data": None, "error": payload}


_COMMAND_MAP: dict[str, Callable[[argparse.Namespace, CliService], dict[str, Any]]] = {
    "open": lambda a, s: success(
        a.session, "open", s.open_page(a.url, session=a.session, headless=a.headless, wait_time=a.wait_time)
    ),
    "snapshot": lambda a, s: success(
        a.session,
        "snapshot",
        s.snapshot_page(
            session=a.session,
            ref=getattr(a, "ref", None),
            depth=getattr(a, "depth", None),
            view=getattr(a, "view", None),
            mode=getattr(a, "mode", "agent_summary"),
            headless=a.headless,
            wait_time=a.wait_time,
        ),
    ),
    "find": lambda a, s: success(
        a.session,
        "find",
        s.find_elements(
            session=a.session,
            locator=getattr(a, "locator", None),
            text=getattr(a, "text", None),
            headless=a.headless,
            wait_time=a.wait_time,
        ),
    ),
    "click": lambda a, s: success(
        a.session,
        "click",
        s.click_element(
            session=a.session,
            ref=getattr(a, "ref", None),
            locator=getattr(a, "locator", None),
            headless=a.headless,
            wait_time=a.wait_time,
        ),
    ),
    "type": lambda a, s: success(
        a.session,
        "type",
        s.type_into_element(
            a.text,
            session=a.session,
            ref=getattr(a, "ref", None),
            locator=getattr(a, "locator", None),
            headless=a.headless,
            wait_time=a.wait_time,
        ),
    ),
    "expand": lambda a, s: success(
        a.session,
        "expand",
        s.expand_container(
            session=a.session,
            ref=a.ref,
            depth=a.depth,
            headless=a.headless,
            wait_time=a.wait_time,
        ),
    ),
    "list-items": lambda a, s: success(
        a.session,
        "list-items",
        s.list_items(
            session=a.session,
            group_ref=a.group_ref,
            sample_size=a.sample_size,
            headless=a.headless,
            wait_time=a.wait_time,
        ),
    ),
    "extract": lambda a, s: success(
        a.session,
        "extract",
        s.extract_group(
            session=a.session,
            target_ref=a.target_ref,
            schema=a.schema,
            limit=a.limit if a.limit is not None else (3 if a.sample_only else None),
            headless=a.headless,
            wait_time=a.wait_time,
        ),
    ),
    "resolve-locator": lambda a, s: success(
        a.session,
        "resolve-locator",
        s.resolve_locator(
            session=a.session,
            ref=a.ref,
            headless=a.headless,
            wait_time=a.wait_time,
        ),
    ),
    "eval": lambda a, s: success(
        a.session,
        "eval",
        s.eval_js(
            session=a.session,
            js=a.js,
            headless=a.headless,
            wait_time=a.wait_time,
        ),
    ),
    "batch-detail-extract": lambda a, s: success(
        a.session,
        "batch-detail-extract",
        s.batch_extract_detail_pages(
            session=a.session,
            items=_load_items_arg(a),
            source_url=getattr(a, "source_url", None),
            target_pages=getattr(a, "target_pages", None),
            list_pages_extracted=getattr(a, "list_pages_extracted", None),
            limit=getattr(a, "limit", None),
            schema=getattr(a, "schema", None),
            extractor=getattr(a, "extractor", "ai"),
            navigation_mode=getattr(a, "navigation_mode", "click"),
            fallback_mode=getattr(a, "fallback_mode", "direct"),
            wait_time=getattr(a, "wait_time", 0.0),
            wait_jitter=getattr(a, "wait_jitter", 0.0),
            max_retries=getattr(a, "max_retries", 1),
            item_timeout=getattr(a, "item_timeout", None),
            ai_timeout=getattr(a, "ai_timeout", None),
            output_file=getattr(a, "output_file", None),
            progress_file=getattr(a, "progress_file", None),
            headless=a.headless,
        ),
    ),
    "session": lambda a, s: success(
        a.session,
        "session.inspect",
        s.inspect_session(session=a.session, headless=a.headless, wait_time=a.wait_time),
    ),
}


def _load_items_arg(args: argparse.Namespace) -> list[dict]:
    if getattr(args, "items_json", None):
        raw = args.items_json
    else:
        with open(args.items_file, "r", encoding="utf-8") as handle:
            raw = handle.read()
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise CliError("invalid_input", "Detail batch input must be a JSON array of item objects.")
    if not all(isinstance(item, dict) for item in parsed):
        raise CliError("invalid_input", "Detail batch input items must be JSON objects.")
    return parsed


def dispatch(args: argparse.Namespace, service: CliService) -> dict[str, Any]:
    handler = _COMMAND_MAP.get(args.command)
    if handler:
        return handler(args, service)
    raise CliError("unknown_command", f"Unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    service = CliService()
    try:
        payload = dispatch(args, service)
        print_json(payload)
        return 0
    except CliError as exc:
        print_json(failure(getattr(args, "session", DEFAULT_SESSION), getattr(args, "command", "unknown"), exc))
        return exc.exit_code
    except Exception as exc:  # pragma: no cover - top-level safety net
        print_json(failure(getattr(args, "session", DEFAULT_SESSION), getattr(args, "command", "unknown"), exc))
        return 1
