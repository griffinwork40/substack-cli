"""Substack CLI — impersonation scan state management, recording, and summary.

State persistence (seen.json, daily log, morning summary markdown) for the
Capital Mischief impersonation scan.
Scan orchestration lives in impersonation.py; detection rules in impersonation_detect.py.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATE_DIR = Path.home() / ".afk" / "state" / "impersonation"
SEEN_FILE = STATE_DIR / "seen.json"

DEFAULT_POST_LIMIT = 10
DEFAULT_NOTES_LIMIT = 20
DEFAULT_DAILY_CAP = 20
REQUEST_DELAY = 1.5  # seconds between read requests (polite crawl)

SUBSTACK_COM = "https://substack.com"

# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def _load_seen() -> dict:
    """Load the persistent seen-state dict.

    Shape: {"acted_user_ids": [...], "daily_counts": {"YYYY-MM-DD": N}}
    """
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text())
        except json.JSONDecodeError:
            pass
        except OSError as exc:
            print(f"[impersonation] Warning: could not read seen-state {SEEN_FILE}: {exc}", flush=True)
    return {"acted_user_ids": [], "daily_counts": {}}


def _save_seen(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(state, indent=2))


def _today() -> str:
    return date.today().isoformat()


def _daily_count(state: dict) -> int:
    return state.get("daily_counts", {}).get(_today(), 0)


def _increment_daily(state: dict) -> None:
    state.setdefault("daily_counts", {})[_today()] = _daily_count(state) + 1


# ---------------------------------------------------------------------------
# Dry-run report builder
# ---------------------------------------------------------------------------

def _dry_run_report_user(user_id: int, reason_str: str) -> dict:
    """Build the would-be report-user request (no network call)."""
    return {
        "dry_run": True,
        "method": "POST",
        "url": f"{SUBSTACK_COM}/api/v1/users/{user_id}/profile/report",
        "body": {
            "reportCategory": "impersonation",
            "details": f"Impersonation of Capital Mischief author Charlie Garcia. Triggers: {reason_str}",
            "reportedToSubstack": True,
            "surface": "profile",
        },
    }


# ---------------------------------------------------------------------------
# Comment tree walker
# ---------------------------------------------------------------------------

def _walk_comments(comments: list) -> list[dict]:
    """Iteratively flatten a comment tree (comments + their children).

    Uses an explicit stack rather than recursion so arbitrarily deep trees
    (e.g. adversarially nested comment chains) never hit Python's recursion
    limit.  Output order is identical to the former recursive implementation:
    each node appears before its children (pre-order, depth-first).
    """
    flat: list[dict] = []
    # Stack entries are (node, index_into_children_already_emitted).
    # We push (comment, children_list) pairs; process each comment first,
    # then push its children onto the stack in reverse order so the leftmost
    # child is processed next (preserving sibling order).
    stack: list[dict] = list(reversed(comments))
    while stack:
        c = stack.pop()
        if not isinstance(c, dict):
            continue
        flat.append(c)
        children = c.get("children") or c.get("comments") or []
        if children:
            # Reverse so that the first child ends up on top of the stack.
            stack.extend(reversed(children))
    return flat


# ---------------------------------------------------------------------------
# Candidate recording
# ---------------------------------------------------------------------------

def _record_candidate(
    candidates, uncertain, acted_ids, state,
    *, user_id, name, name_reasons, body_reasons,
    surface, ref_id, post_id,
    daily_cap, dry_run,
):
    """Classify a match as actionable (candidate) or uncertain; update state.

    Routing:
    - body_reasons only → always uncertain (never auto-reported, never counted
      toward the daily cap).
    - name_reasons (with or without body_reasons) → actionable candidate if
      within daily cap and not already acted on; otherwise uncertain/cap_exceeded.
    """
    if not name_reasons and not body_reasons:
        return

    reasons = name_reasons + body_reasons
    reason_str = ", ".join(reasons)
    entry = {
        "user_id": user_id,
        "display_name": name,
        "reasons": reasons,
        "name_reasons": name_reasons,
        "body_reasons": body_reasons,
        "surface": surface,
        "ref_id": ref_id,
        "post_id": post_id,
    }

    # Body-only → uncertain, never auto-reported, never counts toward cap
    if not name_reasons:
        entry["status"] = "body_only_review"
        uncertain.append(entry)
        return

    # Name-triggered: check dedupe and cap
    if user_id in acted_ids:
        return  # Already reported in a previous run — dedupe

    if _daily_count(state) >= daily_cap:
        entry["status"] = "cap_exceeded"
        uncertain.append(entry)
        return

    # Within cap, not seen before → actionable candidate
    if dry_run:
        entry["dry_run_call"] = _dry_run_report_user(user_id, reason_str)
    entry["status"] = "would_report"
    candidates.append(entry)

    # Mark as acted (so a re-run won't double-count in live mode)
    acted_ids.add(user_id)
    state["acted_user_ids"] = list(acted_ids)
    _increment_daily(state)


# ---------------------------------------------------------------------------
# Summary writer
# ---------------------------------------------------------------------------

def _write_summary(
    path: Path,
    *,
    posts_scanned: int,
    notes_scanned: int,
    candidates: list,
    uncertain: list,
    dry_run: bool,
    today_str: str,
) -> None:
    """Write the morning summary Markdown file."""
    mode = "DRY-RUN" if dry_run else "LIVE"
    # Separate body-only uncertain from cap-exceeded uncertain
    body_only = [u for u in uncertain if u.get("status") == "body_only_review"]
    cap_exceeded = [u for u in uncertain if u.get("status") == "cap_exceeded"]

    lines = [
        f"# Capital Mischief Impersonation Scan — {today_str} ({mode})",
        "",
        f"**Posts scanned:** {posts_scanned}  ",
        f"**Notes scanned:** {notes_scanned}  ",
        f"**Candidates (would report):** {len(candidates)}  ",
        f"**Uncertain — body-only (human review):** {len(body_only)}  ",
        f"**Uncertain — cap exceeded:** {len(cap_exceeded)}  ",
        "",
    ]

    if candidates:
        lines.append("## Would-Report List")
        lines.append("")
        for c in candidates:
            uid = c["user_id"]
            name = c["display_name"]
            reasons = ", ".join(c["reasons"])
            lines.append(f"- **user_id {uid}** — display name: `{name}` — triggers: {reasons}")
        lines.append("")

    if body_only:
        lines.append("## Body-Only Matches (Human Review — NOT auto-reported)")
        lines.append("")
        for c in body_only:
            uid = c["user_id"]
            name = c["display_name"]
            reasons = ", ".join(c["reasons"])
            lines.append(f"- user_id {uid} | `{name}` | {reasons} | status: body_only_review")
        lines.append("")

    if cap_exceeded:
        lines.append("## Cap-Exceeded Items")
        lines.append("")
        for c in cap_exceeded:
            uid = c["user_id"]
            name = c["display_name"]
            reasons = ", ".join(c["reasons"])
            lines.append(f"- user_id {uid} | `{name}` | {reasons} | status: cap_exceeded")
        lines.append("")

    lines += [
        "---",
        "_Generated by impersonation_scan. No reports were filed in dry-run mode._"
        if dry_run else
        "_Generated by impersonation_scan. Reports filed per would-report list above._",
        "",
    ]
    path.write_text("\n".join(lines))
