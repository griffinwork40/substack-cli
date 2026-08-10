# Chat & DM — Agent Capability Map

_Scoping doc, 2026-08-10. Answers: "what functionality would an AI agent need to do everything a
human can do with Substack Chats and DMs?" Produced from three parallel investigations (Chat API
surface, DM API + human affordance surface, local CLI conventions), then corrected by an adversarial
verification wave — see **Part 5: Verification log** for what changed and why._

> **Verdict up front: human parity is NOT reachable through the API today.** Chat has a real write
> surface. DMs have **two** mapped endpoints, both read-only, and one of those two is of contested
> identity (see 3-B). There is **no publicly documented endpoint to send a DM, nor to read the
> contents of a single conversation.** Any "agent does everything a human can" goal is blocked on DM
> write endpoints that no community source has captured.

> **Epistemic status — read this before planning any work.** Two distinctions were sharpened by
> verification and they change what the gaps *mean*:
>
> 1. **"No endpoint documented" ≠ "no endpoint exists."** Substack's own UI demonstrably sends DMs,
>    replies in Chat, reacts, and bans. The backend endpoints therefore **exist**; nobody has
>    captured and published them. Every gap below is a *mapping* gap, not a capability wall.
> 2. **Un-mapped ≠ probed-and-absent.** For the most important gap — replying inside a chat thread —
>    verification found no evidence anyone ever *attempted* the capture. That is a very different
>    risk profile from "someone tried and failed," and it makes the gap cheap to close.
>
> **Source concentration:** the Chat write surface rests on **exactly one** independent source
> (`AnthonyDavidAdams/substack-api-reference`). An adversarial verifier searched ~15 distinct Substack
> client projects across Python/TS/Go/Ruby, npm/PyPI, MCP catalogs and gists, and independently
> re-derived that count as **1** — it did not find false corroboration, it confirmed the single-sourcing.
> Roughly six other clients corroborate the *absences* by omission. **No implementation should start
> before a live DevTools capture.**

---

## Part 1 — Endpoint reality

### 1A. Documented write/read surface (single-source; see capture-method note)

| Capability | Method + path | Host |
|---|---|---|
| Enable/configure Chat | `POST /api/v1/publication/{pub_id}/publication_threads_settings` — `threads_v2_enabled`, `create_thread_minimum_role`, `reader_thread_notifications_enabled` | pub subdomain |
| **Send a thread** | `POST /api/v1/community/publications/{pub_id}/posts` — `id` (client-generated UUID¹), `body`, `media_urls[]`, `audience`, `type`, `send_email`, `send_push`, `link_url` | pub subdomain |
| List threads | `GET /api/v1/community/publications/{pub_id}/posts` | pub subdomain **or** bare — *sources disagree* |
| List scheduled threads | `GET /api/v1/community/publications/{pub_id}/posts/scheduled` | pub subdomain |
| Delete a thread | `DELETE /api/v1/community/posts/{thread_id}` — **soft-delete**; body cleared, record persists | pub subdomain |
| Unread counts | `GET /api/v1/messages/unread-count` → `pubChatUnreadCount`, `unreadCount`, `pendingInviteCount`, … | either |
| **Write** read-state cursor | `PUT /api/v1/user-setting` — `{type:"newest_seen_chat_item_published_at", value_datetime:ISO}` | either |
| Realtime push token | `GET /api/v1/realtime/token` → JWT, aud `zync`, ~1h TTL, websocket for Notes+chat | either |
| Reaction catalog (read) | `GET /api/v1/threads/reactions` | bare |
| DM inbox list | `GET /api/v1/messages/inbox` — **identity contested, see 3-B** | bare |
| Blocked-user ids (read) | `GET /api/v1/blocks/ids` | bare |

**Capture-method correction.** The source labels these ✅ with a legend meaning "curl-tested," but its
own methodology note says the **write-side** bodies (thread send/delete) were captured by
monkey-patching `fetch`/XHR in a Chrome extension during live add-then-revert cycles. That is a
*stronger* method than curl — it is the real browser payload — but the "curl-verified" label is
inaccurate, and anything inferred from "curl works, therefore no browser headers needed" does **not**
follow. Treat header-gating as an open question (Part 2, Layer 0 item 2).

