"""Substack CLI — Chat (read-only).

Substack Chat is the publication-scoped, thread-based messaging surface
(distinct from Notes, the public micro-blog surface, and from DMs, the
1:1/small-group private-message surface). This module implements FOUR
read-only commands and nothing else, by design:

    LIST THREADS      GET /api/v1/community/publications/{pub_id}/posts
    LIST REPLIES       GET /api/v1/community/posts/{thread_id}/comments
    LIST SUB-REPLIES    GET /api/v1/community/comments/{comment_id}/comments
    UNREAD COUNTS       GET /api/v1/messages/unread-count

CONFIDENCE — read this before trusting any of these commands. All four
endpoints are community-reported (reverse-engineered from the Substack web
app, not Substack's own docs) and have NEVER been verified against the
live API by this project. See `references/substack-api.md` § Chat. A 404
from any command here most likely means the endpoint has drifted
(Substack changed the path or response shape) or the id you passed is
wrong — NOT necessarily a mistake on your part.

SCOPE — this subapp is strictly READ-ONLY. Chat has a real write surface
(send a thread, reply, delete, react, ban, lock, etc.) but every one of
those endpoints is unverified against the live API, and a wrong guess
would post to or moderate real, paying subscribers with no undo. None of
that is implemented here. If a write command is genuinely needed, that is
a separate, deliberately-reviewed feature — do not bolt it onto this
module.

NO `publication_id` RESOLVER. No command anywhere in this CLI derives a
numeric publication id from your configured publication URL —
`/api/v1/user/profile/self` returns your *user* id, a different number,
and no other command exposes a publication id. `chat list` therefore
takes a REQUIRED `--publication-id` option; find yours via your browser's
DevTools Network tab while viewing your publication's Chat tab.

PAGINATION. `chat replies`/`chat sub-replies` accept optional
`--before-id`/`--after-id`/`--limit` and pass them straight through as
query params. The response envelope's own cursor fields are reported as
`moreBefore`/`moreAfter` — a different shape than the `hasMore`/
`nextCursor` pair `extract_pagination_meta` understands — so they are
returned to you as-is; this module does not wire `extract_pagination_meta`
and does not auto-paginate.
"""
from typing import Any, Optional

import typer
from rich.console import Console

from substack_cli.app import chat_app
from substack_cli.auth import AuthError, resolve_cookies, resolve_publication_url
from substack_cli.client import (
    SUBSTACK_COM,
    SubstackApiError,
    SubstackClient,
    emit_error,
    output,
    output_list,
)

_console = Console(stderr=True)

_ENDPOINT_DRIFT_HINT = (
    "This Chat endpoint is community-reported and has never been verified "
    "against the live Substack API. A 404 most likely means the endpoint "
    "has drifted or the id you passed is wrong, not necessarily user error."
)

_UNSAFE_ID_CHARS = set("/\\?#")


def _validate_id(value: str, name: str) -> str:
    """Raise ValueError if `value` contains path-traversal characters ('/',
    '\\', '?', '#', or '..') before it is interpolated into a URL path."""
    if ".." in value:
        raise ValueError(f"Invalid {name} {value!r}: contains '..' (path traversal).")
    bad = _UNSAFE_ID_CHARS.intersection(value)
    if bad:
        chars = ", ".join(sorted(repr(c) for c in bad))
        raise ValueError(
            f"Invalid {name} {value!r}: disallowed character(s) {chars}."
        )
    return value


def _clean_params(**kwargs: Any) -> dict:
    """Drop keys whose value is None.

    client.py's own None-stripping (`params=params or None`) only catches a
    WHOLLY empty params dict — a None value nested inside an otherwise
    populated dict reaches httpx and renders as an empty query-string value
    (e.g. `before_id=`). Do not fix this in client.py (widens blast radius
    across every command); every caller with optional cursor params filters
    None itself, as here.
    """
    return {k: v for k, v in kwargs.items() if v is not None}


