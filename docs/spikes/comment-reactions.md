# Spike: comment reactions (who liked a comment or note reply)

**Issue:** #30
**Date:** 2026-11-11
**Outcome:** A — working read command shipped (`substack comments reactions`)
**Live requests made:** 28 GET requests total (all GET, no mutations)

---

## Why

Fake accounts like readers' comments so the reader receives a notification
showing the fake profile (Charlie's blurred face plus a WhatsApp name).
Publishers get no alert for likes. The spike investigates whether the API
exposes the identities of users who reacted to a comment or note reply,
enabling a sweep to scan likers on recent CM comments and Notes.

---

## What was tried

### Data already embedded in existing responses

`GET /api/v1/post/{post_id}/comments` (live-verified, existing command) returns
comment objects that include:

- `reaction_count`: integer total
- `reactions`: `{"❤": N}` count per emoji
- `reactor_names`: array of display name strings (e.g. `["Charlie Garcia"]`)

This field gives names only. No user ids, handles, or photo URLs are embedded
in the comment list response. It is useful as a quick count but cannot be used
for identity checking against known fake profiles.

`GET /api/v1/reader/comment/{id}` (Notes get) and
`GET /api/v1/reader/comment/{id}/replies` (Notes replies) return only
`reaction_count` and `reactions: {"❤": N}` on note and reply objects.
No `reactor_names` field appears on notes (only on post comments).

### Dedicated endpoints tried (all 404)

| Endpoint | Host | Status |
|---|---|---|
| `GET /api/v1/post/{id}/reaction` | P | 404 |
| `GET /api/v1/post/{id}/reactions` | P | 404 |
| `GET /api/v1/comment/{id}/reactions` | P | 404 |
| `GET /api/v1/comment/{id}/reactions` | A | 404 |
| `GET /api/v1/comment/{id}/reaction` | P | 404 |
| `GET /api/v1/comment/{id}/reaction` | A | 404 |
| `GET /api/v1/comment/{id}/likers` | P | 404 |
| `GET /api/v1/comment/{id}/likers` | A | 404 |
| `GET /api/v1/post/{id}/comment/{id}/reaction` | P | 404 |
| `GET /api/v1/post/{id}/comment/{id}/reaction` | A | 404 |
| `GET /api/v1/reader/comment/{id}/reactors` | A | 404 |
| `GET /api/v1/reactions/comment/{id}` | A | 404 |

### Endpoint that works

| Endpoint | Host | Status | Shape |
|---|---|---|---|
| `GET /api/v1/comment/{id}/reactors` | P or A | **200** | Array of reactor objects |
| `GET /api/v1/post/{id}/reactors` | P | 200 | Array (post-level, not per-comment) |

`GET /api/v1/comment/{id}/reactors` works for:

- **Post comments** (using publication host, host "P")
- **Notes** (using substack.com, host "A")
- **Note replies** (using substack.com, host "A")

Both hosts return the same response body. The routing mirrors the convention
already established by `notes.py` and `moderation_core.py`.

---

## Response shape

```json
[
  {
    "id": 10000001,
    "name": "Alice Example",
    "photo_url": "https://substack-post-media.s3.amazonaws.com/...",
    "primary_publication": {
      "id": 20000001,
      "subdomain": "aliceexample",
      "name": "Alice's Newsletter",
      "handles_enabled": false,
      "payments_state": "disabled"
    },
    "bestseller_tier": null,
    "status": {
      "subscriberTier": 1,
      "badge": {"type": "subscriber", "tier": 1}
    },
    "is_subscribed": false,
    "is_following": false,
    "writes": "Alice's Newsletter"
  }
]
```

The `handle` for impersonation detection is `primary_publication.subdomain`.
The `photo_url` is the profile photo shown in the notification the reader sees.

---

## What was shipped

New command `substack comments reactions <COMMENT_ID> [--surface notes] [--full]`
in `substack_cli/reactions.py`. Projected output: `user_id`, `name`, `handle`,
`photo_url`, `writes`. Pass `--full` for the raw reactor objects. Tests in
`tests/test_reactions.py`. Endpoint table row in `references/substack-api.md`.

---

## What remains unknown

- **Pagination:** the endpoint returned up to 12 reactors for a comment with 16
  reactions (the other four may have deleted their accounts or the response caps
  at some limit). No `nextCursor` or `hasMore` field was observed in the
  response. Whether pagination exists for high-reaction-count comments is not
  confirmed. If needed, add a `?limit=` or cursor probe in a follow-up.
- **Unauthenticated access:** not tested. The endpoint likely requires a session
  cookie since it returns `is_subscribed`/`is_following` per the authenticated
  user. Confirm before using without credentials.
- **Post-level reactor list:** `GET /api/v1/post/{id}/reactors` also returns 200
  with an array (100 items observed), but this is reactors to the post itself,
  not to a specific comment. Not wrapped by the CLI.
- **Browser network capture:** the web app may call additional reaction-identity
  endpoints not discovered by path guessing. Capturing the Notes tab's DevTools
  Network panel while viewing a note with reactions would confirm whether any
  further endpoints exist.

---

## Recommended next step

The sweep in the new comment review pipeline can call
`GET /api/v1/comment/{id}/reactors` for each recent CM comment and note reply,
then apply the existing impersonation heuristics (name similarity, photo_url
pattern matching) to the reactor list. This requires no new API discovery.
