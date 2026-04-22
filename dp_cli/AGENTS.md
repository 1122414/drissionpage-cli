# dp_cli PACKAGE KNOWLEDGE BASE

**Generated:** 2026-04-22

## OVERVIEW

Core package: CLI dispatch, browser adapter, session/runtime management, and ref-driven snapshot logic.

## STRUCTURE

```
dp_cli/
├── __main__.py       # `python -m dp_cli` entrypoint
├── cli.py            # argparse setup, JSON dispatch, `main()`
├── service.py        # `CliService` — command orchestration
├── adapter.py        # `DrissionPageAdapter` — DOM snapshot via injected JS
├── session.py        # `SessionManager` — browser lifecycle + tab restore
├── runtime.py        # `RuntimeContext` — ref mapping + page identity
├── session_store.py  # `SessionStore` — JSON persistence + browser discovery
├── models.py         # dataclasses: state, nodes, bounds, visibility
└── errors.py         # `CliError` hierarchy with structured exit codes
```

## WHERE TO LOOK

| Task | File | Notes |
|------|------|-------|
| Add a CLI command | `cli.py` | Register subparser, wire into `dispatch()`, keep JSON contract |
| Change command behavior | `service.py` | `CliService` methods are the primary boundary |
| Change snapshot discovery | `adapter.py` | Contains `SNAPSHOT_SCRIPT` (injected JS) and planner projection logic |
| Change ref assignment rules | `runtime.py` | `upsert_nodes()` assigns `r*` / `e*` prefixes; `ref_item()` looks them up |
| Change session persistence format | `session_store.py` | `read_json` / `write_json` helpers; backward-compat migrations in `load_state` |
| Change browser restore logic | `session.py` | `_restore_tab()` prefers live tab list over persisted `last_tab_id` |
| Add an error code | `errors.py` | Extend `CliError`, assign unique `exit_code`, keep `code` snake_case |

## CONVENTIONS

- `from __future__ import annotations` is required in every module
- Imports: stdlib → `dp_cli.*` (no third-party imports at module level in `cli.py`)
- `CliService` receives `SessionManager` and `DrissionPageAdapter` via constructor for testability
- `RuntimeContext` is a contextmanager: `with self._with_runtime(...) as runtime:`
- Snapshot artifacts are written to `.dpcli/snapshots/<session>/` with timestamped filenames
- Refs are stable per `(runtime_id, page_id)`; changing either invalidates all refs

## ANTI-PATTERNS (THIS PACKAGE)

- Do **not** let `click`/`type` accept container refs — `InvalidRefTypeError` is the contract
- Do **not** expose raw `Chromium`/`ChromiumPage` objects outside `adapter.py`
- Do **not** persist secrets or credentials in `.dpcli/` JSON files
- Do **not** change the JSON response shape `{ok, session, action, data, error}`
- Do **not** add packaging metadata (`pyproject.toml`, `setup.py`) without repo-level discussion
