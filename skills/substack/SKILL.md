---
name: substack
allowed-tools: Bash, Read, Write, Edit
description: >
  CLI wrapper for Substack's unofficial private API — read publication
  content (archive, posts, RSS feed, comments, search, analytics) and
  manage a publication you own (create/edit/publish/schedule drafts,
  moderate comments, manage subscribers, recommendations, and tags), and
  post/list/delete Substack Notes (Substack's micro-blog surface; note that
  the API has no note-editing endpoint — notes publish immediately).
  Triggers on requests to check a Substack newsletter's archive or posts,
  pull post analytics or subscriber counts, draft or publish a Substack
  post, schedule a newsletter, reply to or moderate Substack comments, post
  or delete a Substack Note, or manage a Substack publication
  programmatically. Examples: "what did I publish on Substack this month",
  "draft a new Substack post", "how many subscribers does my newsletter
  have", "schedule my Substack post for Friday", "reply to comments on my
  latest post", "post a Substack Note", "delete my last note". Requires
  SUBSTACK_COOKIES_STRING (session cookie) for any authenticated
  operation; read-only public endpoints (archive, post, feed) work without
  auth. Write operations additionally require SUBSTACK_ENABLE_WRITE=true.
---

# substack — Substack CLI (unofficial API)

## Overview

`substack` is a Python CLI wrapping Substack's undocumented `/api/v1/`
surface — there is no official public API for content/publishing/
subscriber automation, so this wraps the same private endpoints the
Substack web app itself uses. Read-only endpoints (archive, single post,
RSS feed) work anonymously; everything else needs a session cookie.

**Binary location:** `substack` at
`${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/skills/substack/scripts/substack`
(optional: `ln -s "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/skills/substack/scripts/substack" /usr/local/bin/substack`)
**API base:** `https://{your-subdomain}.substack.com/api/v1/` (some routes
require the bare `https://substack.com` host instead — the CLI handles
this automatically per command).
**Python deps:** `pip install -r
"${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/skills/substack/scripts/requirements.txt"` (typer, httpx, rich)

## Setup & Authentication

Substack has no API keys — auth is a browser session cookie. See
`references/auth-setup.md` for the full DevTools walkthrough. Short
version:

```bash
substack config set-cookies "connect.sid=...; substack.sid=...; substack.lli=..."
substack config set-publication charliepgarcia
```

### Environment variables (take priority over the config file)

```bash
export SUBSTACK_COOKIES_STRING="connect.sid=...; substack.sid=..."
export SUBSTACK_PUBLICATION_URL="charliepgarcia"
```

### Enabling write operations

All mutating commands (create/update/delete/publish/schedule/add/remove)
are disabled by default. Enable explicitly:

```bash
export SUBSTACK_ENABLE_WRITE=true
# or: substack config set-value enable_write true
```

Eight specific commands additionally require `--yes` even with the gate
enabled, because they are hard to reverse or immediately externally
visible: `drafts delete`, `drafts publish`, `comments delete`,
`subscribers remove`, `recommendations remove`, `tags delete`,
`notes create` (notes publish immediately and cannot be edited), and
`notes delete`.

### Verify your setup

```bash
substack config test --pretty
```

## Commands

### Reading

```bash
substack archive --sort new --limit 20            # recent posts
substack archive --offset 20 --limit 20            # page 2
substack post brian-sidman-entrepreneur-lessons    # single post by slug
substack feed                                      # RSS as parsed JSON
substack feed --raw                                # raw RSS XML
substack search "AI infrastructure"                # full-text search (filtering behavior unconfirmed by Substack)
substack subscribers count                         # subscriber count / growth summary
substack subscribers stats                          # detailed subscriber stats (fixed 25-row sample, not a full enumeration — see caveat below)
substack analytics post 204779662                  # rich per-post stats: views/opens/open_rate/clicks/signups + firstWeekDailyStats + comps
substack analytics summary                          # publish dashboard summary
substack analytics posts --sort signups --limit 25  # rank posts by a metric (signups|subscriptions|open_rate|opens|views|clicks|engagement_rate|value)
substack analytics digest --top 5                   # "what's working": top posts by signups + publication summary (ranked JSON)
substack comments list 204779662                   # reader comments on a post
substack whoami                                    # confirm which account the cookie belongs to
substack categories                                 # list all Substack categories
substack sections                                   # list publication sections
substack leaderboard finance --top 10              # top 10 finance newsletters by paid subs (cross-publication, no auth needed)
substack leaderboard us-politics --rank all         # same category, ranked by total reach instead
substack leaderboard 76739 --pretty                # numeric category id also accepted (see `substack categories`)
```

**Caveats worth knowing before you rely on these:**
- `analytics post` returns a rich `stats` block (**verified live 2026-07-21**):
  `views`, `opens`, `open_rate`, `clicks`, `signups_within_1_day`,
  `engagement_rate`, `estimated_value`, a `firstWeekDailyStats` timeseries, and
  `comps` benchmarks — plus post metadata. It does **not** include social
  reactions/restacks (those are a separate surface).
- `subscribers stats` always requests `limit:25/offset:0` and returns
  that **fixed 25-row sample** — it has no pagination and is not a full
  subscriber enumeration.
