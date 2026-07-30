from __future__ import annotations

import argparse
import copy
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
    type_parser.add_argument(
        "--submit",
        action="store_true",
        help="Press Enter after typing to submit the surrounding form.",
    )

    scroll_parser = subparsers.add_parser(
        "scroll",
        help="Scroll the current page and return before/after viewport metrics.",
    )
    scroll_parser.add_argument(
        "--direction",
        choices=("down", "up", "left", "right"),
        default="down",
    )
    scroll_parser.add_argument("--amount", type=int, default=900)
    scroll_parser.add_argument(
        "--to",
        choices=("top", "bottom", "half", "leftmost", "rightmost"),
        default=None,
    )
    scroll_parser.add_argument(
        "--ready-condition",
        choices=("document", "element", "network-idle"),
        default=None,
        help="Use a native readiness condition after scrolling instead of a blind delay.",
    )
    scroll_parser.add_argument("--ready-locator", default=None)
    scroll_parser.add_argument("--ready-timeout", type=float, default=10.0)
    _add_common_args(scroll_parser)

    ready_parser = subparsers.add_parser(
        "wait-ready",
        help="Wait for a documented browser readiness condition and return evidence.",
    )
    ready_parser.add_argument(
        "--condition",
        choices=("document", "element", "network-idle"),
        default="document",
    )
    ready_parser.add_argument("--locator", default=None)
    ready_parser.add_argument("--timeout", type=float, default=10.0)
    _add_common_args(ready_parser)

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
    close_parser = session_subparsers.add_parser("close", help="Close the session browser without deleting artifacts.")
    close_parser.add_argument("--session", default=DEFAULT_SESSION)

    return parser


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--headless", action="store_true", default=None)
    parser.add_argument("--wait-time", type=float, default=0.0)
    parser.add_argument(
        "--request-id",
        default=None,
        help="Replay-safe idempotency key for one agent action.",
    )


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
    "scroll": lambda a, s: success(
        a.session,
        "scroll",
        s.scroll_page(
            session=a.session,
            direction=a.direction,
            amount=a.amount,
            to=a.to,
            headless=a.headless,
            wait_time=a.wait_time,
            ready_condition=a.ready_condition,
            ready_locator=a.ready_locator,
            ready_timeout=a.ready_timeout,
        ),
    ),
    "wait-ready": lambda a, s: success(
        a.session,
        "wait-ready",
        s.wait_ready(
            session=a.session,
            condition=a.condition,
            locator=a.locator,
            timeout=a.timeout,
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
            submit=getattr(a, "submit", False),
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
        "session.close" if a.session_command == "close" else "session.inspect",
        (
            s.close_session(session=a.session)
            if a.session_command == "close"
            else s.inspect_session(session=a.session, headless=a.headless, wait_time=a.wait_time)
        ),
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
    if not handler:
        raise CliError("unknown_command", f"Unsupported command: {args.command}")

    request_id = str(getattr(args, "request_id", None) or "").strip()
    if len(request_id) > 200:
        raise CliError(
            "invalid_input",
            "--request-id must not exceed 200 characters.",
        )
    store = service.sessions.store
    if request_id:
        cached = store.load_action_receipt(args.session, request_id)
        if cached is not None:
            replayed = copy.deepcopy(cached)
            data = replayed.get("data")
            if isinstance(data, dict):
                data["_idempotency"] = {
                    "request_id": request_id,
                    "replayed": True,
                }
            return replayed

    result = handler(args, service)
    if request_id and result.get("ok"):
        data = result.get("data")
        if isinstance(data, dict):
            data["_idempotency"] = {
                "request_id": request_id,
                "replayed": False,
            }
        store.save_action_receipt(args.session, request_id, copy.deepcopy(result))
    return result


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
