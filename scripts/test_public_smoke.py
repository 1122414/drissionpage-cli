from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.support import cleanup_session, run_cli, unique_session  # noqa: E402


def main() -> int:
    session = unique_session("script-smoke")
    try:
        opened = run_cli("open", "https://example.com", "--session", session, "--headless")
        assert opened["data"]["page"]["title"] == "Example Domain"
        found = run_cli("find", "--session", session, "--headless", "--locator", "tag:a")
        ref = found["data"]["elements"][0]["ref"]
        clicked = run_cli("click", "--session", session, "--headless", "--ref", ref)
        assert (clicked["data"]["page"]["url"] or "").startswith("http")
        print("Public smoke workflow passed.")
    finally:
        cleanup_session(session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
