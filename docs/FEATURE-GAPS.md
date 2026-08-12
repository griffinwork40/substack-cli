# substack-cli — Feature Gap Audit

_Generated 2026-07-21 from a two-front audit: (1) a codebase cross-reference of the
registered command surface vs. `references/substack-api.md`, and (2) an external survey of
the community-reverse-engineered Substack private `/api/v1/` surface._

> **Caveat.** Substack has **no official API**. Every "missing" item below means *missing from
> this CLI* — not *proven absent from the upstream API*. The external capability map was
> verified against community sources ~June 2026; an undocumented API drifts silently, so
> **re-probe any endpoint with DevTools capture before building it.**
>
> Primary external source: `AnthonyDavidAdams/substack-api-reference` (125 operations, OpenAPI
> 3.1, per-endpoint verification tags). Realistic prior-art baseline: `ma2za/python-substack`.

## What already exists (baseline — not gaps)

Full draft lifecycle (`drafts create/list/get/update/delete/prepublish/publish/schedule/unschedule/scheduled`),
`drafts image-upload`, `notes create/list/get/delete`, `comments create/list/delete/react/unreact`,
`subscribers count/stats/add/remove`, `recommendations add/remove`, `tags list/create/delete/attach/detach`,
`publication update` (partial), `analytics summary/post`, and reads
(`archive/post/feed/search/whoami/categories/sections/leaderboard`). 47 registered commands across
9 subapps.

---

## Tier 1 — Confirmed defects / near-trivial completions (high confidence, small diff)

### 1. `publication update --welcome_email_content` is unreachable ✅ *(FIXED 2026-07-21)*
~~The command signature exposes only `name`, `hero_text`, `language`
(`manage.py:405-410`), yet `_RECOGNIZED_PUB_FIELDS` (`manage.py:167`), the `update_publication`
docstring (`manage.py:172`), **and** the reference table (`references/substack-api.md:54`) all
list `welcome_email_content`. The field can never be sent from the CLI.~~
**Resolved:** added the `--welcome-email-content` param + passthrough to `publication_update_cmd`
(`manage.py`); CLI-level regression tests added (`tests/test_manage_publication.py`). Also corrected
a stale `--hero_text` → `--hero-text` example in `SKILL.md` (Typer emits hyphenated options).

### 2. `publication_settings` boolean toggles are not wrapped at all
Only `PUT /publication` (name/hero/language/welcome) is implemented. The community-confirmed
`PUT /publication_settings` (boolean feature toggles) has no command.
**Module:** `manage.py` / `publication` subapp. **Confidence: med** (endpoint community-confirmed; field set unverified).

