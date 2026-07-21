# Plan — The Content Flywheel (`amplify`)

_Status: DRAFT / not started · Created 2026-07-21 · Owner: TBD_
_Companion to `docs/FEATURE-GAPS.md` (§Killer-feature synthesis)._

## Goal

Turn one published post into ongoing subscriber growth via a closed loop:
**Analyze → Atomize → Distribute → Measure.** Ship it in safe, independently-valuable slices.

**One-liner:** _"Find your best post, break it into on-brand Notes, drip them across Substack + X +
Threads, and measure which channel drove signups — one command, agent-orchestrated."_

**Non-goals (v1):** auto-posting without human approval; a built-in LLM inside the CLI; paid-tier /
Stripe automation; video.

---

## Load-bearing architecture decision

**The CLI stays a thin, deterministic API wrapper. All intelligence lives in the skill layer.**

| Layer | Owns | Testable how |
|---|---|---|
| **CLI** (`substack_cli/`) | Deterministic API I/O only: bulk-stats reads, ranking math, `notes create`, `read post`. **No LLM, no network to X/Threads.** | `respx`-mocked pytest |
| **Skill** (`amplify` orchestration) | The flywheel: decide top post, call the agent to atomize into Notes (`voice-match`), sequence them, invoke `x-api`/`threads-api`, drive the AFK scheduler. | manual / eval |

Consequence: **~60% of this feature is skill/orchestration authoring, not endpoint-wrapping.** Only
the Analyze half (digest/bulk-stats) needs new CLI commands. Distribute/Atomize reuse existing
`read post` + `notes create` + sibling skills.

---

## Phase 0 — De-risking spikes (run FIRST, against a real publication)

These gate the plan. Each is a `--pretty` call run once against a live publication, then we lock the
shape into a `respx` fixture.

> **RESULTS (2026-07-21, run live against a configured publication):**
> - **Spike 2 — RESOLVED ✅.** `GET /post_management/detail/{id}?offset=0&limit=1` returns a rich
>   nested `stats` block (~30 fields): `views`, `opens`, `open_rate`, `clicks`,
>   `signups_within_1_day`, `subscriptions_within_1_day`, `engagement_rate`, `estimated_value`,
>   `shares`, a `firstWeekDailyStats[]` timeseries, and `comps` benchmarks. **This DISPROVES the old
>   "metadata only" caveat.** **Bug found + fixed:** near-term signups live in `signups_within_1_day`
>   (top-level `signups` reads 0 on recent posts) — `_STAT_SORT_FIELDS` corrected; `analytics posts`
>   verified ranking live (2 → 1 → 0 signups_1d).
> - **Spike 1 — deferred (non-blocking).** N+1 via `detail/{id}` confirmed working; whether
>   `post_management/published` carries stats (→ 1 call) is an untested optimization. Not needed to ship.
> - **Spikes 3–4 — open**, gate Milestone 2/3 (Notes threading/scheduling; X/Threads auth).

