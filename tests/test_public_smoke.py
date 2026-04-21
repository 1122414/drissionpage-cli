from __future__ import annotations

import os

import pytest

from tests.support import cleanup_session, run_public_smoke_workflow, unique_session


@pytest.mark.smoke
def test_public_example_dot_com_smoke():
    if os.environ.get("DPCLI_RUN_PUBLIC_SMOKE") != "1":
        pytest.skip("Set DPCLI_RUN_PUBLIC_SMOKE=1 to enable public smoke tests.")

    session = unique_session("smoke")
    try:
        results = run_public_smoke_workflow(session)
        opened = results["opened"]
        assert opened["ok"] is True
        assert opened["data"]["page"]["title"] == "Example Domain"

        found = results["found"]
        assert found["ok"] is True
        assert found["data"]["count"] >= 1

        clicked = results["clicked"]
        assert clicked["ok"] is True
        assert (clicked["data"]["page"]["url"] or "").startswith("http")
    finally:
        cleanup_session(session)
