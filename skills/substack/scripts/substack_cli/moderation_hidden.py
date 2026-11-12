"""Substack CLI — AutoMod hidden-comment core logic (Issue #29).

Pure functions only — no Typer commands.  The CLI command is registered in
moderation.py to avoid the circular-import that would arise if this module
imported moderation_app from moderation.py while moderation.py imports this.

ENDPOINT STATUS: **Unverified**.
The best-evidenced candidate path is:

    GET /api/v1/publication/moderation/hidden_comments

This path is inferred from the pattern of other moderation endpoints
(e.g. /api/v1/comment/moderation/delete_reasons) and the Substack
dashboard's "Moderation" / "Manage bans" section where AutoMod decisions
are listed.  It has NOT been captured from a live browser session.

To verify: open your publication dashboard → Moderation → (AutoMod or
"Manage bans") while DevTools Network tab is open, filter by XHR, and
look for the request that loads the hidden-comment list.  Update
AUTOMOD_HIDDEN_PATH below with the real path and remove the "Endpoint
unverified" note from the PR once confirmed.

Safety: this module contains no write operations and makes no
POST/PUT/DELETE calls.  The restore operation is intentionally NOT
implemented until the endpoint is confirmed.
"""
from __future__ import annotations

import datetime
from typing import Optional

from substack_cli.client import SubstackClient

# ---------------------------------------------------------------------------
# Endpoint constant — isolated here so callers and tests can patch it
# ---------------------------------------------------------------------------

# Candidate path inferred from the moderation endpoint family.
# REPLACE with the real path once captured from the dashboard DevTools.
AUTOMOD_HIDDEN_PATH = "/api/v1/publication/moderation/hidden_comments"

# ---------------------------------------------------------------------------
# Shorthand → timedelta map
# ---------------------------------------------------------------------------

_SINCE_SHORTCUTS: dict[str, datetime.timedelta] = {
    "1h": datetime.timedelta(hours=1),
    "6h": datetime.timedelta(hours=6),
    "12h": datetime.timedelta(hours=12),
    "24h": datetime.timedelta(hours=24),
    "48h": datetime.timedelta(hours=48),
    "7d": datetime.timedelta(days=7),
}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def parse_since(since: Optional[str]) -> Optional[datetime.datetime]:
    """Parse a --since value into a UTC datetime cutoff, or None.

    Accepts shorthand strings ("24h", "7d") or ISO-8601 datetimes.
    Returns None when since is None (meaning: no time filter).
    """
    if since is None:
        return None
    if since in _SINCE_SHORTCUTS:
        return datetime.datetime.now(datetime.timezone.utc) - _SINCE_SHORTCUTS[since]
    try:
        ts = since.replace("Z", "+00:00")
        return datetime.datetime.fromisoformat(ts)
    except ValueError:
        raise ValueError(
            f"--since {since!r} is not a recognised shorthand "
            f"({', '.join(sorted(_SINCE_SHORTCUTS))}) "
            "or a valid ISO-8601 datetime."
        )


def _format_item(raw_item: dict, *, include_raw: bool = False) -> dict:
    """Project a raw hidden-comment object into the audit-friendly shape.

    Because the endpoint is unverified we cannot know the exact field names
    the API will return; we pass the raw object through and layer our
    projected fields on top.  Missing fields fall back gracefully to None.

    Args:
        raw_item: the raw dict returned by the API for a single item.
        include_raw: when True, include the full raw API dict under ``_raw``.
    """
    author_obj = raw_item.get("author") or raw_item.get("commenter") or {}
    subscriber_state = (
        raw_item.get("subscriber_state")
        or raw_item.get("subscriberState")
        or author_obj.get("subscriberState")
    )
    result = {
        "id": raw_item.get("id"),
        "type": raw_item.get("type", "comment"),
        "author_id": raw_item.get("author_id") or author_obj.get("id"),
        "author_name": raw_item.get("author_name") or author_obj.get("name"),
        "author_handle": raw_item.get("author_handle") or author_obj.get("handle"),
        "subscriber_state": subscriber_state,
        "text": raw_item.get("body") or raw_item.get("text") or raw_item.get("bodyText"),
        "reason": raw_item.get("automod_reason") or raw_item.get("reason") or raw_item.get("hidden_reason"),
        "hidden_at": raw_item.get("hidden_at") or raw_item.get("created_at"),
        "post_id": raw_item.get("post_id") or raw_item.get("postId"),
        "post_slug": raw_item.get("post_slug") or raw_item.get("slug"),
        "link": raw_item.get("url") or raw_item.get("link"),
    }
    if include_raw:
        result["_raw"] = raw_item
    return result


def _parse_ts(ts: Optional[str]) -> Optional[datetime.datetime]:
    """Parse an ISO-8601 timestamp string; return None on failure."""
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Core function (testable without the CLI layer)
# ---------------------------------------------------------------------------

def get_hidden_comments(
    client: SubstackClient,
    since: Optional[str] = None,
    limit: int = 100,
    include_raw: bool = False,
) -> list[dict]:
    """Fetch AutoMod-hidden comments from the publication moderation endpoint.

    Args:
        client: authenticated SubstackClient
        since: optional time filter string ("24h", "7d", ISO datetime, or None)
        limit: max results to request from the API (server may return fewer)

    Returns:
        list of projected comment dicts, filtered by ``since`` if provided.

    Raises:
        ValueError: if ``since`` is not a recognised format
        SubstackApiError: on HTTP error from the API
    """
    cutoff = parse_since(since)

    params: dict = {"limit": limit}
    if cutoff is not None:
        params["since"] = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    raw = client.get(AUTOMOD_HIDDEN_PATH, **params)

    # Normalise: API may return a bare list or an envelope dict
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        for key in ("comments", "hidden_comments", "items", "results", "data"):
            if key in raw and isinstance(raw[key], list):
                items = raw[key]
                break
        else:
            import sys
            known = ("comments", "hidden_comments", "items", "results", "data")
            print(
                f"Warning: unrecognised API envelope (keys: {list(raw.keys())!r}); "
                f"expected one of {known}. Returning empty list.",
                file=sys.stderr,
            )
            items = []
    else:
        items = []

    projected = [_format_item(item, include_raw=include_raw) for item in items]

    # Client-side time filter as a safety net if the server ignores `since`
    if cutoff is not None:
        def _keep(p: dict) -> bool:
            ts = _parse_ts(p.get("hidden_at"))
            return ts is None or ts >= cutoff  # keep items with no timestamp

        projected = [p for p in projected if _keep(p)]

        # Warn if the filter had no effect because no items have timestamps
        if projected and all(p.get("hidden_at") is None for p in projected):
            import sys
            print(
                "Warning: --since filter is set but all items have no hidden_at "
                "timestamp; filter had no effect.",
                file=sys.stderr,
            )

    if len(projected) == limit:
        import sys
        print(
            f"Note: result count equals --limit ({limit}); there may be more items.",
            file=sys.stderr,
        )

    return projected
