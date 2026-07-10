"""Substack CLI — Substack Notes (create / list / get / delete).

Notes are Substack's micro-blogging surface. Internally they are backed by
the *comment* system, so the endpoints are named `comment`/`reader/feed`
even though the UI calls them Notes:

    CREATE  POST   /api/v1/comment/feed
    LIST    GET    /api/v1/reader/feed                    (personalized home)
            GET    /api/v1/reader/feed/profile/{user_id}  (a user's own notes)
    GET     GET    /api/v1/reader/feed/c-{comment_id}     (single note)
    DELETE  DELETE /api/v1/comment/{comment_id}

All Notes endpoints are served from the bare `substack.com` host (host "A"),
so — unlike drafts/publication commands — a publication URL is NOT required.

HARD LIMITATION — there is NO edit/update endpoint for Notes. Notes publish
immediately with no draft state and no undo. The only "update" is to delete
the note and create a new one (which gets a new id). This is a Substack API
limitation, not a CLI one.

Unlike newsletter drafts (whose `draft_body` is a *stringified* ProseMirror
document), a Note's `bodyJson` is sent as a nested JSON *object*.

Confidence: the endpoints/shapes here are reverse-engineered from the
unofficial API (community-verified via curl in 2026). See
`references/substack-api.md` § Notes.
"""
import json
import os
import re
from typing import Any, Optional

import typer

from substack_cli.app import notes_app
from substack_cli.auth import (
    AuthError,
    is_write_enabled,
    resolve_cookies,
    resolve_publication_url,
)
from substack_cli.client import (
    SUBSTACK_COM,
    SubstackApiError,
    SubstackClient,
    emit_error,
    output,
    output_list,
)
# Reuse the shared inline-Markdown parser (bold / italic / links). Notes use
# the same ProseMirror text-node + mark schema as newsletter posts.
from substack_cli.publish import _parse_inline

# Notes publish for everyone to reply by default. Other known values include
# "subscriber", "paid_subscriber", "founding". Passed through as-is.
DEFAULT_REPLY_MINIMUM_ROLE = "everyone"


# ---------------------------------------------------------------------------
# Body builder (plain text / minimal Markdown -> ProseMirror "doc")
# ---------------------------------------------------------------------------


def _text_to_note_doc(text: str) -> dict:
    """Build a Note `bodyJson` ProseMirror document from plain text.

    Blank lines split paragraphs; within a paragraph, inline **bold**,
    *italic*, and [text](url) are parsed via the shared `_parse_inline`
    helper. Raises ValueError on empty text.
    """
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    if not blocks:
        raise ValueError("Note text cannot be empty.")
    content = [
        {"type": "paragraph", "content": _parse_inline(block.replace("\n", " "))}
        for block in blocks
    ]
    # `attrs` mirrors the shape captured from the web app's create-note call.
    return {"type": "doc", "attrs": {"schemaVersion": "v1", "title": None}, "content": content}