### 3. `subscribers count` and `analytics summary` are duplicates
Both call `get_publish_dashboard_summary` (`read.py:475` and `read.py:501`). Harmless, but there is
no *distinct* growth/ARR command behind them (see Tier 2 #6).
**Confidence: high** (same function, verified).

---

## Tier 0 — Present defects in shipped code

_Added 2026-08-10. Found incidentally during Chat/DM scoping (`docs/CHAT-DM-CAPABILITY-MAP.md`) and
independently re-derived by an adversarial verification wave. Lettered, not numbered, to avoid
colliding with the Tier 2+ sequence. These affect **already-shipped commands** and are unrelated to
any new feature — they rank above Tier 1._

### A. Blind retry re-sends POST/PUT bodies with no idempotency key — duplicate-execution risk ✅ *(FIXED 2026-08-10)*
~~`_request` wraps every verb in one retry loop shared by `get/post/put/delete` (`client.py:214-242`),
with **no method-specific guard skipping retry for non-idempotent verbs**. Two branches re-send the
identical `json_body`:~~
- ~~`except httpx.HTTPError` → `continue` (`client.py:178-183`) — catches timeouts and connection
  resets, i.e. **exactly the "did my write land?" case** where the server may have already committed.~~
- ~~HTTP 429 → `continue` (`client.py:188-198`).~~

~~`DEFAULT_MAX_RETRIES = 3` (`client.py:22`) and no call site overrides it, so 3 extra attempts always
ship. Every write command inherits this: `notes.create_note`/`reply_to_note` (`notes.py:152,189`),
`publish.create_draft`/`publish_draft`/`schedule_draft` (`publish.py:204,244-248` — which *also*
layers a POST-fallback-on-404 atop the retry), `manage.create_comment`/`add_subscriber`/`create_tag`/
`attach_tag`. Grep for `idempot|uuid|client_id` across the package returns **zero hits**, and
`test_client.py` has **no case** exercising a mid-POST `httpx.HTTPError` retry.~~

~~Practical worst case today: a transport blip during `drafts publish` or `notes create` silently
publishes twice.~~
**Resolved:** added a module-level `_IDEMPOTENT_METHODS` frozenset (`client.py`) and guarded the
`except httpx.HTTPError` retry branch so a transport error is retried only for GET/HEAD/OPTIONS/
PUT/DELETE — POST now re-raises immediately on the first transport error instead of re-sending the
body. The HTTP-429 branch is untouched and still retries for every method (a 429 means the server
never processed the request, so re-sending is safe). Regression tests added (`tests/test_client.py`):
POST + transport error asserts exactly one HTTP attempt (`route.call_count == 1`); GET and PUT +
transport error still retry and succeed; POST + 429-then-200 still retries and succeeds.

### B. Session cookie persisted at default umask, no permission hardening ✅ *(FIXED 2026-08-10)*
~~`config.py:22-29` writes `~/.config/substack-cli/config.json` with a plain `open(..., "w")` +
`json.dump`. Grep for `chmod|0o600|0o644|permission` across `substack_cli/` returns **zero hits**, so
the full account-control credential — the cookie that authorizes all 47 commands, including
`subscribers remove` and `drafts publish` — sits at whatever the ambient umask grants.
`auth-setup.md:65` already tells users to treat the string like a password; the code does not.~~
**Resolved:** `save_config` (`config.py`) now `os.chmod`s the config file to `0o600` and the parent
directory to `0o700` immediately after writing. The chmod calls are wrapped in `try/except OSError`
so a platform or filesystem that can't honor them (Windows, some exotic mounts) doesn't break config
saving — it just silently skips hardening. File format, function signature, and return contract are
unchanged. Regression test added (`tests/test_config.py`): asserts
`stat.S_IMODE(os.stat(path).st_mode) == 0o600` after `save_config`.

---

## Tier 2 — Community-confirmed endpoints, real value, buildable

### 4. Subscriber CSV export + full-list enumeration
`get_subscriber_stats` is hard-capped at `{"limit": 25, "offset": 0}` (`read.py:281`) — no
pagination, no way to enumerate all subscribers. No export command exists. Upstream offers
CSV export jobs (`GET /publication_export`, job list + download URL) and paginated/filtered
`POST /subscriber-stats`.
**Module:** `read.py` (subscribers subapp). **Confidence: high** (cap is in source); **med** on export endpoint shape.

### 5. Rich body authoring (lists, images-in-body, footnotes, paywall, embeds)
The Markdown→ProseMirror converter raises `NotImplementedError` on lists (`publish.py:116-121`)
and inline images (`publish.py:124-129`); it supports only paragraphs/headings/bold/italic/links.
Rich posts require a hand-authored `--body-json`. Highest real-world impact for actual publishing.
**Module:** `publish.py`. **Confidence: high** (explicit in source).

### 6. Analytics depth: post-stats extraction + publication growth timeseries
`analytics post` correctly hits the rich `GET /api/v1/post_management/detail/{id}` (`read.py:302`)
— but **without** the documented `?offset=0&limit=1` params, and it dumps the raw object rather
than surfacing the ~31-field `stats` dict (opens, open_rate, clicks, CTR, views, signups,
referrers, per-link, 7-day daily). No publication growth/ARR command exists
(`GET /publish-dashboard/summary-v2?range=N`, growth sources/events, revenue/MRR).
**Module:** `read.py` (analytics subapp). **Confidence: high** for the missing growth command; **med** on `stats` shape.

### 7. Substack Chat / threads
Enable/disable (`POST /publication/{id}/publication_threads_settings`), send
(`POST /community/publications/{id}/posts`, client-generated UUID), list, delete
(`DELETE /community/posts/{id}`). Entirely absent from the CLI.
**Module:** new `chat.py` subapp. **Confidence: med** (community-confirmed endpoints, not exercised here).

### 8. Audio / podcast upload
4-step S3 presigned flow (`POST /audio/upload` → S3 PUT → `.../transcode` → poll). Declared
out-of-scope (`references/substack-api.md:127`) but community-confirmed upstream. Enables podcast
publishing via `draft_podcast_*` fields.
**Module:** new `media.py` or extend `publish.py`. **Confidence: med.**

### 9. Recommendations discovery (`search` / `suggested` / incoming-stats)
Only `add`/`remove` exist. Upstream has `GET /publication/search`, suggested, and incoming-recommendation stats.
**Module:** `manage.py` (recommendations subapp). **Confidence: med.** ⚠️ recommendations endpoints are referer/Sec-Fetch gated (403 from plain curl).

### 10. Direct-message read
`GET /messages/inbox` + `GET /messages/unread-count` (read-only) are community-confirmed. Absent.
Send-DM is upstream-unverified (Tier 3).
**Module:** new `messages.py`. **Confidence: med** (read); **low** (write).

### 11. Post-management admin lists (`counts`, published)
`GET /post_management/{counts,published}` power dashboard tallies. `drafts list` + `drafts scheduled`
exist; published posts are only reachable via the public `read archive`. A `counts`/published-admin
view is a small addition.
**Module:** `read.py` / `publish.py`. **Confidence: med.**

---

## Tier 3 — Lower confidence / declared out-of-scope / upstream-unverified

State clearly to the user before building; each needs empirical re-verification.

- **Video upload** — bucket/field names known, full flow only *inferred* (`R` upstream). New module.
- **Section create/edit** — read exists (`read sections`); write is unmapped upstream (`U`).
- **Notes like / restack** — apply-POST path unverified/likely-wrong in canonical ref (`U`).
- **Comp / gift / founding-member creation, paid-tier config** — read surfaces these; create/config unmapped (`U`).
- **Custom-domain setup** — `U`, and $50 + captcha-gated; not headlessly automatable.
- **Cross-posting state (YouTube/LinkedIn)** — per-post auth/upload state endpoints exist (`R`).
- **Referral code** — `PUT /user/writer_referrals/code` (idempotent); stats not mapped.
- **Comment moderation beyond delete (ban/restrict)** — SKILL.md's "moderate comments" framing is
  aspirational; only `comments delete` exists (`manage.py:218`). The moderator-delete/ban acting
  endpoints are unmapped upstream (only the delete-reason enum is confirmed).

---

## Recommended sequencing

1. **Ship Tier 1** now — #1 is a real defect with a 5-line fix; #2/#3 are cheap.
2. **Then Tier 2 #4, #5, #6** — these are the highest-value gaps for a "full-featured" CLI
   (export, rich bodies, real analytics) and all rest on community-confirmed endpoints.
3. **Tier 2 #7–#11 and Tier 3** — scope individually; re-probe the endpoint with DevTools first
   (undocumented-API drift risk is high).

Follow the repo's endpoint-adding workflow (`docs/DEVELOPMENT.md`): write the `respx`-mocked test
first → implement in the right module → register `@app.command` → `pytest -q` → update `SKILL.md`
and `references/substack-api.md`.

---

## Killer-feature synthesis

The CLI's differentiator over the Substack web UI is **automation + JSON composability +
agent-drivability** (it ships as an AFK skill). The killer features are *workflows* that chain
primitives, not new endpoints. Ranked:

### ⭐ #1 — The Content Flywheel (`substack amplify`)
**Analyze → atomize → distribute → measure**, as a closed loop:
1. **Analyze** — rank the archive by the rich per-post `stats` (gap #6) to find the top performer.
2. **Atomize** — pull its body (`read post`, confirmed `/api/v1/posts/{slug}` at `read.py:221`) and
   have the agent break it into a sequence of Substack Notes (`notes create`, confirmed) drafted in
   the writer's voice (`voice-match` skill).
3. **Distribute** — drip the Notes over days via the **AFK cron scheduler**, and cross-post to
   X/Threads via the sibling `x-api` / `threads-api` skills with back-links to the post.
4. **Measure** — re-pull analytics to attribute which Notes/channels drove signups; feed back into step 1.

Why it's the killer: it solves the #1 creator pain (turning one long post into ongoing
subscriber-driving promotion) and is **uniquely feasible in this environment** because the
distribution + voice + scheduling assets already exist as sibling skills. Shippable core (Phase 1)
uses only confirmed Substack endpoints — `read post` + `notes create` — with zero external
dependencies. Phases 2–3 add cross-posting and attribution.
**Risks:** cross-posting is public/irreversible → must ride the `SUBSTACK_ENABLE_WRITE` + `--yes`
gate and human approval; analytics-`stats` shape is `med` confidence (re-verify); note *scheduling*
lives in the AFK scheduler, not Substack (Notes have no native schedule endpoint).

### #2 — Own-Your-Data Backup (`substack backup`)
Full local, git-friendly export of a publication: every post as Markdown (`archive` + `read post`),
subscriber CSV (`GET /publication_export`, gap #4), comments (`comments list`), and notes. Pure
read/export → no write-gate, lowest risk, ships in days. Kills platform-lock-in anxiety. Second only
because it's *useful* more than *differentiated*.

### #3 — Growth Insights Digest (`substack digest`)
Read-only report combining rich post `stats` (gap #6) + subscriber growth timeseries →
"what's working": best publish time, subject-line patterns, top posts, signup drivers. The web UI
shows dashboards; this tells you *what to do*. Natural precursor to #1's Analyze step.

**Build order:** #2 (fast, safe beachhead) or #3 (feeds #1) first, then #1 Phase 1 (Substack-only
flywheel core), then #1 Phases 2–3 (cross-platform + attribution).
