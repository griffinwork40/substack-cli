# substack-cli

A command-line tool for reading and managing [Substack](https://substack.com) publications, wrapping Substack's undocumented private API — the same `/api/v1/` endpoints the Substack web app itself uses.

Read-only endpoints (archive, single post, RSS feed) work anonymously. Everything else — publishing drafts, moderating comments, managing subscribers, pulling analytics — uses your browser session cookie.

> ⚠️ **Unofficial.** Substack publishes no public API for content, publishing, or subscriber automation. This tool reverse-engineers the private endpoints, so it can break without notice if Substack changes them. Use it on publications you own, and review Substack's Terms of Use before automating against your account.

## Features

- **Read (no auth):** list the archive, fetch a single post, pull the RSS feed.
- **Search & discovery:** search a publication's archive, list categories and sections.
- **Publishing:** create / edit / delete drafts, publish or schedule posts, upload images (Markdown → ProseMirror conversion).
- **Moderation:** manage comments and reactions.
- **Audience:** manage subscribers, cross-publication recommendations, and post tags.
- **Analytics:** per-post and publication-level stats.
- **Machine-friendly:** compact JSON to stdout by default (`--pretty` for a Rich table/panel); errors are JSON on stderr with a nonzero exit code.

## Install

Requires Python 3.11+.

```bash
git clone https://github.com/griffinwork40/substack-cli.git
cd substack-cli
pip install -r requirements.txt

# Optional: put `substack` on your PATH
ln -s "$(pwd)/substack" /usr/local/bin/substack
```

## Quick start

Public, no auth required:

```bash
# List the most recent posts in a publication's archive
substack archive --publication-url https://example.substack.com

# Fetch a single post by slug
substack post my-post-slug --publication-url https://example.substack.com

# Pull the RSS feed
substack feed --publication-url https://example.substack.com
```

See all commands with `substack --help`:

```
archive  post  feed  search  whoami  categories  sections  image-upload
config   drafts  comments  subscribers  recommendations  tags  publication  analytics
```

## Authentication

Authenticated operations need your Substack **session cookie** (Substack uses cookies, not API tokens). Full extraction walkthrough: [`references/auth-setup.md`](references/auth-setup.md).

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

## Development

```bash
pip install -r requirements-dev.txt
pytest -q          # 173 tests, all HTTP mocked — no real network calls
```

Architecture, auth model, two-host routing, and a guide to adding new endpoints live in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md). The API surface is mapped in [`references/substack-api.md`](references/substack-api.md).

## Provenance

This CLI began life as an [AFK](https://github.com/griffinwork40/agent-afk) agent skill; [`SKILL.md`](SKILL.md) is the original skill manifest, kept for reference.

## License

[MIT](LICENSE) © 2026 Griffin Long