1. **Does the bulk list carry per-post stats?** Probe `GET /api/v1/post_management/published`
   (and `?offset=0&limit=1`). **If it returns a `stats` block per post → digest is 1 call.
   If not → digest is N× `post_management/detail/{id}` (N+1, needs throttle).** This single answer
   sets the digest design. _(Gap #6; endpoint is `med` confidence per FEATURE-GAPS.)_
2. **Confirm the per-post `stats` field set.** Capture one `GET /post_management/detail/{id}?offset=0&limit=1`
   response; record which of the ~31 fields (open_rate, clicks, CTR, signups, referrers…) actually
   come back. Ranking + digest depend on `signups`/`open_rate` existing.
3. **Note threading & scheduling reality.** Confirm whether `notes create` can reply-to/thread, and
   accept that Notes have **no native schedule endpoint** → cadence = AFK cron, not Substack.
4. **Cross-platform auth availability.** Confirm `X_BEARER_TOKEN` + write OAuth for `x-api`, and
   Threads tokens for `threads-api`, exist in the target env. Phase 2 is blocked without them.

**Robustness property:** the Distribute/Atomize half (Milestone 2) does **not** depend on spikes
1–2. Even if the stats endpoint disappoints, `amplify` still works with an operator-chosen post.
Only the smart-Analyze half degrades (falls back to `analytics summary`, publication-level only).

---

## Milestone 1 — `substack digest` (read-only beachhead; feeds Analyze)

**Deliverable:** a deterministic, ranked view of "what's working." Pure read → **no write gate**,
lowest risk, shippable in days, and it's literally the flywheel's Analyze input.

**CLI additions (`read.py`, analytics subapp):**
- `analytics posts [--limit N] [--sort signups|open_rate|views]` — bulk per-post stats as a ranked
  JSON array. Implementation follows spike #1 (1 call vs N+1).
- `analytics digest [--top N]` — composes: archive/published list + bulk stats + publication
  `summary` → a compact object: top posts, best publish-time bucket, subject-line length vs
  open-rate, signup drivers. **CLI emits ranked data; narrative interpretation is skill-side.**

**Helper shape (mirror `get_post_analytics`, `read.py:302`):**
```python
def get_posts_analytics(client, *, limit=25, sort="signups") -> list:
    # spike#1 decides: single list call, else loop detail/{id} with client throttle
    ...
```

**Tests (mirror `tests/test_read_analytics.py`):** `@respx.mock` + `fake_cookies` /
`fake_publication_url` fixtures; mock the list/detail route(s); assert ranking order, sort key,
limit, and graceful degradation when `stats` is absent. Add `tests/test_read_digest.py`.

---

## Milestone 2 — `amplify` Phase 1 (Substack-only flywheel core)

**Deliverable:** `read post` → agent atomizes → `notes create`. 100% confirmed Substack endpoints,
zero external auth. This is the smallest end-to-end flywheel.

**Flow (skill orchestration):**
1. Pick post: operator-supplied id/slug, or top of `analytics digest` (M1).
2. `substack read post <slug>` → full body (`/api/v1/posts/{slug}`, `read.py:221`).
3. Agent atomizes body into an ordered set of Notes, drafted with **`voice-match`** (fabrication
   guard: every claim traces to the post) and **`thesis-lock`** (confirm angle before drafting).
4. **Dry-run by default:** print the drafted Notes for human review.
5. On approval → `substack notes create` per Note (confirmed create endpoint, `notes.py`).

**Safety:** rides the existing `SUBSTACK_ENABLE_WRITE=true` gate; `notes create` is public →
require explicit human approval (dry-run → approve) even though Notes aren't in the six `--yes`
commands. Never auto-fire unattended in this phase.

**CLI additions:** likely none (reuse `read post` + `notes create`). *Optional:* `notes create
--reply-to <id>` for threaded sequences — only if spike #3 confirms threading. New skill:
`amplify` (or extend the existing `substack` skill).

**Tests:** CLI side already covered (`test_notes.py`, `test_read_posts.py`). Skill side = manual +
an eval on atomization quality (voice fidelity, claim-traceability, no fabrication).

---

## Milestone 3 — `amplify` Phase 2 (cross-platform distribution)

**Deliverable:** push the atomized Notes (and post link) to X + Threads with back-links.

- **Blocked on Phase 0 spike #4** (X + Threads auth). No CLI changes — pure skill orchestration
  calling sibling `x-api` / `threads-api` skills.
- Add back-links to the Substack post; carry a tracking param if attribution (M4) needs it.
- **Cadence = AFK scheduler** (`create_schedule` / `automate` skill): drip Notes/posts over days.
- **Irreversible/public →** hard approval gate; log every external send; honor rate limits
  (429 backoff — see `x-api`).

**Risk:** cross-platform posting is the highest-consequence step. Must be approve-per-send or
approve-the-batch, never silent. Recommend a `--plan` output the operator signs off on.

---

## Milestone 4 — `amplify` Phase 3 (attribution loop)

**Deliverable:** close the loop — attribute signups back to Notes/channels, feed into M1 ranking.

- Re-pull `analytics summary` growth + per-post stats after a distribution run; diff subscriber
  deltas against distribution timestamps.
- **Lowest confidence:** Substack gives no clean per-referrer signup attribution via the private
  API (best-effort: `network_attribution` / growth `sources` endpoints, `R`/`med`). Treat as
  research-heavy; may land as correlational ("subs rose N after the X drop"), not causal.
- Depends on M1 data plumbing.

---

## Cross-cutting: safety model

- Every mutating path requires `SUBSTACK_ENABLE_WRITE=true` (existing gate).
- Public/irreversible sends (Notes, X, Threads) → **dry-run → human approval**, never unattended.
- Cross-platform sends: log to a run artifact; 429-backoff; per-batch approval.
- Follow `SUBSTACK_ENABLE_WRITE` + `--yes` conventions from the CLI's write-gate design.

## Cross-cutting: test strategy

- No live network: all CLI HTTP via `respx`; `isolated_config` + `no_sleep` fixtures (per
  `docs/DEVELOPMENT.md`). Lock each spike's real response into a fixture the tests replay.
- Per repo workflow: **write the `respx` test first** → implement helper → register `@app.command`
  → `pytest -q` → update `SKILL.md` + `references/substack-api.md`.
- Skill/atomization quality → eval, not unit test.

---

## Open questions (need your call before/inside Phase 0)

1. **Scope of v1:** stop at Milestone 2 (Substack-only flywheel — safe, self-contained), or commit
   through cross-platform (M3)? _(Recommend: land M1 + M2 first, decide M3 after seeing atomization
   quality.)_
2. **`digest` interpretation:** ranked JSON only (CLI), or also an LLM narrative "what's working"
   (skill)? _(Recommend: CLI emits data, skill adds narrative — keeps CLI deterministic.)_
3. **Which repo owns the `amplify` skill** — a new skill file here, or extend the existing
   `substack` skill? 
4. **Cross-platform accounts:** do you have X + Threads write auth wired in the target env? (Gates M3.)

## Sequencing & rough effort

| # | Slice | Depends on | Risk | Effort |
|---|---|---|---|---|
| 0 | De-risking spikes | live publication | — | hours |
| 1 | `analytics posts` + `digest` | spike 1–2 | low (read-only) | ~1–2 days |
| 2 | `amplify` Phase 1 (Substack-only) | M1 optional, spike 3 | med (public Notes) | ~2–3 days |
| 3 | `amplify` Phase 2 (X/Threads) | spike 4 + M2 | high (public, irreversible) | ~2–4 days |
| 4 | `amplify` Phase 3 (attribution) | M1 + M3 | high (upstream data weak) | research-gated |

**Recommended first move:** Phase 0 spikes 1–3 (you run them against your publication), then I build
Milestone 1 (`digest`) — safe, deterministic, and it unlocks the Analyze step for everything after.
