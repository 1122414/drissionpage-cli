from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.support import LocalFixtureServer, assert_search_state, cleanup_session, run_local_workflow, unique_session  # noqa: E402


def main() -> int:
    last_error: Exception | None = None
    for _ in range(3):
        session = unique_session("script-local")
        try:
            with LocalFixtureServer() as server:
                results = run_local_workflow(session, server.url, typed_text="Scripted CLI")
                assert results["root_snapshot"]["data"]["mode"] == "semantic"
                assert_search_state(results["post_snapshot"]["data"]["nodes"], "Scripted CLI")
                print("Local CLI workflow passed.")
                return 0
        except Exception as exc:
            last_error = exc
        finally:
            cleanup_session(session)
    if last_error is not None:
        raise last_error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
