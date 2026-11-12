# substack-cli

An [AFK](https://github.com/griffinwork40/agent-afk) plugin (and standalone CLI) for reading and managing [Substack](https://substack.com) publications, wrapping Substack's undocumented private API — the same `/api/v1/` endpoints the Substack web app itself uses.

Read-only endpoints (archive, single post, RSS feed) work anonymously. Everything else — publishing drafts, moderating comments, managing subscribers, pulling analytics — uses your browser session cookie.

> ⚠️ **Unofficial.** Substack publishes no public API for content, publishing, or subscriber automation. This tool reverse-engineers the private endpoints, so it can break without notice if Substack changes them. Use it on publications you own, and review Substack's Terms of Use before automating against your account.

## Install

### As an AFK plugin (recommended)

```bash
afk plugin install griffinwork40/substack-cli
```

This registers the `substack` skill, available in every AFK session. The CLI's Python dependencies (typer, httpx, rich) install once from the plugin's bundled requirements file:

```bash
pip install -r ~/.afk/plugins/substack-cli/skills/substack/scripts/requirements.txt
```

### As a standalone CLI

Requires Python 3.11+.

```bash
git clone https://github.com/griffinwork40/substack-cli.git
cd substack-cli/skills/substack/scripts
pip install -r requirements.txt

# Optional: put `substack` on your PATH
ln -s "$(pwd)/substack" /usr/local/bin/substack
substack --help
```

## Features

- **Read (no auth):** list the archive, fetch a single post, pull the RSS feed.
- **Search & discovery:** search a publication's archive, list categories and sections, and pull cross-publication category leaderboards (top 25 publications by paid subscribers, total reach, or growth — no auth needed).
- **Publishing:** create / edit / delete drafts, publish or schedule posts, upload images (Markdown → ProseMirror conversion).
- **Moderation:** manage comments and reactions — create, delete, react, **read who liked a comment or note reply** (`comments reactions`), report spam/impersonation, ban commenters, and look up existing bans.
- **Notes:** create, list (paginated `--all` with `--since` + `--max-pages`), read, delete, and **fetch replies** (`notes replies --all --descendants`) on Substack Notes. All Notes endpoints use `substack.com` (host A), not the publication subdomain.
- **Audience:** manage subscribers, cross-publication recommendations, and post tags.
- **Analytics:** per-post and publication-level stats.
- **Machine-friendly:** compact JSON to stdout by default (`--pretty` for a Rich table/panel); errors are JSON on stderr with a nonzero exit code.

Full command list: `substack --help`.

## Authentication

Authenticated operations need your Substack **session cookie** (Substack uses cookies, not API tokens). Full extraction walkthrough: [`skills/substack/references/auth-setup.md`](skills/substack/references/auth-setup.md).

In short:

```bash
# Extract connect.sid (+ optionally substack.sid / substack.lli) from your
# browser DevTools while logged into Substack, then:
export SUBSTACK_COOKIES_STRING="connect.sid=...; substack.sid=..."
export SUBSTACK_PUBLICATION_URL="https://yourname.substack.com"

# Or persist to ~/.config/substack-cli/config.json:
substack config set-cookies "connect.sid=...; substack.sid=..."

# Verify:
substack whoami
```

**Write operations are gated.** Any mutating command (publish, delete, remove) requires `SUBSTACK_ENABLE_WRITE=true` (or `enable_write: true` in config) **and** an explicit `--yes` on destructive commands. This is a deliberate guardrail against accidental changes to a live publication.

Cookies are stored at `~/.config/substack-cli/config.json` — **outside** this repo — and are never committed. Treat the cookie string like a password; it grants full access to your account.

## Repository layout

```
substack-cli/
├── .claude-plugin/plugin.json        # AFK plugin manifest
├── skills/substack/
│   ├── SKILL.md                      # skill manifest (agent-facing usage)
│   ├── references/                   # API map + auth-setup guide
│   └── scripts/                      # the Python CLI (substack_cli/ + tests/)
├── docs/DEVELOPMENT.md               # architecture & contributor guide
├── README.md
└── LICENSE
```

## Development

```bash
cd skills/substack/scripts
pip install -r requirements-dev.txt
pytest -q          # 439 tests, all HTTP mocked — no real network calls
```

Architecture, auth model, two-host routing, and a guide to adding new endpoints live in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md). The API surface is mapped in [`skills/substack/references/substack-api.md`](skills/substack/references/substack-api.md).

## Notes commands

All Notes endpoints hit `https://substack.com` (host A) — a publication URL is not required.

### Fetch replies for a Note

```bash
# Single page (raw API envelope)
substack notes replies <NOTE_ID>

# All pages merged
substack notes replies <NOTE_ID> --all

# All pages + recursively expand nested replies
substack notes replies <NOTE_ID> --all --descendants

# Resume from a cursor
substack notes replies <NOTE_ID> --cursor <CURSOR>
```

`NOTE_ID` accepts a plain numeric id (`123`) or the `c-`-prefixed entity key shown by `notes list` (`c-123`).

Default output shape (one page):
```json
{
  "rootComment": {...},
  "commentBranches": [{"comment": {...}, "descendantComments": [...]}],
  "automodHiddenBranches": [...],
  "moreBranches": 1,
  "nextCursor": "tok"
}
```

`--all` output (merged pages):
```json
{
  "rootComment": {...},
  "commentBranches": [...],
  "automodHiddenBranches": [...],
  "moreBranches": 0,
  "nextCursor": null,
  "pages": 2
}
```

`--descendants` recursively fetches any branch whose `comment.children_count` exceeds the number of returned `descendantComments`, replacing `descendantComments` with the full depth-first flattened list. Capped at recursion depth 5 and 200 extra requests; if either cap is hit a top-level `"truncated": true` is added.

