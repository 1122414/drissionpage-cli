from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.support import cleanup_session, run_public_smoke_workflow, unique_session  # noqa: E402


def main() -> int:
    session = unique_session("script-smoke")
    try:
        results = run_public_smoke_workflow(session)
        opened = results["opened"]
        assert opened["data"]["page"]["title"] == "Example Domain"
        clicked = results["clicked"]
        assert (clicked["data"]["page"]["url"] or "").startswith("http")
        print("Public smoke workflow passed.")
    finally:
        cleanup_session(session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
