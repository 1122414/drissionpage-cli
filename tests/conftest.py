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
        if payload.get("ok") is False and payload.get("error", {}).get("message", "").find("浏览器连接失败") != -1:
            pytest.skip("Local browser runtime is not available in the current execution environment.")
    finally:
        cleanup_session(session)
