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

## Global Options

All commands support these options:

- `--session <name>` — Session name (default: `default`)
- `--headless` — Run browser without GUI

## JSON Output Contract

Every command returns the same top-level JSON shape:

```json
{
  "ok": true,
  "session": "demo",
  "action": "snapshot",
  "data": { ... },
  "error": null
}
```

On error:

```json
{
  "ok": false,
  "session": "demo",
  "action": "click",
  "data": null,
  "error": {
    "code": "ref_stale",
    "message": "Element ref 'e12' is stale for the current runtime or page.",
    "details": {
      "ref": "e12",
      "expected_page_id": "page_xxx",
      "actual_page_id": "page_yyy"
    }
  }
}
```

## Error Codes

| Exit Code | Code | Description |
|-----------|------|-------------|
| 1 | `unexpected_error` | General unexpected error |
| 2 | `browser_config_error` | Browser configuration failed |
| 3 | `element_not_found` | Target element not found on page |
| 4 | `invalid_input` | Missing or invalid command arguments |
| 5 | `ref_not_found` | Ref does not exist in this session |
| 6 | `ref_stale` | Ref belongs to a previous page/runtime |
| 7 | `invalid_ref_type` | Container ref used where element ref required |
| 8 | `element_not_interactable` | Element exists but cannot be interacted with |

## Commands

### `open` — Open a URL

Open a page in the session browser.

```bash
python -m dp_cli open https://example.com --session demo
```

**Output:**

```json
{
  "ok": true,
  "session": "demo",
  "action": "open",
  "data": {
    "page": {
      "url": "https://example.com",
      "title": "Example Domain"
    }
  },
  "error": null
}
```

---

### `snapshot` — Capture page structure

Return a structured page snapshot with semantic node discovery.

```bash
# Default planner view (low-token, agent-friendly)
python -m dp_cli snapshot --session demo

# Full discovery mode (all nodes)
python -m dp_cli snapshot --session demo --mode full

# Expand a container subtree
python -m dp_cli snapshot r5 --session demo --depth 3

# Extract mode for data extraction
python -m dp_cli snapshot --session demo --mode extract
```

**Options:**
- `[ref]` — Optional container ref to expand (e.g., `r5`)
- `--mode full|agent_summary|extract` — Snapshot mode (default: `agent_summary`)
- `--depth <N>` — Discovery depth for subtree expansion

**Output (agent_summary mode):**

```json
{
  "ok": true,
  "session": "demo",
  "action": "snapshot",
  "data": {
    "schema_version": "0.6",
    "mode": "agent_summary",
    "page": {
      "url": "https://example.com",
      "title": "Example Domain"
    },
    "page_identity": {
      "runtime_id": "rt_abc123",
      "page_id": "page_def456",
      "snapshot_id": "snap_ghi789",
      "snapshot_seq": 1
    },
    "scope": "page",
    "root_ref": null,
    "depth": null,
    "index": {
      "interactable_elements": [
        {"ref": "e1", "role": "link", "name": "More information"},
        {"ref": "e2", "role": "button", "name": "Submit"}
      ],
      "surface_index": [
        {"ref": "r1", "ref_type": "container", "role": "search", "name": "Search", "child_count": 3, "in_viewport": true, "interactable_now": false},
        {"ref": "e1", "ref_type": "element", "role": "link", "name": "More information", "child_count": 0, "in_viewport": true, "interactable_now": true}
      ],
      "deep_index": [
        {"ref": "r2", "ref_type": "container", "role": "generic", "name": "", "text": "Footer copyright text...", "in_viewport": false}
      ],
      "tree": {
        "roots": ["r1", "r2"],
        "parent_map": {"e1": "r1", "e2": "r1"},
        "children_map": {"r1": ["e1", "e2"]}
      },
      "stats": {
        "total_nodes": 42,
        "surface_count": 12,
        "deep_count": 30,
        "in_viewport": 20,
        "offscreen": 22,
        "interactable_now": 8
      }
    }
  },
  "error": null
}
```

---

### `find` — Find elements

Find elements by CSS locator or text content.

```bash
# Find by CSS locator
python -m dp_cli find --session demo --locator "tag:a"
python -m dp_cli find --session demo --locator "#search-input"

# Find by text content
python -m dp_cli find --session demo --text "Search"
python -m dp_cli find --session demo --text "Next page"
```

