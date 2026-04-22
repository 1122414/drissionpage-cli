from __future__ import annotations

import pytest

from tests.support import LocalFixtureServer, cleanup_session, run_cli, unique_session


@pytest.fixture
def local_fixture_server():
    with LocalFixtureServer() as server:
        yield server


@pytest.fixture
def local_session() -> str:
    return unique_session("local")


@pytest.fixture(scope="session", autouse=True)
def browser_runtime_available():
    session = unique_session("probe")
    try:
        payload = run_cli("open", "about:blank", "--session", session, "--headless", check=False)
        error = payload.get("error") or {}
        message = error.get("message", "")
        browser_down_markers = ("BrowserConnectError", "connect", "连接", "连接失败")
        if payload.get("ok") is False and any(marker in message for marker in browser_down_markers):
            pytest.skip("Local browser runtime is not available in the current execution environment.")
    finally:
        cleanup_session(session)