- `analytics posts` / `analytics digest` list posts from `archive` and pull
  per-post stats from `post_management/detail` (N+1, throttled). **Verified
  working 2026-07-21.** Ranking note: the meaningful signup signal is
  `signups_within_1_day` — the top-level `signups` reads 0 on recent posts, so
  `--sort signups` ranks by the within-1-day field. A post whose detail call
  fails is still listed (stats `{}`, ranks as 0). See
  `docs/plans/amplify-content-flywheel.md`.

### Drafting & publishing

```bash
substack drafts create --title "Dear Charlie #37" --body-markdown "$(cat draft.md)"
substack drafts list --limit 10
substack drafts get 123456
substack drafts prepublish 123456                  # validation pass — run before publish
SUBSTACK_ENABLE_WRITE=true substack drafts publish 123456 --yes
SUBSTACK_ENABLE_WRITE=true substack drafts schedule 123456 --at 2026-07-11T09:00:00Z
substack drafts scheduled 123456                    # check if a draft is scheduled
SUBSTACK_ENABLE_WRITE=true substack drafts unschedule 123456
substack image-upload ./cover.png
```

### Comments & community

```bash
substack comments list 204779662
SUBSTACK_ENABLE_WRITE=true substack comments create 204779662 "Thanks for reading!"
SUBSTACK_ENABLE_WRITE=true substack comments react 204779662 --emoji "🔄"
SUBSTACK_ENABLE_WRITE=true substack comments delete 456 --yes --host A
```

### Notes

Substack Notes are the micro-blog surface (comment-backed internally).
Full CRUD **except update** — the API has no note-edit endpoint, so notes
publish immediately with no draft/undo; the only "edit" is delete + recreate.

```bash
substack notes list                                 # your personalized Notes home feed
substack notes list --mine                          # only YOUR posted notes (get ids to delete)
substack notes list --user-id 12345                 # a specific user's notes
substack notes get c-98765                           # single note (accepts 98765 or c-98765)
# create: publishes IMMEDIATELY + cannot be edited -> requires --yes
SUBSTACK_ENABLE_WRITE=true substack notes create "Shipping Notes support today. **Big.**" --yes
SUBSTACK_ENABLE_WRITE=true substack notes create --body-json ./note.json --yes    # rich ProseMirror body
SUBSTACK_ENABLE_WRITE=true substack notes create "Subscribers only" --reply-min-role paid_subscriber --yes
SUBSTACK_ENABLE_WRITE=true substack notes delete 98765 --yes
```

### Subscribers

```bash
SUBSTACK_ENABLE_WRITE=true substack subscribers add user@example.com --name "John"
SUBSTACK_ENABLE_WRITE=true substack subscribers remove 123 --yes
```

### Recommendations

```bash
SUBSTACK_ENABLE_WRITE=true substack recommendations add 999
SUBSTACK_ENABLE_WRITE=true substack recommendations remove 123 --yes
```

### Tags

```bash
substack tags list
SUBSTACK_ENABLE_WRITE=true substack tags create "AI"
SUBSTACK_ENABLE_WRITE=true substack tags delete 1 --yes
SUBSTACK_ENABLE_WRITE=true substack tags attach 123 1
SUBSTACK_ENABLE_WRITE=true substack tags detach 123 1
```

### Publication settings

```bash
SUBSTACK_ENABLE_WRITE=true substack publication update --name "New Name"
SUBSTACK_ENABLE_WRITE=true substack publication update --hero_text "New tagline"
```

### Config

```bash
substack config set-cookies "connect.sid=..."
substack config set-cookies-file ./cookies.txt
substack config set-publication charliepgarcia
substack config show [--pretty]
substack config test [--pretty]
```

## Output & Exit Codes

- Default: compact JSON on stdout. `--pretty` renders Rich tables/panels for humans.
- Errors: `{"error": true, "message": "...", "status_code": ...}` JSON on
  **stderr**, exit code 1 — even under `--pretty` (a colored panel is
  added, the JSON is never dropped).
- Exit 0: success. Exit 1: usage error, auth error, API error, write-gate refusal.

## Common Workflows

```bash
# Pull this week's post titles + URLs
substack archive --limit 10 | jq '.[] | {title, canonical_url}'

# Check subscriber growth before drafting the next "Dear Charlie"
substack subscribers count --pretty

# Full draft -> review -> publish loop
substack drafts create --title "New Post" --body-markdown "$(cat post.md)" | jq -r '.id' > /tmp/draft_id
SUBSTACK_ENABLE_WRITE=true substack drafts prepublish "$(cat /tmp/draft_id)"
SUBSTACK_ENABLE_WRITE=true substack drafts publish "$(cat /tmp/draft_id)" --yes
```

## Known Limitations

See `references/substack-api.md` for the full endpoint-confidence table.
Highlights: search filtering behavior is unconfirmed by Substack itself;
`drafts publish` verb (PUT vs POST) has a documented source conflict
(this CLI tries PUT then falls back to POST on 404); `recommendations add`
can 403 a plain HTTP client even with valid cookies (a documented
browser-vs-curl gap, not necessarily an expired cookie). **Notes cannot be
edited** — Substack exposes no note-update endpoint, so `notes` supports
create/list/get/delete only; to change a note, delete it and create a new
one (a new id is issued). **`leaderboard` covers category-level
cross-publication discovery only** (top 25 per category/rank) — it cannot
answer per-subscriber questions about a publication you don't own; only
`finance` and `us-politics` have live-verified slug aliases, everything
else needs a numeric id from `substack categories`.