**Output:**

```json
{
  "ok": true,
  "session": "demo",
  "action": "find",
  "data": {
    "page": { "url": "...", "title": "..." },
    "page_identity": { "runtime_id": "...", "page_id": "..." },
    "count": 3,
    "nodes": [
      {
        "ref": "e1",
        "ref_type": "element",
        "tag": "a",
        "role": "link",
        "name": "",
        "text": "More information",
        "locator": "xpath:/html/body/div/p[2]/a",
        "visibility": {
          "visible": true,
          "in_viewport": true,
          "interactable_now": true
        }
      }
    ],
    "query": {
      "locator": "tag:a",
      "text": null
    }
  },
  "error": null
}
```

---

### `click` — Click an element

Click an element by ref or locator.

```bash
# Click by ref (preferred)
python -m dp_cli click --session demo --ref e12

# Click by locator
python -m dp_cli click --session demo --locator "#submit-button"
```

**Output:**

```json
{
  "ok": true,
  "session": "demo",
  "action": "click",
  "data": {
    "page": { "url": "...", "title": "..." },
    "target": {
      "ref": "e12",
      "locator": "xpath:/html/body/form/button"
    },
    "target_state": {
      "visible": true,
      "in_viewport": true,
      "interactable_now": true
    }
  },
  "error": null
}
```

**Error example (ref_stale):**

```json
{
  "ok": false,
  "session": "demo",
  "action": "click",
  "data": null,
  "error": {
    "code": "ref_stale",
    "message": "Element ref 'e12' is stale for the current runtime or page. Re-run snapshot or find first.",
    "details": {
      "ref": "e12",
      "expected_page_id": "page_xxx",
      "actual_page_id": "page_yyy"
    }
  }
}
```

---

### `type` — Type text into an element

Type text into a form field by ref or locator.

```bash
# Type by ref (preferred)
python -m dp_cli type --session demo --ref e11 --text "Hello World"

# Type by locator
python -m dp_cli type --session demo --locator "#search-input" --text "python tutorial"
```

**Output:**

```json
{
  "ok": true,
  "session": "demo",
  "action": "type",
  "data": {
    "page": { "url": "...", "title": "..." },
    "target": {
      "ref": "e11",
      "locator": "xpath:/html/body/form/input[1]"
    },
    "target_state": {
      "visible": true,
      "in_viewport": true,
      "interactable_now": true
    },
    "typed_text": "Hello World"
  },
  "error": null
}
```

---

### `expand` — Expand container subtree

Expand a container ref to reveal its child nodes.

```bash
python -m dp_cli expand r5 --session demo --depth 3
```

**Output:**

```json
{
  "ok": true,
  "session": "demo",
  "action": "expand",
  "data": {
    "page": { "url": "...", "title": "..." },
    "page_identity": { "runtime_id": "...", "page_id": "..." },
    "target_ref": "r5",
    "mode": "full",
    "count": 15,
    "nodes": [
      {
        "ref": "e20",
        "ref_type": "element",
        "tag": "a",
        "role": "link",
        "text": "Article Title",
        "locator": "xpath:/html/body/div[3]/div[1]/a"
      }
    ]
  },
  "error": null
}
```

---

### `list-items` — List items in a group

List items within a group/container.

```bash
python -m dp_cli list-items r3 --session demo --sample-size 5
```

**Output:**

```json
{
  "ok": true,
  "session": "demo",
  "action": "list-items",
  "data": {
    "page": { "url": "...", "title": "..." },
    "group_ref": "r3",
    "group_kind": "list",
    "item_count": 10,
    "sample_items": [
      { "item_ref": "e5", "fields": {} },
      { "item_ref": "e6", "fields": {} },
      { "item_ref": "e7", "fields": {} }
    ],
    "schema_hints": {
      "title": "text",
      "author": "text"
    }
  },
  "error": null
}
```

---

### `extract` — Extract structured data

Extract structured data from a group/container.

```bash
# Extract all items from a group
python -m dp_cli extract r3 --session demo

# Extract with schema hints
python -m dp_cli extract r3 --session demo --schema title author url

# Extract sample only (first 3 items)
python -m dp_cli extract r3 --session demo --sample-only
```

**Output:**

