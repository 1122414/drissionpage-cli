# tests/ KNOWLEDGE BASE

**Generated:** 2026-04-29

## OVERVIEW

Pytest integration test suite. Tests invoke CLI via subprocess (`run_cli()`), not direct import. Local tests use a fixture server; public tests require network + browser.

## STRUCTURE

```
tests/
├── conftest.py              # 3 fixtures (local_fixture_server, local_session, browser_runtime_available)
├── support.py               # ALL test infrastructure: run_cli(), LocalFixtureServer, workflow runners
├── test_cli_local.py        # 20 tests — main regression suite
├── test_public_smoke.py     # 1 test — network-gated smoke
├── test_schema_v0_5.py      # ~15 tests — pure data model unit tests
├── test_agent_computor.py   # LLM agent harness + unit tests (2171 lines)
├── test_agent_loop_view.py  # 2 tests — validates compact_snapshot()
└── fixtures/site/           # Static HTML for deterministic testing
    ├── index.html
    └── detail.html
```

## WHERE TO LOOK

| Task | File | Notes |
|------|------|-------|
| Add local integration test | `test_cli_local.py` | Use `local_fixture_server` + `local_session` fixtures |
| Add public smoke test | `test_public_smoke.py` | Mark `@pytest.mark.smoke`, gate with `DPCLI_RUN_PUBLIC_SMOKE` |
| Add unit test for models | `test_schema_v0_5.py` | Pure dataclass/function tests, no browser needed |
| Add test helper | `support.py` | `run_cli()`, `select_node()`, `snapshot_nodes()`, workflow runners |
| Add HTML fixture | `fixtures/site/` | Served by `LocalFixtureServer` on random port |

## CONVENTIONS

- **Subprocess CLI testing**: `run_cli(*args)` spawns `python -m dp_cli` as child process, parses JSON stdout
- **Session isolation**: Each test gets `unique_session("local")` → `local-{uuid[:8]}`
- **Cleanup pattern**: `cleanup_session(session)` in `finally` blocks — quits browser, removes session dir
- **Node selection**: `select_node(nodes, ref_type=, role=, element_id=, name_contains=, interactable_now=)`
- **Text matching**: `best_text_match(nodes, text)` ranks by `score_text_match()` from `dp_cli.models`
- **Fixture server**: `LocalFixtureServer` context manager — `ThreadingHTTPServer` on `127.0.0.1:0`
- **Browser guard**: `browser_runtime_available` (session-scoped autouse) probes `about:blank`; auto-skips all tests if browser unavailable

## ANTI-PATTERNS

- Do **not** import `CliService` or `DrissionPageAdapter` directly in tests — use `run_cli()` subprocess
- Do **not** share session names between tests — always use `unique_session()`
- Do **not** forget `cleanup_session()` — leaked browser processes accumulate
- Do **not** add tests that require network without `@pytest.mark.smoke` + env var gating
