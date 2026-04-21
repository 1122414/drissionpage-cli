from __future__ import annotations

import os

import pytest

from tests.support import cleanup_session, run_cli, unique_session


@pytest.mark.smoke
def test_public_example_dot_com_smoke():
    if os.environ.get("DPCLI_RUN_PUBLIC_SMOKE") != "1":
        pytest.skip("Set DPCLI_RUN_PUBLIC_SMOKE=1 to enable public smoke tests.")

    session = unique_session("smoke")
    try:
        opened = run_cli("open", "https://example.com", "--session", session, "--headless")
        assert opened["ok"] is True
        assert opened["data"]["page"]["title"] == "Example Domain"

        found = run_cli("find", "--session", session, "--headless", "--locator", "tag:a")
        assert found["ok"] is True
        assert found["data"]["count"] >= 1

        ref = found["data"]["elements"][0]["ref"]
        clicked = run_cli("click", "--session", session, "--headless", "--ref", ref)
        assert clicked["ok"] is True
        assert (clicked["data"]["page"]["url"] or "").startswith("http")
    finally:
        cleanup_session(session)