```json
{
  "ok": true,
  "session": "demo",
  "action": "extract",
  "data": {
    "group_ref": "r3",
    "item_count": 10,
    "fields": ["title", "author", "url"],
    "items": [
      {
        "title": "First Article",
        "author": "John Doe",
        "url": "https://example.com/1"
      },
      {
        "title": "Second Article",
        "author": "Jane Smith",
        "url": "https://example.com/2"
      }
    ]
  },
  "error": null
}
```

---

### `resolve-locator` — Resolve ref to locator

Get locator candidates for a ref.

```bash
python -m dp_cli resolve-locator --session demo --ref e12
```

**Output:**

```json
{
  "ok": true,
  "session": "demo",
  "action": "resolve-locator",
  "data": {
    "ref": "e12",
    "fingerprint": "fp_abc123",
    "confidence": 0.9,
    "locator_candidates": [
      "xpath:/html/body/form/button",
      "css:form > button[type=submit]"
    ],
    "re_resolve_result": "matched"
  },
  "error": null
}
```

---

### `eval` — Evaluate JavaScript

Execute JavaScript on the page.

```bash
python -m dp_cli eval "document.title" --session demo
python -m dp_cli eval "document.querySelectorAll('a').length" --session demo
```

**Output:**

```json
{
  "ok": true,
  "session": "demo",
  "action": "eval",
  "data": {
    "result": "Example Domain"
  },
  "error": null
}
```

---

### `session inspect` — Inspect session state

Return agent-friendly session state.

```bash
python -m dp_cli session inspect --session demo
```

**Output:**

```json
{
  "ok": true,
  "session": "demo",
  "action": "session.inspect",
  "data": {
    "session_name": "demo",
    "session_id": "sess_abc123",
    "runtime": {
      "runtime_id": "rt_def456",
      "status": "running",
      "browser_pid": 12345,
      "port": 9333,
      "headless": false,
      "last_seen_at": "2026-04-23T10:30:00Z"
    },
    "page": {
      "tab_id": "tab_ghi789",
      "url": "https://example.com",
      "title": "Example Domain",
      "page_id": "page_jkl012",
      "snapshot_id": "snap_mno345",
      "snapshot_seq": 3
    },
    "ref_count": 25,
    "container_ref_count": 8,
    "element_ref_count": 17,
    "last_snapshot_file": ".dpcli/snapshots/demo/snap_mno345.json",
    "last_snapshot_mode": "agent_summary"
  },
  "error": null
}
```

---

## Snapshot Model Details

### Node Structure

Each discovered node in snapshot includes:

```json
{
  "ref": "e12",
  "ref_type": "element",
  "id": "search-input",
  "tag": "input",
  "role": "textbox",
  "name": "Search",
  "text": "",
  "value": "",
  "placeholder": "Search...",
  "href": "",
  "input_type": "text",
  "title": "",
  "aria_label": "Search",
  "alt": "",
  "label": "Search",
  "locator": "xpath:/html/body/div/form/input",
  "depth": 3,
  "bounds": {
    "x": 100.0,
    "y": 200.0,
    "width": 300.0,
    "height": 40.0
  },
  "visibility": {
    "visible": true,
    "in_viewport": true,
    "interactable_now": true
  },
  "context": {
    "landmark": "search",
    "heading": "",
    "form": "search-form",
    "list": "",
    "dialog": ""
  },
  "states": {
    "disabled": false,
    "checked": false,
    "selected": false,
    "expanded": false
  }
}
```

### Ref Rules

- `r*` — Semantic container ref (groups, lists, regions)
- `e*` — Interactive element ref (buttons, links, inputs)

**Command constraints:**

| Command | Accepts `r*` | Accepts `e*` |
|---------|-------------|-------------|
| `snapshot` | Yes | Yes |
| `expand` | Yes | No |
| `list-items` | Yes | No |
| `extract` | Yes | No |
| `click` | No | Yes |
| `type` | No | Yes |
| `resolve-locator` | Yes | Yes |

---

## Core Workflow Example

### Complete automation example

```bash
# 1. Open a page
python -m dp_cli open https://github.com/login --session github --headless

# 2. Take a snapshot to discover elements
python -m dp_cli snapshot --session github --headless
# -> Returns e1 (username input), e2 (password input), e3 (sign-in button)

# 3. Type credentials
python -m dp_cli type --session github --headless --ref e1 --text "my-username"
python -m dp_cli type --session github --headless --ref e2 --text "my-password"

# 4. Click sign-in
python -m dp_cli click --session github --headless --ref e3

# 5. Re-snapshot after navigation
python -m dp_cli snapshot --session github --headless
```