### List a user's Notes with pagination (`--all`)

```bash
# All of your notes (paginated)
substack notes list --mine --all

# Stop at a date boundary
substack notes list --mine --all --since 2026-10-01

# Limit pages fetched (default 50)
substack notes list --mine --all --max-pages 10

# Another user's notes
substack notes list --user-id 27965159 --all --since 2026-09-01
```

`--all` calls `GET /api/v1/reader/feed/profile/{user_id}?limit=<limit>&cursor=<c>` (host A) and follows `nextCursor` until it is null, until the oldest item on a page is older than `--since`, or until `--max-pages` pages have been fetched.

Output shape with `--all`:
```json
{"items": [...], "pages": 3, "nextCursor": null}
```

Without `--all`, `notes list` behaviour is unchanged (single request to the reader feed).

## Comment-moderation commands

These commands target scam/impersonation accounts (e.g. "Charlie WhatsApp" reply-
bots). All write commands require `SUBSTACK_ENABLE_WRITE=true` and **default to
dry-run** — they print the exact HTTP request without firing it. Add `--no-dry-run`
(alias `--yes`) to execute.

### Report a comment

```bash
# Dry-run (default) — print request only
substack comments report <COMMENT_ID> \
  --category spam \
  --details "Impersonation scam: Charlie WhatsApp +1672..."

# Execute for real
substack comments report <COMMENT_ID> \
  --category impersonation \
  --details "Fake Charlie account soliciting DMs" \
  --no-dry-run
```

`--category` values (from Substack's reporting flow):
`spam`, `impersonation`, `hate`, `private_information`, `plagiarism`,
`illegal_activity`, `pornography`, `other`.

`--to-substack` / `--no-to-substack`: whether to also escalate to Substack
Trust & Safety (default: `--to-substack`).

### Report a user profile

```bash
substack comments report-user <USER_ID> \
  --category impersonation \
  --details "Impersonating Charlie Garcia to solicit readers" \
  --no-dry-run
```

Routes to `POST https://substack.com/api/v1/users/{user_id}/profile/report`
with `surface: "profile"`.

### Ban a commenter

```bash
# Dry-run
substack comments ban <COMMENT_ID> \
  --user-id <USER_ID> \
  --expiry 1y \
  --visibility visible

# Execute — 1-year ban, keep comments visible
substack comments ban <COMMENT_ID> \
  --user-id <USER_ID> \
  --expiry 1y \
  --no-dry-run

# Permanent ban + delete all comments
substack comments ban <COMMENT_ID> \
  --user-id <USER_ID> \
  --expiry 1000y \
  --visibility delete_all_comments \
  --no-dry-run
```

`--expiry` enum (confirmed from Substack's ban modal): `1d`, `30d`, `90d`, `1y`, `1000y` (`1000y` = "Forever").

`--visibility` enum (confirmed from Substack web bundle):
- `visible` *(default)*: keep the user's existing comments
- `delete_this_comment`: delete only the comment you are banning from
- `delete_all_comments`: delete all of their comments on this publication (irreversible)

> **Note:** Substack's UI reloads the page when `commentVisibility ≠ "visible"`,
> suggesting server-side comment deletion happens asynchronously.

### Look up existing bans for a user (read-only)

```bash
substack comments bans <USER_ID>
```

### Fetch report category tree (read-only)

```bash
substack moderation report-categories --pretty
```

### Fetch comment deletion reason codes (read-only)

```bash
substack moderation delete-reasons --pretty
```

### Recommended first smoke-test for a known scam comment

Replace `<COMMENT_ID>` and `<USER_ID>` with values from `substack comments list`:

```bash
# Step 1 — dry-run report (no write occurs)
SUBSTACK_ENABLE_WRITE=true substack comments report <COMMENT_ID> \
  --category impersonation \
  --details "Scam account impersonating Charlie Garcia, soliciting WhatsApp contact"

# Step 2 — inspect the printed JSON, then fire for real
SUBSTACK_ENABLE_WRITE=true substack comments report <COMMENT_ID> \
  --category impersonation \
  --details "Scam account impersonating Charlie Garcia, soliciting WhatsApp contact" \
  --no-dry-run

# Step 3 — dry-run ban
SUBSTACK_ENABLE_WRITE=true substack comments ban <COMMENT_ID> \
  --user-id <USER_ID> \
  --expiry 1000y \
  --visibility delete_all_comments

# Step 4 — execute ban
SUBSTACK_ENABLE_WRITE=true substack comments ban <COMMENT_ID> \
  --user-id <USER_ID> \
  --expiry 1000y \
  --visibility delete_all_comments \
  --no-dry-run
```

### Unverified / open questions

| Item | Status |
|------|--------|
| `report_context` body shape | Not found in bundle. Field accepted as optional `--report-context <JSON>` string; omit in most cases. |
| `expiry` format | Confirmed enum `1d`/`30d`/`90d`/`1y`/`1000y` from the ban modal's select options. Not yet live-verified. |
| `commentVisibility` enum | Confirmed `visible` / `delete_this_comment` / `delete_all_comments` from the ban modal's select options. |
| Host for comment report | Publication host (`charliepgarcia.substack.com`). Notes UI uses `substack.com` — if a comment is on Notes, the CLI may need `--host A` (not yet exposed). |
| Host for Notes replies | All Notes endpoints (including `notes replies` and `notes list --all`) use `substack.com` (host A) automatically — no `--host` flag needed. |
| Profile report live response shape | Unknown; the endpoint exists per bundle but has not been called with real credentials. |

## License

[MIT](LICENSE) © 2026 Griffin Long
