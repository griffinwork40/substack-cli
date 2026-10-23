"""Substack CLI — impersonation scan orchestration for Capital Mischief (charliepgarcia).

Scan orchestration only. Detection rules live in impersonation_detect.py;
state management in impersonation_state.py.

Scans recent post comments and Notes replies for fake 'Charlie' accounts that:
  1. Include WhatsApp or Telegram contact details (phone numbers or handles)
     in the commenter's display name or comment body.
  2. Use a disguised 'charlie' in their display name — including Unicode
     confusable / homoglyph substitutions (e.g. 𝐂𝐡𝐚𝐫𝐥𝐢𝐞, Ϲharlie, Ċharlie).

Safety contract
---------------
- SUBSTACK_ENABLE_WRITE must be UNSET (or != "true") when --dry-run is active.
  The script asserts this on startup and aborts if violated.
- All write actions (report-user) are emitted via the existing dry-run path
  only: {dry_run: true, method, url, body} — zero HTTP POSTs.
- Read endpoints used: GET /api/v1/archive, GET /api/v1/post/{id}/comments,
  GET /api/v1/reader/feed/profile/{user_id}, GET /api/v1/reader/comment/{id}.
- The real Charlie account (user_id 27965159, handle charliepgarcia) is
  allowlisted and will never be flagged regardless of name match.

State files
-----------
  ~/.afk/state/impersonation/seen.json        — persistent set of acted user_ids
  ~/.afk/state/impersonation/YYYY-MM-DD.json  — dated scan log
  ~/.afk/state/impersonation/YYYY-MM-DD.md    — morning summary markdown

Daily cap
---------
  Default 20 would-report candidates per calendar day (--daily-cap to override).
  Candidates beyond the cap are logged as "uncertain" (cap_exceeded).
  Body-only matches always go to uncertain regardless of the cap, and are never
  auto-reported.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from substack_cli.impersonation_detect import (
    ALLOWLISTED_USER_IDS,
    REAL_CHARLIE_USER_ID,
    _detect,
)
from substack_cli.impersonation_state import (
    DEFAULT_DAILY_CAP,
    DEFAULT_NOTES_LIMIT,
    DEFAULT_POST_LIMIT,
    REQUEST_DELAY,
    STATE_DIR,
    _daily_count,
    _load_seen,
    _record_candidate,
    _save_seen,
    _today,
    _walk_comments,
    _write_summary,
)


def run_scan(
    client,  # SubstackClient
    *,
    post_limit: int = DEFAULT_POST_LIMIT,
    notes_limit: int = DEFAULT_NOTES_LIMIT,
    daily_cap: int = DEFAULT_DAILY_CAP,
    dry_run: bool = True,
    verbose: bool = False,
) -> dict:
    """Run the impersonation scan.

    Returns a result dict with:
      posts_scanned, notes_scanned, candidates, uncertain,
      would_report (list of {user_id, name, reasons, dry_run_call}),
      summary_path, log_path
    """
    # --- Refuse live reporting (not yet implemented) ---
    if not dry_run:
        # NOTE: Any future live reporting path must require is_write_enabled()
        # and --yes before sending any POST requests. See auth.py for the
        # write-gate pattern used by moderation commands.
        import json as _json
        import sys as _sys
        _sys.stderr.write(
            _json.dumps({"error": True, "message": "live reporting not implemented; impersonation scan is dry-run only"})
            + "\n"
        )
        raise SystemExit(1)

    # --- Safety assertion ---
    if dry_run:
        write_env = os.environ.get("SUBSTACK_ENABLE_WRITE", "").strip().lower()
        if write_env == "true":
            raise RuntimeError(
                "SAFETY ABORT: SUBSTACK_ENABLE_WRITE=true is set but --dry-run is active. "
                "Unset SUBSTACK_ENABLE_WRITE before running in dry-run mode."
            )

    state = _load_seen()
    acted_ids: set[int] = set(state.get("acted_user_ids", []))

    candidates: list[dict] = []
    uncertain: list[dict] = []
    posts_scanned = 0
    notes_scanned = 0

    # ------------------------------------------------------------------ #
    # 1. Scan recent post comments                                        #
    # ------------------------------------------------------------------ #
    if verbose:
        print(f"[scan] Fetching archive (limit={post_limit}) …")

    try:
        archive = client.get("/api/v1/archive", sort="new", offset=0, limit=post_limit)
        posts = archive if isinstance(archive, list) else archive.get("posts", [])
    except Exception as exc:
        posts = []
        if verbose:
            print(f"[scan] Archive fetch failed: {exc}")

    for post in posts:
        if not isinstance(post, dict):
            continue
        post_id = post.get("id")
        if not post_id:
            continue

        time.sleep(REQUEST_DELAY)
        try:
            raw = client.get(f"/api/v1/post/{post_id}/comments")
        except Exception as exc:
            if verbose:
                print(f"[scan] Comments fetch failed for post {post_id}: {exc}")
            continue

        # Tolerate bare list or envelope
        if isinstance(raw, list):
            comment_list = raw
        elif isinstance(raw, dict):
            comment_list = (
                raw.get("comments") or raw.get("items") or raw.get("data") or []
            )
        else:
            comment_list = []

        all_comments = _walk_comments(comment_list)
        posts_scanned += 1

        for c in all_comments:
            user_id = c.get("user_id") or (c.get("author") or {}).get("id")
            name = c.get("name") or (c.get("author") or {}).get("name") or ""
            body = c.get("body") or ""
            comment_id = c.get("id")

            if not user_id:
                continue
            if int(user_id) in ALLOWLISTED_USER_IDS:
                continue

            name_reasons, body_reasons = _detect(name, body, user_id=int(user_id))
            _record_candidate(
                candidates, uncertain, acted_ids, state,
                user_id=int(user_id), name=name,
                name_reasons=name_reasons, body_reasons=body_reasons,
                surface="comment", ref_id=comment_id, post_id=post_id,
                daily_cap=daily_cap,
                dry_run=dry_run,
            )

    # ------------------------------------------------------------------ #
    # 2. Scan Charlie's Notes replies                                     #
    # ------------------------------------------------------------------ #
    if verbose:
        print(f"[scan] Fetching Charlie's Notes (limit={notes_limit}) …")

    time.sleep(REQUEST_DELAY)
    try:
        notes_raw = client.get(
            f"/api/v1/reader/feed/profile/{REAL_CHARLIE_USER_ID}",
            host="A",
            limit=notes_limit,
        )
    except Exception as exc:
        notes_raw = []
        if verbose:
            print(f"[scan] Notes feed fetch failed: {exc}")

    # Notes feed shape: list of {item: {comment: {id, ...}, children: [...]}}
    note_items = notes_raw if isinstance(notes_raw, list) else (
        notes_raw.get("items") or notes_raw.get("posts") or []
    )

    for item_wrapper in note_items:
        if not isinstance(item_wrapper, dict):
            continue
        item = item_wrapper.get("item") or item_wrapper
        note_comment = item.get("comment") or {}
        _raw_entity_key = item_wrapper.get("entity_key", "")
        if note_comment.get("id"):
            note_id = note_comment["id"]
        elif _raw_entity_key.startswith("c-"):
            note_id = _raw_entity_key[2:]  # strip the "c-" prefix
        else:
            if _raw_entity_key:
                import sys as _sys
                _sys.stderr.write(
                    f"[impersonation] Warning: unexpected entity_key format"
                    f" {_raw_entity_key!r} — skipping note item\n"
                )
            continue

        # Replies are in item.children
        children = item.get("children") or []
        all_replies = _walk_comments(children)
        notes_scanned += 1

        for reply in all_replies:
            user_id = reply.get("user_id") or (reply.get("author") or {}).get("id")
            name = reply.get("name") or (reply.get("author") or {}).get("name") or ""
            body = reply.get("body") or ""
            reply_id = reply.get("id")

            if not user_id:
                continue
            if int(user_id) in ALLOWLISTED_USER_IDS:
                continue

            name_reasons, body_reasons = _detect(name, body, user_id=int(user_id))
            _record_candidate(
                candidates, uncertain, acted_ids, state,
                user_id=int(user_id), name=name,
                name_reasons=name_reasons, body_reasons=body_reasons,
                surface="note_reply", ref_id=reply_id, post_id=None,
                daily_cap=daily_cap,
                dry_run=dry_run,
            )

    # ------------------------------------------------------------------ #
    # 3. Persist state + write outputs                                    #
    # ------------------------------------------------------------------ #
    _save_seen(state)

    today_str = _today()
    log_path = STATE_DIR / f"{today_str}.json"
    summary_path = STATE_DIR / f"{today_str}.md"

    log_data = {
        "scan_ts": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "posts_scanned": posts_scanned,
        "notes_scanned": notes_scanned,
        "candidates_found": len(candidates),
        "uncertain_count": len(uncertain),
        "would_report": candidates,
        "uncertain": uncertain,
    }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(log_data, indent=2))

    # Morning summary markdown
    _write_summary(
        summary_path,
        posts_scanned=posts_scanned,
        notes_scanned=notes_scanned,
        candidates=candidates,
        uncertain=uncertain,
        dry_run=dry_run,
        today_str=today_str,
    )

    return {
        "posts_scanned": posts_scanned,
        "notes_scanned": notes_scanned,
        "candidates_found": len(candidates),
        "uncertain_count": len(uncertain),
        "would_report": candidates,
        "uncertain": uncertain,
        "log_path": str(log_path),
        "summary_path": str(summary_path),
    }


# ---------------------------------------------------------------------------
# CLI entry point (invoked by impersonation_scan CLI command)
# ---------------------------------------------------------------------------

def run_cli(
    post_limit: int = DEFAULT_POST_LIMIT,
    notes_limit: int = DEFAULT_NOTES_LIMIT,
    daily_cap: int = DEFAULT_DAILY_CAP,
    dry_run: bool = True,
    verbose: bool = False,
) -> None:
    """Build a client and run the scan. Called from the typer CLI command."""
    from substack_cli.auth import resolve_cookies, resolve_publication_url
    from substack_cli.client import SubstackClient

    cookies = resolve_cookies()
    pub_url = resolve_publication_url()
    client = SubstackClient(cookies=cookies, publication_url=pub_url)

    result = run_scan(
        client,
        post_limit=post_limit,
        notes_limit=notes_limit,
        daily_cap=daily_cap,
        dry_run=dry_run,
        verbose=verbose,
    )

    import json as _json
    print(_json.dumps(result, indent=2))
    print(f"\n[scan] Summary written to: {result['summary_path']}", flush=True)
