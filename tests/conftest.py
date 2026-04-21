from __future__ import annotations

import pytest

from tests.support import LocalFixtureServer, unique_session


@pytest.fixture
def local_fixture_server():
    with LocalFixtureServer() as server:
        yield server


@pytest.fixture
def local_session() -> str:
    return unique_session("local")

