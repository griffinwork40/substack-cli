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
- **Search & discovery:** search a publication's archive, list categories and sections.
- **Publishing:** create / edit / delete drafts, publish or schedule posts, upload images (Markdown → ProseMirror conversion).
- **Moderation:** manage comments and reactions.
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
pytest -q          # 173 tests, all HTTP mocked — no real network calls
```

Architecture, auth model, two-host routing, and a guide to adding new endpoints live in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md). The API surface is mapped in [`skills/substack/references/substack-api.md`](skills/substack/references/substack-api.md).

## License

[MIT](LICENSE) © 2026 Griffin Long
