from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.support import LocalFixtureServer, cleanup_session, run_local_workflow, unique_session  # noqa: E402


def main() -> int:
    session = unique_session("script-local")
    try:
        with LocalFixtureServer() as server:
            snapshot = run_local_workflow(session, server.url, typed_text="Scripted CLI")["snapshot"]
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