### Data extraction example

```bash
# 1. Open Hacker News
python -m dp_cli open https://news.ycombinator.com --session hn --headless

# 2. Take snapshot to find the news list container
python -m dp_cli snapshot --session hn --headless
# -> Returns r1 (news list container)

# 3. List items in the container
python -m dp_cli list-items r1 --session hn --headless --sample-size 5

# 4. Extract structured data
python -m dp_cli extract r1 --session hn --headless --schema title url author
```

---

## Action Safety

`click` and `type` do more than simple selector execution:

- Validate that the ref still belongs to the current runtime and page
- Reject stale refs with `ref_stale` (exit code 6)
- Reject container refs with `invalid_ref_type` (exit code 7)
- Verify that the target element is interactable
- Auto-scroll into view before action when needed
- Return `element_not_interactable` (exit code 8) when the element exists but cannot be acted on

**When you get `ref_stale`:**

The page likely navigated or changed. Take a new snapshot to get fresh refs:

```bash
python -m dp_cli snapshot --session demo --headless
```

---

## Files and Storage

Session state lives under:

```text
.dpcli/sessions/<session-name>/
  meta.json      — Session metadata (port, browser path, runtime info)
  state.json     — Ref mappings, active page, snapshot history
  profile/       — Browser user data directory
```

Snapshot artifacts live under:

```text
.dpcli/snapshots/<session-name>/
  <snapshot-id>.json  — Full snapshot data
```

To reset a session (clear all refs and state):

```bash
rm -rf .dpcli/sessions/<session-name>
```

---

## Environment Configuration

Copy `.env` template and fill in your API key:

```bash
cp .env .env.local
```

```env
# OpenAI-compatible API configuration
OPENAI_API_KEY=your-api-key-here
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=gpt-4o-mini
```

Optional environment variables:

- `DPCLI_BROWSER_PATH` — Override browser executable path
- `DPCLI_RUN_PUBLIC_SMOKE` — Enable public network smoke tests

---

## Scripts

Local semantic workflow smoke test:

```bash
python scripts/test_local_cli.py
```

Public smoke test:

```bash
python scripts/test_public_smoke.py
```

Agent loop test with natural language goals:

```bash
python tests/test_agent_computor.py --scenario automation
```

Run with visible browser:

```bash
# Edit test_agent_computor.py: set headless=False in TestRunner
python tests/test_agent_computor.py --scenario automation
```

## Tests

Run local regression tests:

```bash
pytest -q tests/test_cli_local.py
pytest -q tests
```

Enable public smoke tests explicitly:

```bash
# Windows
set DPCLI_RUN_PUBLIC_SMOKE=1
pytest -q tests/test_public_smoke.py

# Linux/macOS
export DPCLI_RUN_PUBLIC_SMOKE=1
pytest -q tests/test_public_smoke.py
```

## Current Scope

This version intentionally focuses on the minimum reliable contract for agents:

- Semantic snapshot with full-page discovery
- Planner projection with pinned controls
- Ref-driven interaction (`r*` containers, `e*` elements)
- Stable session identity (session_id, runtime_id, page_id, snapshot_id)
- Stale ref detection and recovery
- Full-page `find` fallback
- Visible/interactable execution safety
- Group compression and schema extraction
- Container expansion for subtree exploration

## Architecture

```
dp_cli/
├── cli.py          — Argument parsing, JSON dispatch, main entry
├── service.py      — CliService: command orchestration
├── adapter.py      — DrissionPageAdapter: DOM snapshot via injected JS
├── session.py      — SessionManager: browser lifecycle + tab restore
├── runtime.py      — RuntimeContext: ref mapping + page identity
├── session_store.py — SessionStore: JSON persistence + browser discovery
├── models.py       — Dataclasses: state, nodes, bounds, visibility
├── errors.py       — CliError hierarchy with structured exit codes
├── compressor.py   — DOM node grouping and compression
├── projector.py    — Planner view and extraction projectors
├── grouper.py      — Group kind detection and field schema extraction
├── locator.py      — Locator candidate generation
└── fingerprint.py  — Node fingerprinting for stable ref resolution
```
