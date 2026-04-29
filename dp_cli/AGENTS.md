# dp_cli PACKAGE KNOWLEDGE BASE

**Generated:** 2026-04-29

## OVERVIEW

Core package: CLI dispatch, browser adapter, session/runtime management, and ref-driven snapshot logic.

## STRUCTURE

```
dp_cli/
├── __main__.py       # `python -m dp_cli` entrypoint
├── cli.py            # argparse setup, JSON dispatch, `main()`
├── service.py        # `CliService` — command orchestration (~1500 lines)
├── adapter.py        # `DrissionPageAdapter` — DOM snapshot via embedded JS (~920 lines)
├── session.py        # `SessionManager` — browser lifecycle + tab restore
├── runtime.py        # `RuntimeContext` — ref mapping + page identity
├── session_store.py  # `SessionStore` — JSON persistence + browser discovery
├── models.py         # dataclasses: state, nodes, bounds, visibility + score_text_match()
├── errors.py         # `CliError` hierarchy with structured exit codes
├── compressor.py     # DOM node grouping and compression
├── projector.py      # Planner view and extraction projectors
├── grouper.py        # Group kind detection and field schema extraction
├── locator.py        # Locator candidate generation (ref → CSS/XPath)
├── fingerprint.py    # Node fingerprinting for stable ref resolution
└── ai_extractor.py   # LLM-powered detail extraction
```

## WHERE TO LOOK

| Task | File | Notes |
|------|------|-------|
| Add a CLI command | `cli.py` | Register subparser in `_COMMAND_MAP`, wire into `dispatch()`, keep JSON contract |
| Change command behavior | `service.py` | `CliService` methods are the primary boundary |
| Change snapshot discovery | `adapter.py` | Contains `SNAPSHOT_SCRIPT` (injected JS) and planner projection logic |
| Change ref assignment rules | `runtime.py` | `upsert_nodes()` assigns `r*` / `e*` prefixes; `ref_item()` looks them up |
| Change session persistence format | `session_store.py` | `read_json` / `write_json` helpers; backward-compat migrations in `load_state` |
| Change browser restore logic | `session.py` | `_restore_tab()` prefers live tab list over persisted `last_tab_id` |
| Add an error code | `errors.py` | Extend `CliError`, assign unique `exit_code`, keep `code` snake_case |
| Modify DOM compression | `compressor.py` | Node grouping logic |
| Change planner/extract view | `projector.py` | `build_planner_index()`, `build_extract_index()` |
| Change group detection | `grouper.py` | `detect_group_kind()`, `extract_field_schema()` |
| Change locator resolution | `locator.py` | Generates CSS/XPath candidates from node attributes |
| Modify fingerprinting | `fingerprint.py` | **WARNING**: changing hash inputs invalidates ALL stored refs |

## CONVENTIONS

- `from __future__ import annotations` is required in every module
- Imports: stdlib → `dp_cli.*` (no third-party imports at module level in `cli.py`)
- `CliService` receives `SessionManager` and `DrissionPageAdapter` via constructor for testability
- `RuntimeContext` is a contextmanager: `with self._with_runtime(...) as runtime:`
- Snapshot artifacts are written to `.dpcli/snapshots/<session>/` with timestamped filenames
- Refs are stable per `(runtime_id, page_id)`; changing either invalidates all refs
- 12 CLI subcommands: `open`, `snapshot`, `find`, `click`, `type`, `expand`, `list-items`, `extract`, `resolve-locator`, `eval`, `batch-detail-extract`, `session`

## ANTI-PATTERNS (THIS PACKAGE)

- Do **not** let `click`/`type` accept container refs — `InvalidRefTypeError` is the contract
- Do **not** expose raw `Chromium`/`ChromiumPage` objects outside `adapter.py`
- Do **not** persist secrets or credentials in `.dpcli/` JSON files
- Do **not** change the JSON response shape `{ok, session, action, data, error}`
- Do **not** add packaging metadata (`pyproject.toml`, `setup.py`) without repo-level discussion
- Do **not** modify `fingerprint.py` hash functions without a migration strategy for existing sessions

## KNOWN DEBT

- `session_store.py` line 161: legacy field migrations (`refs`→`element_refs`) scheduled for removal after 2026-07-01
- `fingerprint.py`: 6 stubbed no-op methods awaiting real implementation
- `models.py`: `score_text_match()` is business logic mixed with dataclasses