¹ **`id` is asserted required, not proven required.** The source states three times that `id` is a
client-generated idempotency key but never demonstrates the failure mode (no captured 400 for an
omitted `id`). Design for it; confirm it in the probe.

### 1B. Reported only — library-implemented, never independently captured

| Capability | Method + path | Notes |
|---|---|---|
| List replies in a thread | `GET /api/v1/community/posts/{thread_id}/comments?order=asc&initial=true` | Real **cursor** pagination: `before_id`/`after_id` + `moreBefore`/`moreAfter`. Host: bare. |
| List replies-to-a-reply | `GET /api/v1/community/comments/{comment_id}/comments` | Found by reading the web app's JS bundle. Same cursor mechanism. |

Namespace split: threads are `/community/posts/{id}`, replies are `/community/comments/{id}`. Do
**not** assume the Notes-path `DELETE /comment/{id}` works on chat replies.

### 1C. UNMAPPED — and, for the top item, apparently never probed

**`chat reply` — post a reply into an existing thread. `[needs-human-review]`**
This is the single highest-value gap: without it an agent can broadcast but not converse. Its status
was **downgraded during verification** from "no known endpoint" to *unmapped and never attempted*.
Evidence: two methodologically-independent mapping efforts (Chrome-extension capture; JS-bundle
inspection) both stopped at *reading* replies, and neither's changelog mentions trying to capture a
reply-send. Substack's own docs confirm subscribers reply in-thread, so the endpoint exists. Reading
replies already works at `/community/posts/{id}/comments`, so the write is plausibly a sibling POST in
the same namespace. **One DevTools capture likely closes this**, and if it does, the browser-automation
fork in Layer 6 largely evaporates.

**Chat, also unmapped:** delete an individual reply · apply a reaction (catalog readable; the applying
POST is unmapped even for Notes) · edit a thread's initial message · lock/unlock replies · ban a
subscriber · approve/remove reported content · @mention · **create** a scheduled thread (list exists,
create-scheduled does not) · search within Chat · mute a Chat.

**DMs — effectively the entire write surface:** send a DM · fetch one conversation / its history ·
mark read · delete a single message · delete/leave a thread · block/unblock (write) · accept/decline a
request · group-DM management (create, rename, add/remove, ≤50) · react · mute · report · notification
preferences.

### 1D. Product constraints that shape any design

- **Read receipts do not exist** on any platform — an agent can never know a DM was read.
- **No bulk/broadcast DM.** 1:1 or a manually-built ≤50-person group. Any "message my subscribers"
  feature is a loop over individuals — precisely the shape that turns an agent into a spam engine.
- **Tier gating is first-class.** `create_thread_minimum_role` (free/paid/founding/contributors) and
  per-thread `audience` mean the agent must know *who it is talking to and at what tier*.
- **402 → payment required** for paid-gated chat access; needs a typed error, not a generic failure.
- Several affordances are **app-only** (delete a single DM message, lock replies, quote-reply, mute)
  or **web-only** (edit a thread message). A headless HTTP client inherits neither.
- Message body format (plain text vs. ProseMirror JSON) is **unresolved** — the source flags it itself.

---

## Part 2 — What we'd have to build

### Layer 0 — Transport prerequisites this CLI lacks (all four verified against source)

1. **Client-generated UUIDs.** Thread send needs a client-supplied `id`. The package has **zero**
   id-generation of any kind — no `uuid`, no `secrets`, no `os.urandom`, no random-hex scheme; ids are
   always server-assigned or passed through (`publish.py:194`, `read.py:391`). New infrastructure.
   *[verified CONFIRMED — independent re-derivation]*
