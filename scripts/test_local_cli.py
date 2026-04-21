from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.support import LocalFixtureServer, cleanup_session, run_cli, unique_session  # noqa: E402


def main() -> int:
    session = unique_session("script-local")
    try:
        with LocalFixtureServer() as server:
            run_cli("open", server.url, "--session", session, "--headless")
            button_ref = run_cli("find", "--session", session, "--headless", "--text", "Primary Action")["data"]["elements"][0]["ref"]
            input_ref = run_cli("find", "--session", session, "--headless", "--locator", "#name-input")["data"]["elements"][0]["ref"]
            run_cli("type", "--session", session, "--headless", "--ref", input_ref, "--text", "Scripted CLI")
            run_cli("click", "--session", session, "--headless", "--ref", button_ref)
            snapshot = run_cli("snapshot", "--session", session, "--headless")
            elements = snapshot["data"]["elements"]
            button = next(item for item in elements if item["id"] == "primary-action")
            text_input = next(item for item in elements if item["id"] == "name-input")
            assert button["text"] == "Clicked 1"
            assert text_input["value"] == "Scripted CLI"
            print("Local CLI workflow passed.")
    finally:
        cleanup_session(session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
