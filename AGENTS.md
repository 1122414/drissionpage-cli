# PROJECT KNOWLEDGE BASE

**Generated:** 2026-04-22

## OVERVIEW

`dp_cli` is an Agent-first CLI wrapper around DrissionPage. Core stack: Python 3.11+, `argparse`, `DrissionPage`, `pytest`, `langchain-openai` (agent loop scripts only).

The main design contract is **semantic snapshot + ref-driven interaction**, not raw selectors.

## STRUCTURE

```
.
├── dp_cli/           # Core package (CLI, service, adapter, session)
├── scripts/          # Runnable smoke tests and agent loop demos
├── tests/            # pytest suite (local + public smoke)
├── .dpcli/           # Runtime state (sessions, snapshots) — gitignored
├── pytest.ini        # Markers: smoke
└── now.md            # Living project status doc (Chinese)
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Add CLI command | `dp_cli/cli.py` | argparse subparsers; keep JSON output contract |
| Command logic / orchestration | `dp_cli/service.py` | `CliService` is the main boundary |
| Browser interaction / snapshot | `dp_cli/adapter.py` | Injected JS for semantic discovery |
| Session & browser lifecycle | `dp_cli/session.py` | `SessionManager._restore_tab` is critical |
| Runtime state / ref mapping | `dp_cli/runtime.py` | `RuntimeContext` contextmanager |
| Persistence / paths | `dp_cli/session_store.py` | `.dpcli/sessions/<name>/` layout |
| Data models | `dp_cli/models.py` | dataclasses: `SessionState`, `SnapshotNodeRecord`, etc. |
| Error types | `dp_cli/errors.py` | Structured `CliError` with `code`, `exit_code` |
| Run local smoke | `scripts/test_local_cli.py` | Uses `tests.support` fixture server |
| Run agent loop demo | `scripts/test_min_agent_loop.py` | Needs OpenAI config filled in |
| Add test | `tests/test_cli_local.py` | Uses `run_cli()` helper from `tests/support.py` |

## CODE MAP

| Symbol | Type | File | Role |
|--------|------|------|------|
| `main` | function | `cli.py` | Entry point; parses args, dispatches, prints JSON |
| `CliService` | class | `service.py` | Orchestrates all commands; owns `SessionManager` + `DrissionPageAdapter` |
| `DrissionPageAdapter` | class | `adapter.py` | Wraps browser tab; runs JS snapshot discovery |
| `SessionManager` | class | `session.py` | Loads meta/state, builds `ChromiumOptions`, restores tab |
| `RuntimeContext` | class | `runtime.py` | Context manager for runtime identity, page sync, ref upsert |
| `SessionStore` | class | `session_store.py` | JSON read/write for meta/state; browser auto-discovery |
| `CliError` | class | `errors.py` | Base for all structured errors; maps to JSON + exit codes |

## CONVENTIONS

- **Imports**: `from __future__ import annotations` first, then stdlib, then `dp_cli.*`
- **Naming**: `snake_case` functions/modules, `PascalCase` classes/dataclasses/exceptions, `UPPER_SNAKE_CASE` constants
- **JSON contract**: Every command returns `{ok, session, action, data, error}`
- **Ref rules**: `r*` = container, `e*` = element; `click`/`type` only accept `e*`
- **Snapshot modes**: `planner` (default, low-token) or `full` (complete discovery)
- **Session storage**: `.dpcli/sessions/<name>/` (meta.json, state.json, profile/)
- **Snapshot artifacts**: `.dpcli/snapshots/<name>/`
- **Browser override**: `DPCLI_BROWSER_PATH` env var
- **pytest**: `smoke` marker for opt-in public network tests

## ANTI-PATTERNS (THIS PROJECT)

- Do **not** add `pyproject.toml` / `setup.py` packaging without discussion — the repo is intentionally package-first but unpackaged
- Do **not** break the JSON output contract shape (`{ok, session, action, data, error}`)
- Do **not** let `click`/`type` accept container refs (`r*`) — must return `invalid_ref_type`
- Do **not** store runtime data outside `.dpcli/`
- Do **not** expose raw DrissionPage objects past `adapter.py` into `service.py` callers

## UNIQUE STYLES

- CLI is designed for `python -m dp_cli`, not a standalone console script
- `scripts/` and `tests/` both contain runnable workflows; `scripts/` are demos, `tests/` are assertions
- `now.md` is a living bilingual project status doc, not a roadmap

## COMMANDS

```bash
# Local regression
pytest -q tests/test_cli_local.py
pytest -q tests

# Public smoke (requires network + browser)
set DPCLI_RUN_PUBLIC_SMOKE=1
pytest -q tests/test_public_smoke.py

# Manual smoke scripts
python scripts/test_local_cli.py
python scripts/test_public_smoke.py
python scripts/test_min_agent_loop.py   # needs OpenAI config
```

## NOTES

- No lint/format config present (no ruff, black, flake8, mypy). Follow existing file style.
- Browser runtime is required; `conftest.py` auto-skips if browser is unavailable.
- Session identity includes `session_id`, `runtime_id`, `page_id`, `snapshot_id` to prevent stale ref reuse.
