"""Substack CLI — comment reactions: read who liked a comment or note reply.

The same endpoint serves both post comments and Notes/note replies:

    GET /api/v1/comment/{comment_id}/reactors

It is served from either host (publication subdomain or substack.com) and
returns an identical shape.  Host "P" (publication subdomain) is used by
default for post comments; host "A" (substack.com) is used for Notes and
note replies, matching the routing convention in notes.py and moderation_core.py.

Live-verified 2026-11-11 against Capital Mischief.  Confidence: HIGH.

Response shape (array of reactor objects):
    [
      {
        "id": <int>,
        "name": <str>,
        "photo_url": <str | null>,
        "primary_publication": {
          "id": <int>,
          "subdomain": <str>,   <- handle
          "name": <str>,
          ...
        },
        "bestseller_tier": <int | null>,
        "status": { "subscriberTier": <int>, ... },
        "is_subscribed": <bool>,
        "is_following": <bool>,
        "writes": <str>         <- publication display name
      },
      ...
    ]

Projected output fields (--full returns the raw reactor objects):
    user_id, name, handle (primary_publication.subdomain), photo_url, writes
"""
from __future__ import annotations

from typing import Any, List

import typer

from substack_cli.app import comments_app
from substack_cli.auth import AuthError, resolve_cookies, resolve_publication_url
from substack_cli.client import (
    SUBSTACK_COM,
    SubstackApiError,
    SubstackClient,
    emit_error,
    output,
    output_list,
)


# ---------------------------------------------------------------------------
# Core function (testable without CLI layer)
# ---------------------------------------------------------------------------


def get_comment_reactors(
    client: SubstackClient,
    comment_id: int,
    *,
    surface: str = "publication",
) -> List[dict]:
    """Return the list of users who reacted to a comment or note reply.

    GET /api/v1/comment/{comment_id}/reactors

    surface="publication" (default): uses the publication host (host "P") —
        correct for post comments.
    surface="notes": uses substack.com (host "A") — correct for Notes and
        note replies, which live on substack.com rather than the pub subdomain.

    Both surfaces return the same response shape.  Live-verified for both
    surfaces (2026-11-11).
    """
    if surface not in ("publication", "notes"):
        raise ValueError(
            f"Invalid surface {surface!r}. Must be 'publication' or 'notes'."
        )
    host = "A" if surface == "notes" else "P"
    result = client.get(f"/api/v1/comment/{comment_id}/reactors", host=host)
    if isinstance(result, list):
        return result
    # Tolerate a future envelope shape
    if isinstance(result, dict):
        for key in ("reactors", "users", "items", "data"):
            if key in result and isinstance(result[key], list):
                return result[key]
    return []


def _project_reactor(reactor: Any) -> dict:
    """Project a raw reactor object to the fields useful for scam detection."""
    if not isinstance(reactor, dict):
        return {}
    pub = reactor.get("primary_publication") or {}
    return {
        "user_id": reactor.get("id"),
        "name": reactor.get("name"),
        "handle": pub.get("subdomain"),
        "photo_url": reactor.get("photo_url"),
        "writes": reactor.get("writes"),
    }


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


def _make_client() -> SubstackClient:
    cookies = resolve_cookies()
    try:
        pub_url = resolve_publication_url()
    except AuthError:
        pub_url = SUBSTACK_COM
    return SubstackClient(cookies=cookies, publication_url=pub_url)


@comments_app.command("reactions")
def comments_reactions_cmd(
    comment_id: int = typer.Argument(
        ..., help="Comment or note id whose likers to fetch."
    ),
    surface: str = typer.Option(
        "publication",
        "--surface",
        help=(
            "Which host to query. "
            "'publication' (default) uses your publication subdomain — "
            "correct for post comments. "
            "'notes' uses substack.com — correct for Notes and note replies."
        ),
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help="Return full reactor objects instead of the projected summary.",
    ),
    pretty: bool = typer.Option(False, "--pretty"),
):
    """List users who liked (reacted to) a comment or note reply.

    Calls GET /api/v1/comment/{comment_id}/reactors and returns each
    reactor's user_id, name, handle (Substack subdomain), photo_url, and
    writes (publication name).  Pass --full to get the raw API objects.

    Use --surface notes for Notes and note replies (they live on substack.com).
    Use --surface publication (default) for post comments.

    Live-verified endpoint (2026-11-11). Read-only — no write access required.
    """
    if surface not in ("publication", "notes"):
        emit_error(
            f"Invalid --surface {surface!r}. Must be 'publication' or 'notes'.",
            pretty=pretty,
        )
        return
    try:
        client = _make_client()
        reactors = get_comment_reactors(client, comment_id, surface=surface)
        if full:
            output(reactors, pretty=pretty)
        else:
            projected = [_project_reactor(r) for r in reactors]
            output_list(projected, pretty=pretty, title="Reactors")
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)
