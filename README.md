# dp_cli

`dp_cli` is an Agent-first CLI wrapper around DrissionPage.

The current MVP focuses on a Playwright-CLI-style workflow:

1. `open`
2. `snapshot`
3. choose a `ref` from semantic nodes
4. `click` / `type` by `ref`
5. re-snapshot when the page changes

The key design choice is: **the main contract is semantic snapshot + ref**, not hand-written Chinese area descriptions.

## Install

```bash
conda activate dp-cli
pip install DrissionPage pytest langchain-openai
```

If you need to create the environment first:

```bash
conda create -n dp-cli python=3.11
conda activate dp-cli
pip install DrissionPage pytest langchain-openai
```

## Commands

All commands return JSON with the same top-level shape:

- `ok`
- `session`
- `action`
- `data`
- `error`

Common options:

- `--session`
- `--headless`

Available commands:

- `open`
- `snapshot`
- `find`
- `click`
- `type`
- `session inspect`

## Snapshot model

`snapshot` now uses a two-layer contract:

1. full-page semantic discovery
2. a low-token planner projection

By default, the CLI returns the planner projection instead of dumping every discovered node.

Example:

```bash
python -m dp_cli snapshot --session demo --headless
python -m dp_cli snapshot --session demo --headless --view full
python -m dp_cli snapshot r5 --session demo --headless --depth 3
python -m dp_cli snapshot r5 --session demo --headless --depth 3 --view full
```

Snapshot payload fields:

- `mode`
  - currently always `semantic`
- `scope`
  - `page` or `subtree`
- `root_ref`
  - `null` for page snapshot
  - a ref when expanding a subtree
- `depth`
- `artifact_file`

Default planner view fields:

- `planner_view.pinned_controls`
  - critical controls that must never be omitted
  - examples: navigation links, pagination controls, search / submit buttons
- `planner_view.viewport_nodes`
  - the most relevant nodes already in the current viewport
- `planner_view.condensed_groups`
  - compressed summaries for large repeated groups such as card grids
- `planner_view.stats`
- `planner_view.omitted_summary`

Use `--view full` when you want the complete discovered node list.

Each discovered node includes:

- `ref`
- `ref_type`
  - `container` or `element`
- `role`
- `name`
- `text`
- `states`
- `visibility`
  - `visible`
  - `in_viewport`
  - `interactable_now`
- `context`
  - `landmark`
  - `heading`
  - `form`
  - `list`
  - `dialog`
- `bounds`
- `locator`

Ref rules:

- `r*`
  - semantic container ref
- `e*`
  - interactive element ref

Command rules:

- `snapshot` accepts `r*` or `e*`
- `click` / `type` only accept `e*`
- using a container ref with `click` / `type` returns `invalid_ref_type`

## Core workflow

### Open a page

```bash
python -m dp_cli open https://example.com --session demo --headless
```

### Take the default planner snapshot

```bash
python -m dp_cli snapshot --session demo --headless
```

### Take the full semantic discovery snapshot

```bash
python -m dp_cli snapshot --session demo --headless --view full
```

### Expand a container subtree

```bash
python -m dp_cli snapshot r5 --session demo --headless --depth 3
python -m dp_cli snapshot r5 --session demo --headless --depth 3 --view full
```

### Find visible interactive elements

`find --text` now searches the full discovered page graph, not just the current viewport.

By text:

```bash
python -m dp_cli find --session demo --headless --text "Movies"
python -m dp_cli find --session demo --headless --text "Next page"
```

By locator:

```bash
python -m dp_cli find --session demo --headless --locator "#search-input"
python -m dp_cli find --session demo --headless --locator "tag:a"
```

### Click by ref or locator

```bash
python -m dp_cli click --session demo --headless --ref e12
python -m dp_cli click --session demo --headless --locator "#next-page"
```

### Type by ref or locator

```bash
python -m dp_cli type --session demo --headless --ref e11 --text "Agentic CLI"
python -m dp_cli type --session demo --headless --locator "#search-input" --text "Agentic CLI"
```

### Inspect session state

```bash
python -m dp_cli session inspect --session demo --headless
```

## Action safety

`click` and `type` do more than simple selector execution:

- validate that the ref still belongs to the current runtime and page
- reject stale refs with `ref_stale`
- reject container refs with `invalid_ref_type`
- verify that the target element is interactable
- auto-scroll into view before action when needed
- return `element_not_interactable` when the element exists but cannot be acted on

## Files and storage

Session state lives under:

```text
.dpcli/sessions/<session-name>/
```

Snapshot artifacts live under:

```text
.dpcli/snapshots/<session-name>/
```

## Scripts

Local semantic workflow smoke test:

```bash
python scripts/test_local_cli.py
```

Public smoke test:

```bash
python scripts/test_public_smoke.py
```

Minimal natural-language agent loop:

```bash
python scripts/test_min_agent_loop.py
```

Before running the agent loop script, fill these fields in [scripts/test_min_agent_loop.py](/E:/GitHub/Repositories/drissionpage-cli/scripts/test_min_agent_loop.py:20):

- `OPENAI_CONFIG["api_key"]`
- `OPENAI_CONFIG["base_url"]`
- `OPENAI_CONFIG["model"]`

The script uses `langchain_openai.ChatOpenAI` and drives `dp_cli` through:

- planner snapshot
- ref selection
- `find --text` fallback when the planner view does not expose the target yet
- `click` / `type`

## Tests

Run local regression tests:

```bash
pytest -q tests/test_cli_local.py
pytest -q tests
```

Enable public smoke tests explicitly:

```bash
set DPCLI_RUN_PUBLIC_SMOKE=1
pytest -q tests/test_public_smoke.py
```

## Current scope

This version intentionally focuses on the minimum reliable contract for agents:

- semantic snapshot
- planner projection with pinned controls
- ref-driven interaction
- stable session identity
- stale ref detection
- full-page find fallback
- visible/interactable execution safety

Not implemented yet:

- `wait`
- `inspect`
- `screenshot`
- `press`
- visual understanding as the main interaction path