# ---------------------------------------------------------------------------
# Core operations (pure functions — take a client, return API data)
# ---------------------------------------------------------------------------


def list_threads(client: SubstackClient, publication_id: int) -> Any:
    """List Chat threads for a publication.

    GET /api/v1/community/publications/{publication_id}/posts on host "P"
    (the publication subdomain). Reported, never live-verified.
    `publication_id` is int to eliminate path-traversal at parse time.
    """
    return client.get(
        f"/api/v1/community/publications/{publication_id}/posts", host="P"
    )


def list_replies(
    client: SubstackClient,
    thread_id: str,
    *,
    before_id: Optional[str] = None,
    after_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> Any:
    """List replies within a Chat thread, oldest-first.

    GET /api/v1/community/posts/{thread_id}/comments on host "A" (bare
    substack.com). Always sends order=asc & initial=true; before_id/
    after_id/limit are included only when explicitly set (None-filtered
    locally via `_clean_params`). Reported, never live-verified.
    """
    _validate_id(thread_id, "thread_id")
    params = _clean_params(before_id=before_id, after_id=after_id, limit=limit)
    return client.get(
        f"/api/v1/community/posts/{thread_id}/comments",
        host="A",
        order="asc",
        initial=True,
        **params,
    )


def list_sub_replies(
    client: SubstackClient,
    comment_id: str,
    *,
    before_id: Optional[str] = None,
    after_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> Any:
    """List replies to a reply (second-level thread), oldest-first.

    GET /api/v1/community/comments/{comment_id}/comments on host "A", same
    order=asc/initial=true + None-filtered cursor pass-through as
    `list_replies`. Reported, never live-verified.
    """
    _validate_id(comment_id, "comment_id")
    params = _clean_params(before_id=before_id, after_id=after_id, limit=limit)
    return client.get(
        f"/api/v1/community/comments/{comment_id}/comments",
        host="A",
        order="asc",
        initial=True,
        **params,
    )


def get_unread_count(client: SubstackClient) -> Any:
    """Get unread Chat/message counts.

    GET /api/v1/messages/unread-count on host "A". Known field:
    `pubChatUnreadCount` (unread Chat messages for your publication).
    Reported, never live-verified.
    """
    return client.get("/api/v1/messages/unread-count", host="A")


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def _make_client() -> SubstackClient:
    """Resolve auth and create a client for Chat operations.

    Used by `chat replies`/`chat sub-replies`/`chat unread` — all host "A"
    (substack.com) only — so, mirroring notes.py, falls back to
    substack.com when no publication URL is configured.

    NOT used by `chat list`: that command resolves pub_url directly and
    errors early when none is configured, because host "P" against
    substack.com would silently 404.
    """
    cookies = resolve_cookies()
    try:
        pub_url = resolve_publication_url()
    except AuthError:
        pub_url = SUBSTACK_COM
    return SubstackClient(cookies=cookies, publication_url=pub_url)


def _emit_chat_error(exc: Exception, pretty: bool) -> None:
    """Like emit_error(str(exc), ...), but appends the endpoint-drift hint
    on a 404 (mandatory confidence labelling — see module docstring)."""
    message = str(exc)
    status_code = getattr(exc, "status_code", None)
    if status_code == 404:
        message = f"{message}\n{_ENDPOINT_DRIFT_HINT}"
    emit_error(message, status_code=status_code, pretty=pretty)


@chat_app.command("list")
def chat_list_cmd(
    publication_id: int = typer.Option(
        ...,
        "--publication-id",
        help="Numeric Substack publication id. REQUIRED: this CLI cannot "
        "currently derive a publication id from your configured "
        "publication URL — /api/v1/user/profile/self returns your USER "
        "id, not a publication id, and no resolver exists. Find yours via "
        "your browser's DevTools Network tab while viewing your "
        "publication's Chat tab.",
    ),
    pretty: bool = False,
):
    """List Chat threads for a publication.

    UNVERIFIED — community-reported, never confirmed against the live API.
    A 404 most likely means endpoint drift or a wrong --publication-id,
    not user error.
    """
    try:
        # Resolve directly — _make_client() silently falls back to
        # SUBSTACK_COM when no pub URL is configured, but list_threads
        # needs host "P" (the publication subdomain). A real pub URL is
        # required; without one we surface a clear actionable error.
        cookies = resolve_cookies()
        try:
            pub_url = resolve_publication_url()
        except AuthError:
            emit_error(
                "chat list requires a configured publication URL — run "
                "`substack config set-publication <subdomain>` or set "
                "SUBSTACK_PUBLICATION_URL.",
                pretty=pretty,
            )
            return
        client = SubstackClient(cookies=cookies, publication_url=pub_url)
        result = list_threads(client, publication_id)
        output_list(result, pretty=pretty, title="Chat Threads")
    except (SubstackApiError, AuthError, ValueError) as exc:
        _emit_chat_error(exc, pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@chat_app.command("replies")
def chat_replies_cmd(
    thread_id: str = typer.Argument(..., help="Chat thread id (the top-level post's id)."),
    before_id: str = typer.Option(
        None,
        "--before-id",
        help="Cursor: return replies before this id (optional, pass-through, unset by default).",
    ),
    after_id: str = typer.Option(
        None,
        "--after-id",
        help="Cursor: return replies after this id (optional, pass-through, unset by default).",
    ),
    limit: int = typer.Option(
        None, "--limit", help="Max replies to return (optional, pass-through, unset by default)."
    ),
    pretty: bool = False,
):
    """List replies within a Chat thread, oldest-first (order=asc, initial=true).

    UNVERIFIED — community-reported, never confirmed against the live API.
    A 404 most likely means endpoint drift or a wrong THREAD_ID, not user
    error.
    """
    try:
        client = _make_client()
        result = list_replies(
            client, thread_id, before_id=before_id, after_id=after_id, limit=limit
        )
        output_list(result, pretty=pretty, title="Chat Replies")
    except (SubstackApiError, AuthError, ValueError) as exc:
        _emit_chat_error(exc, pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@chat_app.command("sub-replies")
def chat_sub_replies_cmd(
    comment_id: str = typer.Argument(..., help="Comment id whose replies to list."),
    before_id: str = typer.Option(
        None,
        "--before-id",
        help="Cursor: return replies before this id (optional, pass-through, unset by default).",
    ),
    after_id: str = typer.Option(
        None,
        "--after-id",
        help="Cursor: return replies after this id (optional, pass-through, unset by default).",
    ),
    limit: int = typer.Option(
        None, "--limit", help="Max replies to return (optional, pass-through, unset by default)."
    ),
    pretty: bool = False,
):
    """List replies to a reply — a second-level thread — oldest-first.

    UNVERIFIED — community-reported, never confirmed against the live API.
    A 404 most likely means endpoint drift or a wrong COMMENT_ID, not user
    error.
    """
    try:
        client = _make_client()
        result = list_sub_replies(
            client, comment_id, before_id=before_id, after_id=after_id, limit=limit
        )
        output_list(result, pretty=pretty, title="Chat Sub-Replies")
    except (SubstackApiError, AuthError, ValueError) as exc:
        _emit_chat_error(exc, pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@chat_app.command("unread")
def chat_unread_cmd(pretty: bool = False):
    """Show unread Chat/message counts.

    UNVERIFIED — community-reported, never confirmed against the live API.
    Under --pretty, additionally surfaces `pubChatUnreadCount` (unread
    Chat messages for your publication) as a bespoke highlighted line
    before the full JSON/panel render.
    """
    try:
        client = _make_client()
        result = get_unread_count(client)
        if pretty and isinstance(result, dict) and "pubChatUnreadCount" in result:
            _console.print(
                "[bold cyan]Publication Chat unread:[/bold cyan] "
                f"{result['pubChatUnreadCount']}"
            )
            output(result, pretty=False)  # JSON to stdout; Rich line already printed
        else:
            output(result, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        _emit_chat_error(exc, pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)
