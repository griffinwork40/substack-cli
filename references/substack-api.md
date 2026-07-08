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
| `subscribers count` / `analytics summary` | GET | `/api/v1/publish-dashboard/summary` | P | Yes (403 anon) | Route confirmed | Subscriber count + open rates |
| `subscribers stats` | POST | `/api/v1/subscriber-stats` | P | Yes | Single source | **Verb may be wrong** — 404 surfaces clear message |
| `analytics post` | GET | `/api/v1/post_management/detail/{id}` | P | Yes | Single source | Views/opens/CTR per post |
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

## Rate Limits

No official numeric limit published. Community guidance: <1 req/s sustained
is safe. The CLI throttles to 1.0s between requests by default and retries
429s with exponential backoff (1s/2s/4s, respecting `Retry-After` header).

## Two-Host Routing

- **Host "P"** (publication subdomain): default for most endpoints — `https://{subdomain}.substack.com`
- **Host "A"** (substack.com bare): used for `whoami`, `categories`, and optionally `comments delete`

The CLI handles this automatically per command — you don't need to specify
the host manually unless using `comments delete --host A`.

## Deferred / Out of Scope for v1

- Substack Notes (`/api/v1/comment/feed`, `/api/v1/reader/feed`)
- Full Markdown→ProseMirror (lists, images-in-body, footnotes, paywall markers, embeds)
- Messaging/Chat/DMs
- Stripe/pledges
- Cross-publication discovery
- OAuth / programmatic login flows
- Streaming/WebSocket or podcast-distribution management