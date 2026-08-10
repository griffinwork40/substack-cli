# Substack API Reference (CLI-scoped)

A condensed reference mapping only the endpoints this CLI implements to
their confidence level and operational quirks. For the full upstream
research (all ~55 discovered endpoints), see
`~/.afk/workspace/substack-api-reference.md`.

## Authentication

- **Cookie-based** (`connect.sid`, `substack.sid`, `substack.lli`)
- No API key / OAuth flow for this surface
- See `references/auth-setup.md` for extraction instructions

## Endpoint Table

| Command | Method | Path | Host | Auth | Confidence | Quirk |
|---|---|---|---|---|---|---|
| `archive` | GET | `/api/v1/archive` | P | No (public) | **Live-verified** | Singular `archive`, not `archives` (404s) |
| `post` | GET | `/api/v1/posts/{slug}` | P | No | **Live-verified** | Returns full post content |
| `feed` | GET | `/feed` | P | No (public) | **Live-verified** | RSS 2.0 XML, not JSON |
| `whoami` | GET | `/api/v1/user/profile/self` | A | Yes | Confirmed (2+) | Hits substack.com, not publication subdomain |
| `comments list` | GET | `/api/v1/post/{id}/comments` | P | No | **Live-verified** | Comments have nested `children` field for replies |
| `search` | GET | `/api/v1/archive?search=` | P | No* | **Param confirmed, behavior unconfirmed** | Substack may not actually filter — verify results. *CLI requires auth conservatively (filtering unconfirmed); only `archive`/`post`/`feed` run anonymously. |
| `categories` | GET | `/api/v1/categories` | A | No | **Live-verified** | Global list, not publication-specific |
| `sections` | GET | `/api/v1/publication/sections` | P | Yes (403 anon) | Route confirmed | — |
| `leaderboard` | GET | `/api/v1/category/public/{category_id}/{rank}` | A | No (public) | **Live-verified** | Cross-publication category leaderboard, top 25 pubs. `rank` ∈ `paid`\|`all`\|`rising` — see § Category Leaderboard below for the ranking-variant semantics and field shapes. |
| `subscribers count` / `analytics summary` | GET | `/api/v1/publish-dashboard/summary` | P | Yes (403 anon) | Route confirmed | Subscriber count + open rates |
| `subscribers stats` | POST | `/api/v1/subscriber-stats` | P | Yes | Single source | **Verb may be wrong** — 404 surfaces clear message |
| `analytics post` | GET | `/api/v1/post_management/detail/{id}` | P | Yes | **Live-verified** | Returns a rich nested `stats` block (~30 fields: views, opens, open_rate, clicks, signups_within_1_day, engagement_rate, estimated_value, `firstWeekDailyStats[]`, `comps`) + post metadata. NOT social reactions/restacks. |
| `analytics posts` / `analytics digest` | GET | `/api/v1/archive` + `/api/v1/post_management/detail/{id}` (digest also `/publish-dashboard/summary`) | P | Yes | **Live-verified (N+1)** | Ranks published posts by a `stats` metric (signups\|subscriptions\|open_rate\|opens\|views\|clicks\|engagement_rate\|value). Detail call sends `?offset=0&limit=1`; `_extract_post_stats` tolerates nested/inline/`posts[]` shapes; degrades to `0` (post still listed) when absent. **Ranking note:** near-term signups live in `signups_within_1_day` (top-level `signups` reads 0 on recent posts). Client throttles the loop. Optional future optimization: `post_management/published` may carry per-post stats → single call. See `docs/plans/amplify-content-flywheel.md`. |
| `drafts list` | GET | `/api/v1/drafts` | P | Yes | Confirmed (2+) | **Known breaking change 2026-05**: bare array → `{posts, hasMore, nextCursor}`. CLI tolerates both. |
| `drafts get` | GET | `/api/v1/drafts/{id}` | P | Yes | Confirmed (2+) | — |
| `drafts create` | POST | `/api/v1/drafts` | P | Yes | Confirmed (2+) | **`draft_body` MUST be a JSON string** — nested dict renders literal text. Auto-derives `byline_ids` from self profile if not supplied. |
| `drafts update` | PUT | `/api/v1/drafts/{id}` | P | Yes | Confirmed (2+) | — |
| `drafts delete` | DELETE | `/api/v1/drafts/{id}` | P | Yes | Single source | — |
| `drafts prepublish` | GET | `/api/v1/drafts/{id}/prepublish` | P | Yes | Confirmed (2+) | Returns `{errors, suggestions}` — run before publish |
| `drafts publish` | PUT→POST | `/api/v1/drafts/{id}/publish` | P | Yes | **Verb conflict** | Tries PUT first; falls back to POST on 404 only (not on 401/403) |
| `drafts schedule` | POST | `/api/v1/drafts/{id}/scheduled_release` | P | Yes | Single source | Body key MUST be `trigger_at` — not `post_date`/`scheduled_at` |
| `drafts unschedule` | DELETE | `/api/v1/drafts/{id}/scheduled_release` | P | Yes | Single source | — |
| `drafts scheduled` | GET | `/api/v1/drafts/{id}/scheduled_release` | P | Yes | Single source | — |
| `image-upload` | POST | `/api/v1/image` | P | Yes | Confirmed (2+) | **JSON + base64**, not multipart. Fields: `bytes`, `imageWidth`, `imageHeight`, `url` |
| `comments create` | POST | `/api/v1/post/{id}/comment` | P | Yes | Single source | Body: `{"body": "text"}` |
| `comments delete` | DELETE | `/api/v1/comment/{id}` | A or P | Yes | Confirmed (2+) | Accepts host override |
| `comments react` | POST | `/api/v1/post/{id}/reaction` | P | Yes | Single source | Body: `{"reaction": "❤", "surface": "reader"}` — **literal emoji**, not "like" |
| `comments unreact` | DELETE | `/api/v1/post/{id}/reaction` | P | Yes | Single source | — |
| `subscribers add` | POST | `/api/v1/subscriber/add` | P | Yes | Single source | Body: `{"email": "...", "name": "..."}` |
| `subscribers remove` | DELETE | `/api/v1/subscriber/{id}` | P | Yes | Single source | — |
| `recommendations add` | PUT | `/api/v1/recommendations` | P | Yes | Single source | **Browser-vs-curl gap**: may 403 even with valid cookies |
| `recommendations remove` | DELETE | `/api/v1/recommendations/` | P | Yes | Single source | **Trailing slash REQUIRED** — bare path 404s |
| `tags list` | GET | `/api/v1/publication/post-tag` | P | Yes | Single source | — |
| `tags create` | POST | `/api/v1/publication/post-tag` | P | Yes | Single source | Body: `{"name": "..."}` |
| `tags delete` | DELETE | `/api/v1/publication/post-tag/{id}` | P | Yes | Single source | — |
| `tags attach` | POST | `/api/v1/post/{id}/tag/{tag_id}` | P | Yes | Single source | — |
| `tags detach` | DELETE | `/api/v1/post/{id}/tag/{tag_id}` | P | Yes | Single source | — |
| `publication update` | PUT | `/api/v1/publication` | P | Yes | Single source | One field per call: `name`, `hero_text`, `language`, `welcome_email_content` |
| `notes create` | POST | `/api/v1/comment/feed` | A | Yes | Confirmed (2+) | Body `{bodyJson, replyMinimumRole}`. **`bodyJson` is a nested OBJECT** (NOT stringified like `draft_body`). Publishes immediately — **no edit/undo**. Requires `--yes`. |
| `notes reply` | POST | `/api/v1/comment/feed` | A | Yes | **Confirmed (live)** | Body `{bodyJson, parent_id, tabId:"for-you", surface:"feed", replyMinimumRole}` — **SAME endpoint as `notes create`**; `parent_id` threads the reply under the parent note (visible in the created reply's `ancestor_path`). Publishes immediately — **no edit/undo** (delete only). Requires `--yes`. `POST /api/v1/comment/{id}` 404s — the reply path is `comment/feed`. |
| `notes list` | GET | `/api/v1/reader/feed` (home) · `/api/v1/reader/feed/profile/{user_id}` (`--mine`/`--user-id`) | A | Yes | Confirmed (2+) | Returns `{items:[...]}`; note entity_keys are `c-`-prefixed (posts are `p-`). `GET /comment/feed` 403s — the read path is `reader/feed`. |
| `notes get` | GET | `/api/v1/reader/feed/c-{id}` | A | Yes | Reported | Single-note detail (SPA feed-item expand path) |
| `notes delete` | DELETE | `/api/v1/comment/{id}` | A | Yes | Confirmed (2+) | Same endpoint as `comments delete` (notes are comment-backed). Requires `--yes`. |
| `chat list` | GET | `/api/v1/community/publications/{publication_id}/posts` | P | Yes | **Community-reported, never live-verified** | `--publication-id` is REQUIRED — no resolver exists anywhere in this CLI (`/user/profile/self` returns a user id, not a publication id). |
| `chat replies` | GET | `/api/v1/community/posts/{thread_id}/comments` | A | Yes | **Community-reported, never live-verified** | Always sends `order=asc&initial=true`. Optional `--before-id`/`--after-id`/`--limit` are None-filtered locally before the request (client.py's own None-stripping only catches a wholly-empty params dict). Response cursor fields are reported as `moreBefore`/`moreAfter` — NOT `hasMore`/`nextCursor`, so `extract_pagination_meta` is intentionally not used; cursors pass through untouched. |
| `chat sub-replies` | GET | `/api/v1/community/comments/{comment_id}/comments` | A | Yes | **Community-reported, never live-verified** | Sibling of `chat replies` one level deeper (replies to a reply). Same params/pagination behavior. |
| `chat unread` | GET | `/api/v1/messages/unread-count` | A | Yes | **Community-reported, never live-verified** | Known field: `pubChatUnreadCount`. `--pretty` surfaces it as a bespoke highlighted line (not via the shared `output()` helper, to avoid widening that helper's blast radius across all other commands) before the full render. |

## Rate Limits

No official numeric limit published. Community guidance: <1 req/s sustained
is safe. The CLI throttles to 1.0s between requests by default and retries
429s with exponential backoff (1s/2s/4s, respecting `Retry-After` header).

## Two-Host Routing

- **Host "P"** (publication subdomain): default for most endpoints — `https://{subdomain}.substack.com`
- **Host "A"** (substack.com bare): used for `whoami`, `categories`, `leaderboard`, `chat replies`/`chat sub-replies`/`chat unread`, and optionally `comments delete`. `chat list` is host "P" (the publication subdomain).

The CLI handles this automatically per command — you don't need to specify
the host manually unless using `comments delete --host A`.

## Category Leaderboard

`GET https://substack.com/api/v1/category/public/{category_id}/{rank}` — a
**public, unauthenticated** endpoint returning the top 25 publications in a
category. This is Substack's own leaderboard data source (same one the
substack.com category pages render), reverse-engineered here for CLI/agent
consumption.

**Category id**: numeric, discovered via `substack categories`. The CLI
additionally accepts two live-verified slug aliases as shorthand:
`finance` → `153`, `us-politics` → `76739`. No other slugs are hardcoded —
an unrecognized non-numeric string errors instead of guessing.

**`{rank}` path segment** — three variants, each a genuinely different
ordering (not just a display sort of the same set):

| Rank | Ordered by | Notes |
|---|---|---|
| `paid` (CLI default) | Paid-subscriber count | "Bestsellers." What "top N in `<category>`" almost always means. |
| `all` | Total reach (free + paid) | **Includes publications with payments disabled/paused** (no `plans` array at all) — this is why `all` and `paid` return different publications in different orders, not just a resort. |
| `rising` | Growth velocity | Newer / fast-growing publications, not necessarily the largest. |

**Why the CLI defaults to `paid`, not `all`**: the two rank variants
returned genuinely different orderings during endpoint verification —
`all` surfaced payments-disabled publications ranked by reach alone,
which produced a wrong "top bestsellers" answer if you assumed ordering
was uniform. `--rank all`/`--rank rising` remain available for the total-
reach and growth-rate questions, but the CLI does not assume you meant
those when you asked for a leaderboard.

**Response shape**: `{"publications": [...], "more": bool, "title": str}`
(tolerated via the same envelope/bare-list handling as other list
endpoints). Each publication object carries 100+ fields; the CLI projects
down to: `rank` (1-indexed position in the response — the API itself
returns no explicit rank field), `name`, `author`, `url` (from `base_url`,
falling back to `custom_domain`), `monthly_usd`/`yearly_usd` (parsed from
`plans[].amount` — **integer cents**, `interval` is `"month"`/`"year"`;
when a founding-member tier adds a second, pricier plan on the same
interval, the CLI takes the **minimum** amount per interval as the
standard price), `paid_subscriber_band` (`rankingDetail`, a banded string
like `"Hundreds of paid subscribers"`), `total_subscribers` (parsed from
the confusingly-named `freeSubscriberCount` field, which at this endpoint
actually holds the **total** free+paid count, not free-only — parsed from
its comma-formatted string form, e.g. `"774,000"` → `774000`),
`payments_state`, and `bestseller_tier` (`author_bestseller_tier`). Raw
100+-field objects are never emitted — a full category response is ~4MB,
so tests and any manual inspection must use a small hand-built fixture,
never the live payload.

## Chat — confidence caveat

Unlike every other surface in this table, the four `chat` endpoints were
never independently curl/browser-verified by this project — they are
carried over from community reverse-engineering of the Substack web app's
JS bundle with no confirmed live response captured. Treat every field name
in a `chat` response as provisional. A 404 from any `chat` command most
likely means the endpoint path or shape has drifted, or the id/
`--publication-id` you passed is wrong — not necessarily user error. `chat`
is deliberately **read-only**: Chat has a real write surface (send a
thread, reply, delete, react, lock, ban, etc.) but every one of those
endpoints is unverified, and a wrong guess would mutate state visible to
real, paying subscribers with no undo. None of that is implemented.

## Deferred / Out of Scope for v1

- Substack Notes **editing** — Notes CRUD + replies are implemented (`notes create`/`reply`/`list`/`get`/`delete`), but there is **no edit/update endpoint**: notes publish immediately with no draft state and no undo. The only "edit" is delete-then-recreate (which yields a new id).
- Full Markdown→ProseMirror (lists, images-in-body, footnotes, paywall markers, embeds)
- **Chat writes** (send/reply/delete/react/lock/ban/settings) — reads only (`chat list`/`replies`/`sub-replies`/`unread`) are implemented; see the confidence caveat above.
- **DMs** — no endpoints implemented at all (read or write).
- Stripe/pledges
- Cross-publication discovery of **category-level rankings** is now supported via `leaderboard` (see § Category Leaderboard). Still out of scope: arbitrary per-subscriber / per-post data for publications you don't own — the leaderboard endpoint only exposes the same aggregate, banded fields Substack shows publicly on its own category pages.
- OAuth / programmatic login flows
- Streaming/WebSocket or podcast-distribution management