2. **Per-endpoint header override (Referer / Origin / Sec-Fetch-\*).** No path exists.
   `build_headers` returns a fixed 3-key dict — `Cookie`, `User-Agent`, `Accept` (`auth.py:98-104`) —
   handed to `httpx.Client` once at construction (`client.py:73-76`); no verb method accepts a
   `headers` kwarg, and `**params` is forwarded as query-string only (`client.py:135-242`). Given the
   capture-method correction above, header-gating for chat routes is **unresolved**, so this may be
   required work. *[verified CONFIRMED]*
3. **Real cursor pagination.** `extract_pagination_meta` is imported at `client.py:13` and never
   invoked anywhere in production — its only call sites are `tests/test_models.py:47,52,56`. Chat
   replies use genuine `before_id`/`after_id` cursors, so `chat replies` would be its first real
   consumer. *[verified CONFIRMED — exhaustive call-site search incl. dynamic dispatch]*
4. **Mixed per-call host routing.** Chat writes → publication subdomain; reply reads → bare
   `substack.com`. The `host: Literal["A","P"]` param supports this already; it is hardcoded per call
   from empirical discovery, never auto-detected.
5. **Body-format discipline.** Notes send `bodyJson` as a **nested object** (`notes.py:23,151`);
   drafts **stringify** via `_prosemirror_doc_to_body_string` (`publish.py:141-145`). Invert them and
   Substack renders literal `{type:"doc",...}` as text. Chat's format is unknown — resolve by capture.
6. **Typed 402 handling** for paid-gated chat.

### Layer 1–4 — The command surface

```
chat settings                 # documented — enable/disable, minimum role, reader notifications
chat send                     # documented — the blast-radius command (see gates below)
chat list                     # documented
chat scheduled                # documented
chat delete <thread-id>       # documented — SOFT delete; not a retraction
chat replies <thread-id>      # reported — first real cursor-pagination consumer
chat sub-replies <comment-id> # reported
chat unread                   # documented
chat seen --at <iso>          # documented — writes Substack's own cursor
dm inbox                      # documented — but see 3-B, identity contested
dm unread                     # documented
dm blocks                     # documented (read-only)
chat reply                     ← UNMAPPED, never probed — probe #1, likely cheap
dm send / dm conversation      ← UNMAPPED — probe #2, may be a genuine wall
```

Roughly: **Chat ≈ half** the human affordance list has a documented endpoint. **DMs ≈ 2 of ~19**, both
read, one contested.

### Layer 5 — Agent-operability requirements *beyond* human parity

A human has eyes, a notification badge, and judgment. An agent needs these made explicit:

1. **Seen-state cursor + dedupe ledger.** Without a high-water mark an agent re-answers the same reply
   forever. Substack's own cursor is *writable* (`PUT /user-setting`) — use it, plus a local ledger
   keyed by comment id, because the server cursor is timestamp-based and coarse.
2. **Idempotency, persisted before the call — the sharpest hazard here.** The client-generated UUID is
   a natural dedupe key *only if written to disk before the POST and reused on retry*. The transport
   layer already auto-retries: `DEFAULT_MAX_RETRIES = 3` → `max_attempts = self._max_retries + 1`
   (`client.py:22,153`), and **no call site overrides it**, so 3 extra attempts always ship.
   **Precise trigger set** (verification corrected an over-broad earlier claim): retries fire on
   `httpx.HTTPError` transport/connection failures (`client.py:178-183`) and on **HTTP 429 only**,
   honoring `Retry-After` when parseable else exponential backoff (`client.py:188-198`). Every other
   status ≥400 — 500/502/503 and all other 4xx — raises immediately with **no** retry
   (`_raise_for_status`, `client.py:200`).
   *This narrowing makes the hazard worse, not better:* 429 is the single most likely response when
   blasting a thread to a large list, so the one condition that auto-retries is exactly the one a bulk
   send provokes. Mint the UUID per-attempt and a throttled retry **re-posts to your entire subscriber
   list.** Separately, `publish.py:247` holds an unrelated single-shot 404→POST verb-fallback — a
   *different* mechanism; do not conflate them when reasoning about retry safety.
