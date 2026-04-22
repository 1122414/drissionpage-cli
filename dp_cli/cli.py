from __future__ import annotations

import argparse
import json
from typing import Any

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
    snapshot_parser.add_argument("--view", choices=("planner", "full"), default="planner")
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

    session_parser = subparsers.add_parser("session", help="Inspect session runtime and page identity.")
    session_subparsers = session_parser.add_subparsers(dest="session_command", required=True)
    inspect_parser = session_subparsers.add_parser("inspect", help="Return agent-friendly session state.")
    _add_common_args(inspect_parser)

    return parser


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--headless", action="store_true")


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def success(session: str, action: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "session": session, "action": action, "data": data, "error": None}


def failure(session: str, action: str, error: CliError | Exception) -> dict[str, Any]:
    if isinstance(error, CliError):
        payload = {"code": error.code, "message": error.message, "details": error.details}
    else:
        payload = {"code": "unexpected_error", "message": str(error), "details": {}}
    return {"ok": False, "session": session, "action": action, "data": None, "error": payload}


def dispatch(args: argparse.Namespace, service: CliService) -> dict[str, Any]:
    if args.command == "open":
        return success(args.session, "open", service.open_page(args.url, session=args.session, headless=args.headless))
    if args.command == "snapshot":
        return success(
            args.session,
            "snapshot",
            service.snapshot_page(
                session=args.session,
                ref=getattr(args, "ref", None),
                depth=getattr(args, "depth", None),
                view=getattr(args, "view", "planner"),
                headless=args.headless,
            ),
        )
    if args.command == "find":
        return success(
            args.session,
            "find",
            service.find_elements(
                session=args.session,
                locator=getattr(args, "locator", None),
                text=getattr(args, "text", None),
                headless=args.headless,
            ),
        )
    if args.command == "click":
        return success(
            args.session,
            "click",
            service.click_element(
                session=args.session,
                ref=getattr(args, "ref", None),
                locator=getattr(args, "locator", None),
                headless=args.headless,
            ),
        )
    if args.command == "type":
        return success(
            args.session,
            "type",
            service.type_into_element(
                args.text,
                session=args.session,
                ref=getattr(args, "ref", None),
                locator=getattr(args, "locator", None),
                headless=args.headless,
            ),
        )
    if args.command == "session" and args.session_command == "inspect":
        return success(
            args.session,
            "session.inspect",
            service.inspect_session(session=args.session, headless=args.headless),
        )
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