def _load_body_json(body_json: str) -> dict:
    """Resolve a --body-json value: a file path or a raw JSON string.

    Accepts either a full `{"type": "doc", ...}` document, or an object that
    already wraps it under a `bodyJson` key (in which case the inner doc is
    used). Raises ValueError on malformed input.
    """
    if os.path.isfile(body_json):
        with open(body_json) as f:
            doc = json.load(f)
    else:
        try:
            doc = json.loads(body_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--body-json is neither a file nor valid JSON: {exc}") from exc
    if isinstance(doc, dict) and "bodyJson" in doc and isinstance(doc["bodyJson"], dict):
        doc = doc["bodyJson"]
    if not isinstance(doc, dict) or doc.get("type") != "doc":
        raise ValueError(
            "--body-json must be a ProseMirror document with top-level "
            '{"type": "doc", ...} (optionally wrapped under a "bodyJson" key).'
        )
    return doc


def _normalize_comment_id(raw: Any) -> int:
    """Accept a numeric note id, tolerating the `c-` entity_key prefix shown
    by `notes list` (e.g. "c-12345" -> 12345). Raises ValueError otherwise."""
    s = str(raw).strip()
    if s.lower().startswith("c-"):
        s = s[2:]
    if not s.isdigit():
        raise ValueError(
            f"Invalid note id: {raw!r}. Expected a numeric comment id "
            "(optionally 'c-' prefixed, as shown in `notes list` entity_key)."
        )
    return int(s)


# ---------------------------------------------------------------------------
# Core operations (pure functions — take a client, return API data)
# ---------------------------------------------------------------------------


def create_note(
    client: SubstackClient,
    *,
    text: Optional[str] = None,
    body_json: Optional[str] = None,
    reply_minimum_role: str = DEFAULT_REPLY_MINIMUM_ROLE,
) -> dict:
    """Publish a Note. `body_json` (a full ProseMirror doc) takes precedence
    over `text`. The `bodyJson` is sent as a nested object, NOT stringified.

    Requires SUBSTACK_ENABLE_WRITE=true (defense-in-depth — the command layer
    also gates)."""
    if not is_write_enabled():
        raise ValueError(
            "Write operations require SUBSTACK_ENABLE_WRITE=true "
            "(env var) or enable_write: true (config)"
        )
    if body_json is not None:
        doc = _load_body_json(body_json)
    elif text is not None:
        doc = _text_to_note_doc(text)
    else:
        raise ValueError("Provide note text or --body-json.")

    body = {"bodyJson": doc, "replyMinimumRole": reply_minimum_role}
    return client.post("/api/v1/comment/feed", host="A", json_body=body)


def _resolve_self_user_id(client: SubstackClient) -> int:
    """Look up the authenticated user's numeric id via /user/profile/self."""
    profile = client.get("/api/v1/user/profile/self", host="A")
    user_id = profile.get("id") if isinstance(profile, dict) else None
    if user_id is None:
        raise SubstackApiError(
            "Could not resolve your user id from /api/v1/user/profile/self "
            "(no 'id' field in the response)."
        )
    return user_id


def list_notes(
    client: SubstackClient,
    *,
    user_id: Optional[int] = None,
    mine: bool = False,
    limit: int = 25,
) -> Any:
    """List notes. Default: the caller's personalized home feed. When `mine`
    is set (or an explicit `user_id` is given), lists that profile's own
    published notes instead."""
    if mine and user_id is None:
        user_id = _resolve_self_user_id(client)
    if user_id is not None:
        return client.get(
            f"/api/v1/reader/feed/profile/{user_id}", host="A", limit=limit
        )
    return client.get("/api/v1/reader/feed", host="A", limit=limit)


def get_note(client: SubstackClient, comment_id: int) -> dict:
    """Get a single note by its numeric comment id."""
    return client.get(f"/api/v1/reader/feed/c-{comment_id}", host="A")


def delete_note(client: SubstackClient, comment_id: int) -> Any:
    """Delete a note by its numeric comment id. Requires
    SUBSTACK_ENABLE_WRITE=true (defense-in-depth)."""
    if not is_write_enabled():
        raise ValueError(
            "Write operations require SUBSTACK_ENABLE_WRITE=true "
            "(env var) or enable_write: true (config)"
        )
    return client.delete(f"/api/v1/comment/{comment_id}", host="A")


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def _make_client() -> SubstackClient:
    """Resolve auth and create a client for Notes operations.

    Notes endpoints all use host "A" (substack.com), so a publication URL is
    NOT required — fall back to substack.com when none is configured. This
    lets accounts without a publication still post/read/delete notes.
    """
    cookies = resolve_cookies()
    try:
        pub_url = resolve_publication_url()
    except AuthError:
        pub_url = SUBSTACK_COM
    return SubstackClient(cookies=cookies, publication_url=pub_url)


@notes_app.command("create")
def notes_create_cmd(
    text: str = typer.Argument(
        None,
        help="Note text. Blank lines split paragraphs; supports **bold**, "
        "*italic*, [text](url). Omit when using --body-json.",
    ),
    body_json: str = typer.Option(
        None,
        "--body-json",
        help="Path to a JSON file or a raw ProseMirror bodyJson document. "
        "Overrides TEXT — use for rich content (mentions, images, etc.).",
    ),
    reply_min_role: str = typer.Option(
        DEFAULT_REPLY_MINIMUM_ROLE,
        "--reply-min-role",
        help="Who may reply: everyone | subscriber | paid_subscriber | founding.",
    ),
    yes: bool = typer.Option(
        False, "--yes", help="Confirm publishing (notes are immediate + uneditable)."
    ),
    pretty: bool = False,
):
    """Publish a Note.

    Notes publish IMMEDIATELY to your public feed and CANNOT be edited (only
    deleted) — so this command requires --yes, like `drafts publish`.
    """
    if text is None and body_json is None:
        emit_error("Provide note text or --body-json.", pretty=pretty)
    if not yes:
        emit_error(
            "Refusing to publish note without --yes. Notes publish immediately "
            "to your public feed and cannot be edited (only deleted). "
            "Re-run with --yes to confirm.",
            pretty=pretty,
        )
    if not is_write_enabled():
        emit_error(
            "Write operations require SUBSTACK_ENABLE_WRITE=true "
            "(env var) or enable_write: true (config)",
            pretty=pretty,
        )
    try:
        client = _make_client()
        result = create_note(
            client, text=text, body_json=body_json, reply_minimum_role=reply_min_role
        )
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@notes_app.command("list")
def notes_list_cmd(
    mine: bool = typer.Option(
        False, "--mine", help="List YOUR published notes (resolves your user id)."
    ),
    user_id: int = typer.Option(
        None, "--user-id", help="List a specific user's published notes."
    ),
    limit: int = typer.Option(25, "--limit", help="Max items to return."),
    pretty: bool = False,
):
    """List notes.

    Default: your personalized Notes home feed (mixes notes and posts).
    --mine: only your own published notes (best for finding ids to delete).
    Note entity_keys are prefixed `c-` (posts are `p-`).
    """
    try:
        client = _make_client()
        result = list_notes(client, user_id=user_id, mine=mine, limit=limit)
        output_list(result, pretty=pretty, title="Notes")
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@notes_app.command("get")
def notes_get_cmd(comment_id: str, pretty: bool = False):
    """Get a single note by id (accepts 123 or c-123)."""
    try:
        cid = _normalize_comment_id(comment_id)
        client = _make_client()
        result = get_note(client, cid)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@notes_app.command("delete")
def notes_delete_cmd(comment_id: str, yes: bool = False, pretty: bool = False):
    """Delete one of your notes by id (accepts 123 or c-123). Requires --yes."""
    if not yes:
        emit_error(
            f"Refusing to delete note {comment_id} without --yes. "
            "Re-run with --yes to confirm.",
            pretty=pretty,
        )
    if not is_write_enabled():
        emit_error(
            "Write operations require SUBSTACK_ENABLE_WRITE=true "
            "(env var) or enable_write: true (config)",
            pretty=pretty,
        )
    try:
        cid = _normalize_comment_id(comment_id)
        client = _make_client()
        result = delete_note(client, cid)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)