3. **Watch loop.** Either cheap polling on `unread-count` (respecting the 1.0s min-interval throttle,
   `client.py:85-89`) or a websocket via `GET /realtime/token` with ~60s JWT refresh. Polling is the
   pragmatic v1; the token endpoint is the ceiling.
4. **Thread-context assembly.** To reply coherently the agent needs the whole ancestor chain — thread
   body + prior replies + author identity + `pub_roles` (who is writer/moderator) + tier. A synthesized
   conversation object, not a raw API response.
5. **Self-identity guard.** Cross-reference `whoami` on every reply candidate so the agent never
   replies to itself and loops.
6. **Blast-radius gates — the most important item here.** `chat send` with `send_email: true` +
   `send_push: true` notifies **the entire subscriber list**: irreversible external action against
   thousands of real people. `SUBSTACK_ENABLE_WRITE` + `--yes` is not proportionate. Required: default
   `send_email`/`send_push` to **false**, explicit `--notify` opt-in, `--dry-run` printing the exact
   payload and estimated recipient count, and `--yes` on top.
7. **Volume governor.** Per-run send cap, per-recipient cooldown, hard stop. Substack's anti-spam rules
   are undocumented (a third-party "10 DMs/day to non-connected users" claim is **uncorroborated** by
   Substack's own docs — do not encode it as truth).
8. **Append-only audit log** (JSONL: timestamp, endpoint, payload hash, response id). "Did the agent DM
   my subscribers?" must be answerable after the fact.
9. **Draft → human-approve → send queue as the DEFAULT for anything person-facing.** The
   `cm-repurpose` pattern already in use: agent drafts, deterministic guard checks, human approves,
   then send.
10. **Fabrication guard on outbound text** — every factual claim traceable to the publication's own
    published words (`cm-repurpose`'s `verify_framed`).
11. **Soft-delete awareness.** Chat delete clears the body but keeps the record. "Deleted" ≠ "never
    happened"; never report a delete as a retraction.

### Layer 6 — The fork in the road (now contingent, not settled)

For affordances with no endpoint, the only parity path is **browser automation** — which turns a pure
httpx/typer client into a Playwright-carrying one, with its own auth, headless-detection and
maintenance burden.

**This decision is explicitly deferred until after probes #1 and #2.** The earlier draft treated the
fork as near-inevitable; verification showed that rested on presuming `chat reply` absent when it is
merely un-captured. If probe #1 lands, Chat reaches near-parity over plain HTTP and browser automation
is only needed for DM writes and moderation — a much smaller, more deferrable scope. Sequence the
probes first; do not pre-commit the architecture.

---

## Part 3 — Probe before build

Each is a DevTools/HAR capture against a real logged-in session, not a code task. Ordered by
value-per-effort after verification re-ranked them.

1. **`chat reply` create.** Now the clear #1: highest value (broadcast → conversation), plausibly
   never attempted, and a sibling of an already-known read path. Capture a reply sent from the web UI.
   Cheap to falsify, expensive to wrongly build around.
2. **DM send + DM conversation fetch.** Unblocks the entire DM half. If a capture shows nothing
   obtainable, say so loudly and strike DM features from planning rather than designing around them.
3. **3-B — DM inbox identity.** Confirm `GET /api/v1/messages/inbox` really is the **DM** inbox and not
   the reader/notification inbox. A Ruby client (`Duartemartins/substack`) exposes similarly-named
   `inbox_top`/`unread_count` that map to the *notification* inbox — "inbox" is overloaded in this API.
   If contested, one of our two mapped DM endpoints isn't a DM endpoint at all.
4. **Header gating.** Do chat/DM routes need `Referer`/`Sec-Fetch-*`? Determines whether Layer 0
   item 2 is required work. Newly elevated: the "curl-clean" basis for assuming *no* was shown to be a
   mislabel.
5. **Chat body format** — plain text vs. ProseMirror; nested vs. stringified.
6. **`id` requiredness** — omit it once and observe whether the API 400s.
7. **Reaction apply-POST** (shared with Notes; unmapped for both).
8. **Scheduled-thread create**, then the moderation set (ban, lock, approve/remove).

## Part 4 — Repo conventions any implementation must follow

- Register the subapp in `app.py` (Typer instance + `add_typer`), and **do not** import the domain
  module there — `app.py` must stay a leaf (`config.py:5` creates a cycle; see the `§1.3.1` comment at
  `app.py:29-35`).
- Add the module to the entry script's explicit pre-import block (`scripts/substack:8-14`) or the
  commands never register.
- Tests **must** import the domain module directly, not just the subapp — decorators register as an
  import side effect (`test_notes.py:12-24`). Also add it to `test_app.py:14-19` and the subapp-name
  list at `test_app.py:29-31`, or the subapp is silently unverified.
- There is **no shared `--yes` helper** — each destructive command re-implements the guard inline
  (`notes.py:289-295,347-353,417-422`). Given Chat/DM blast radius, this is the moment to extract one.
- Reuse: `is_write_enabled` (`auth.py:89-95`), `emit_error`/`output`/`output_list`
  (`client.py:250-302`), `extract_list` (`models.py:41-62`), `_parse_inline` (`publish.py:33`),
  `_normalize_comment_id` (`notes.py:108-119`), `_load_body_json` (`notes.py:83-105`).
- `notes.py` is the closest structural analog — comment-based resource, rich-text body,
  create/reply/list/get/delete. Copy its command-wrapper shape verbatim.
- Update `references/substack-api.md` and `SKILL.md`; this repo treats those as convention, not optional.

---

## Part 5 — Verification log

An adversarial wave re-derived the load-bearing claims independently, without seeing the original
investigators' reasoning or sources. Outcome: **4 CONFIRMED, 3 CONFIRMED-with-correction,
1 DOWNGRADED, 1 UNVERIFIED.**

| Claim | Verdict | Action taken |
|---|---|---|
| `extract_pagination_meta` uncalled in production | CONFIRMED | kept |
| Zero client-side UUID generation in package | CONFIRMED | kept |
| No per-endpoint header-override mechanism | CONFIRMED | kept |
| Transport auto-retries up to 3 extra attempts | CONFIRMED, **narrowed** | trigger set corrected to transport-errors + 429 **only**; 5xx/other-4xx never retry. Hazard restated as *worse*. |
| DM send / conversation-fetch unmapped | CONFIRMED, **re-framed** | flavor corrected to "not publicly documented," not "does not exist"; ~15 sources searched, `INDEPENDENT_SOURCE_COUNT: 1` + ~6 silent corroborations |
| Chat thread create/delete/settings documented | CONFIRMED, **single-source** | capture method corrected (Chrome-extension XHR capture, not curl); `id`-requiredness marked asserted-not-proven |
| `chat reply` has no known endpoint | **DOWNGRADED → `[needs-human-review]`** | re-stated as *unmapped and never attempted*; promoted to probe #1; **Layer 6 architecture fork de-committed** |
| `openapi.yaml` promotes a competing settings path | **UNVERIFIED** | verifier found no competing path in `ENDPOINTS.md` and could not fetch `openapi.yaml` (network). Earlier draft stated this as fact — **claim removed**; use the documented `/publication/{id}/...` path |

New finding surfaced only by verification: the **DM-inbox identity trap** (probe 3-B) — "inbox" is
overloaded across Substack's surface, so one of the two mapped DM endpoints may not be a DM endpoint.

---

## Part 6 — Adversarial critique of this plan (devils-advocate wave)

Three independent critics (pragmatist / paranoid / architect), each given only the proposal + goal
and barred from reading this doc, plus an independent synthesis agent. The steelman lens was skipped
per contract (self-authored proposal). **Outcome: `dissent = true` — the plan above should NOT be
executed as written.**

### Ranking matrix

| Option | Cost | Risk | Scope-fit | Goal-fit |
|---|---|---|---|---|
| **original** (this doc, Parts 2–3) | High — 5 stages, 9 subsystems, full probe battery before any code | Medium — well-mitigated but ships a watch/poll loop mismatched to the actual deployment shape | Partial — over-built | Good — closest literal match, covers DM |
| **pragmatist** *(strong)* | Low | Med-High — no idempotency protection, zero DM coverage | Good — tight, evidence-gated | Partial — no DM, no unattended safety |
| **paranoid** *(strong)* | Medium | **Lowest** — no autonomous send exists by construction | Partial — refuses the "send" affordance the goal asks for | Poor-Partial — "everything except press send" |
| **architect** *(medium, level UP)* | Medium — relocation + integration risk | Higher on the send path (self-admittedly bypasses the one inescapable gate) | Good on DRY/precedent | Poor — a placement critique, not a command surface |

### Synthesis recommendation (spine: **pragmatist**)

Ship `chat list` + `chat unread` (GET-only, near-zero probe risk) and **exactly one** write command,
`chat reply`, through the existing `SubstackClient` with **no transport-subsystem work**: `uuid4()`
inline at the one call site if a client id is needed; a `headers=` kwarg on one method if a header is
needed. Grafts: (1) from **paranoid** — default that write command to *emitting a draft file*, with
`--send-now` as an explicit escape hatch, still env + `--yes` gated; (2) from **architect** — put
idempotency in `client.py`'s retry loop, not in a ledger, and ship it regardless of Chat/DM. Start DM
read-only (`inbox`/`unread`) to close the cheapest gap. **Drop the watch/poll loop entirely** — not
deferred; the deployment shape (agent-invoked skill, not a long-running service) makes it unneeded
scope, which also moots most of the abuse-signal objection.

### Dissent — why this is a matrix, not a decision

Both `strong` critics reject the synthesis from **opposite** directions: pragmatist considers the
mandatory draft-default extra ceremony beyond its own "dry-run captures most of the value" position;
paranoid rejects *any* code path capable of direct send.

**Strongest counter-argument (paranoid, now evidence-backed):** `client.py` is *proven* to blindly
retry POST bodies with no idempotency key **today** (see `FEATURE-GAPS.md` Tier 0-A). Shipping a
`--send-now` path against a live paying-subscriber thread before that transport bug is fixed *and
verified fixed* means a network blip can double-send a real message to a real subscriber. "A command
that isn't implemented can't be misused" is a materially stronger safety posture than gating a send
path that inherits a known-defective retry mechanism.

### Corrections the critique forced on Parts 2–3 above

- **Layer 5's watch/poll loop is now scope to cut, not build.** Wrong shape for an agent-invoked skill.
- **Layer 5's idempotency belongs DOWN in `client.py`, not in a ledger** — and it is a fix to a
  *present* bug affecting all ~47 commands, not new chat/DM machinery.
- **Probe-first was over-applied.** Probe only the endpoint being built next; the full battery
  up-front is a bottleneck, not a prerequisite.
- **The write gate should be split.** One boolean currently authorizes both "delete a tag" and
  "message a paying subscriber" (`auth.py:89-95`, applied identically ~10× across `notes.py` and
  `manage.py`). Person-facing sends need their own env var.
- **A shared thread primitive may be the real abstraction.** Notes are comment-system-backed and
  share `DELETE /api/v1/comment/{id}` with plain comments (`notes.py:236`, `manage.py:46`), though
  they differ on create (`POST /comment/feed` vs. `POST /post/{id}/comment`). Chat may be a fourth
  skin; a fifth near-copy would multiply existing duplication. **Unproven until probe #1 runs.**
- **Endpoint drift is observed history, not hypothetical** — `models.py:1-8` records Substack
  changing a response envelope without notice (the 2026-05 `/drafts` change).

### Decision required from the operator

The matrix does not resolve itself. The open question is **whether a direct-send code path should
exist in this binary at all** before Tier 0-A is fixed. Recommended order regardless of that answer:
fix `FEATURE-GAPS.md` Tier 0-A → probe `chat reply` → build read-only chat → then decide on send.
