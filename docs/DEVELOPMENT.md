# Substack CLI — Developer Guide

A Python CLI wrapping Substack's unofficial private API. This document
covers the internal architecture, auth model, and how to extend the CLI.
For install and usage, see the [root README](../README.md).

## Quick Start

```bash
# Install deps
pip install -r requirements.txt

# Run tests
pip install -r requirements-dev.txt
pytest -q

# Install as a system binary (optional)
ln -s "$(pwd)/substack" /usr/local/bin/substack
```

## Module Architecture

| Module | Owns | Imports from |
|---|---|---|
| `models.py` | Permissive TypedDicts + `extract_list()` / `extract_pagination_meta()` | nothing |
| `config.py` | `~/.config/substack-cli/config.json` load/save + `config` subapp | nothing |
| `auth.py` | Credential resolution (env → config), headers, redaction, hints | `config.py` |
| `client.py` | `SubstackClient` (HTTP, retries, throttle, errors), `emit_error()`, `output()` | `auth.py`, `models.py` |
| `read.py` | All GET/read commands (archive, posts, feed, comments, search, stats, analytics, categories/sections, category leaderboard) | `client.py`, `app.py` (registers commands) |
| `publish.py` | Draft CRUD, publish/schedule lifecycle, image upload, MD→ProseMirror | `client.py`, `app.py` (registers commands) |
| `manage.py` | Comments, reactions, subscribers, recommendations, tags, pub settings | `client.py`, `app.py` (registers commands) |
| `notes.py` | Substack Notes CRUD (create/list/get/delete) + note bodyJson builder | `client.py`, `app.py`, `publish.py` (reuses `_parse_inline`) |
| `app.py` | Root Typer app, subapp wiring, `config test` command, `main()` error wrapper | all (composition root) |

## Import Graph

```
app.py ──registers──> config.py
                  └-> read.py    -> client.py -> auth.py -> config.py
                  └-> publish.py -> client.py
                  └-> manage.py  -> client.py
                  └-> notes.py   -> client.py, publish.py (_parse_inline)

Entry point (substack script):
  substack -> imports config, read, publish, manage, notes (registers commands)
           -> imports app.main() -> calls app()
```

**Circular import avoidance**: The entry point script (`substack`) imports
the command modules BEFORE calling `main()`. `app.py` itself does NOT
import the command modules at module level to avoid circular dependencies
(config.py → app.py → config.py). Tests that exercise the CLI must import
the command modules explicitly:

```python
from substack_cli import config, read, publish, manage, notes  # ensure registration
from substack_cli.app import app
```

## Auth Model

- **Cookies, not tokens**: `connect.sid` (primary), `substack.sid` (legacy), `substack.lli` (optional)
- **Resolution order**: env var → config file → AuthError
  - `SUBSTACK_COOKIES_STRING` env var → `~/.config/substack-cli/config.json` `cookies_string` key
  - `SUBSTACK_PUBLICATION_URL` env var → config `publication_url` key
- **Write gate**: `SUBSTACK_ENABLE_WRITE=true` env var OR `enable_write: true` config key
- **`--yes` flag**: required for `drafts delete/publish`, `comments delete`, `subscribers remove`, `recommendations remove`, `tags delete`, `notes create` (publishes immediately, no edit/undo), `notes delete`

## Output Conventions

- Default: compact JSON to stdout
- `--pretty`: Rich Panel/Table to stdout (for human consumption)
- Errors: `{"error": true, "message": "...", "status_code": ...}` JSON to stderr, exit 1 — ALWAYS, even under `--pretty`

## Two-Host Routing

- **Host "P"** (publication subdomain): default — `https://{subdomain}.substack.com`
- **Host "A"** (substack.com bare): `whoami`, `categories`, `comments delete --host A`

## How to Add a New Endpoint

1. **Write the test**: Create `tests/test_<module>_<operation>.py`. Use `respx` to mock the HTTP response. Follow the existing test patterns (see `test_read_archive.py` for a simple example, `test_publish_drafts_crud.py` for a complex one).

2. **Implement the function**: Add the function to the appropriate module (`read.py` for GET-only, `publish.py` for draft/publish ops, `manage.py` for comments/subscribers/tags). Use the existing `SubstackClient.get/post/put/delete` methods — they handle retries, rate limiting, and error redaction automatically.

3. **Register the CLI command**: Add a `@app.command(...)` or `@<subapp>.command(...)` function in the same module. Follow the pattern: resolve auth → check write gate → create client → call function → output result → handle errors.

4. **Run tests**: `pytest -q` from `skills/substack/scripts/`. All tests must pass.

5. **Update docs**: Add the command to `SKILL.md` (Claude-facing) and `references/substack-api.md` (API reference).

## Test Strategy

- **No real network calls**: all HTTP mocked via `respx`
- **No real config file**: `isolated_config` fixture redirects `CONFIG_PATH` to tmp
- **No real env vars**: `isolated_config` clears `SUBSTACK_*` env vars
- **No real sleep**: `no_sleep` fixture patches `time.sleep` for retry/throttle tests
- **234 test cases** across 22 test files (including integration/e2e tests)

## Deferred / Out of Scope for v1

See `references/substack-api.md` § Deferred. Highlights:
- Substack Notes *editing* — create/list/get/delete are implemented (`notes` subapp); the API has no edit endpoint (notes publish immediately, no draft/undo), so "edit" means delete-then-recreate
- Full Markdown→ProseMirror (lists, images, footnotes, paywall markers, embeds)
- Messaging/Chat/DMs, Stripe/pledges, cross-publication discovery
- OAuth / programmatic login flows