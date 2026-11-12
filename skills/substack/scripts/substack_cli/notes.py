"""Substack CLI — Substack Notes (create / list / get / delete / replies).

Notes are Substack's micro-blogging surface. Internally they are backed by
the *comment* system, so the endpoints are named `comment`/`reader/feed`
even though the UI calls them Notes:

    CREATE  POST   /api/v1/comment/feed
    IMAGE   POST   /api/v1/image                         (upload -> CDN url)
            POST   /api/v1/comment/attachment            ({url, type:"image"} -> {id})
                   then CREATE with attachmentIds: [id, ...]
    DRAFT   POST   /api/v1/comment/draft                  (saved, NOT published;
                                                           add trigger_at to schedule)
    DRAFTS  GET    /api/v1/feed/drafts                    (saved + scheduled drafts)
    REPLY   POST   /api/v1/comment/feed                   (body adds parent_id)
    LIST    GET    /api/v1/reader/feed                    (personalized home)
            GET    /api/v1/reader/feed/profile/{user_id}  (a user's own notes)
            GET    /api/v1/notes?author_id=<id>&cursor=   (paginated author notes,
                                                           used by --all)
    GET     GET    /api/v1/reader/comment/{comment_id}    (single note -> {item})
    REPLIES GET    /api/v1/reader/comment/{id}/replies    (note reply tree)
    DELETE  DELETE /api/v1/comment/{comment_id}

All Notes endpoints are served from the bare `substack.com` host (host "A"),
so — unlike drafts/publication commands — a publication URL is NOT required.

HARD LIMITATION — there is NO edit/update endpoint for PUBLISHED Notes. A
published note has no undo; the only "update" is to delete it and create a new
one (which gets a new id). To review before anything goes public, save it as a
draft (--draft) or schedule it (--schedule): both land in the Drafts tab of
the Substack Notes composer, where they can be edited, published, or deleted.

Unlike newsletter drafts (whose draft_body is a *stringified* ProseMirror
document), a Note's bodyJson is sent as a nested JSON *object*.

Confidence: the endpoints/shapes here are reverse-engineered from the
unofficial API (community-verified via curl in 2026). See
references/substack-api.md § Notes.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, List, Optional

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
from substack_cli.publish import _parse_inline, upload_image

# Notes publish for everyone to reply by default.
# Live-verified values: "everyone", "paid_subscriber".
# "subscriber" was rejected by the API with 400 "Invalid value" (Oct 2026).
# "founding" has never been tested against the live API.
DEFAULT_REPLY_MINIMUM_ROLE = "everyone"

# Only these values have been confirmed or are safe to pass through.
# "subscriber" was rejected by the API (issue #31). "founding" is unverified.
REPLY_MINIMUM_ROLE_CHOICES = ("everyone", "paid_subscriber")


def _validate_reply_minimum_role(value: str) -> str:
    """Validate --reply-min-role against the known-good set.

    Raises ValueError with a descriptive message if the value is not in the
    live-verified set. This prevents silent 400s from the API.
    """
    if value not in REPLY_MINIMUM_ROLE_CHOICES:
        raise ValueError(
            f"--reply-min-role {value!r} is not a verified API value. "
            f"Accepted values: {', '.join(REPLY_MINIMUM_ROLE_CHOICES)}. "
            "('subscriber' was rejected by the API with 400 in Oct 2026; "
            "'founding' has never been verified against the live API.)"
        )
    return value

# Safety caps for --descendants recursive fetching
_DESCENDANTS_MAX_DEPTH = 5
_DESCENDANTS_MAX_REQUESTS = 200


def _unwrap_comment(entry: dict) -> dict:
    """Normalize a descendantComments entry to a bare comment dict.

    The /api/v1/reader/comment/{id}/replies endpoint returns descendantComments
    entries wrapped as {"type": "comment", "comment": {...fields...}}.
    Some callers may pass already-bare dicts (e.g. from a previous normalisation
    pass).  This function handles both shapes and always returns the bare dict.
    """
    if entry.get("type") == "comment" and "comment" in entry and "id" not in entry:
        return entry["comment"]
    return entry


def _normalize_descendants(entries: list) -> list:
    """Unwrap all entries, dedupe by id preserving first-seen order."""
    seen: dict = {}
    for raw in entries:
        bare = _unwrap_comment(raw)
        cid = bare.get("id")
        if cid is not None and cid not in seen:
            seen[cid] = bare
        elif cid is None:
            # No id — include as-is to avoid silent data loss
            seen[id(bare)] = bare
    return list(seen.values())


# ---------------------------------------------------------------------------
# Body builder
# ---------------------------------------------------------------------------

# Marks that the Notes API has been confirmed to accept.
# `strong` and `em` caused 500s on POST /api/v1/comment/draft (issue #31,
# Oct 2026). Only `link` is confirmed safe from observed live Note drafts.
_NOTE_ALLOWED_MARKS = frozenset({"link"})


def _strip_unsupported_note_marks(nodes: list, *, _warned: set | None = None) -> list:
    """Remove ProseMirror marks that the Notes API does not accept.

    `strong` (`**bold**`) and `em` (`*italic*`) caused HTTP 500 on the
    Notes draft endpoint (issue #31, Oct 2026). Only `link` marks are
    confirmed safe. Unsupported marks are stripped and their text is kept as
    plain text; a warning is printed to stderr for each mark type removed.

    Recurses into any node that has a ``content`` list so marks are stripped
    at all depths (e.g. inside list items, blockquotes, nested paragraphs).
    """
    if _warned is None:
        _warned = set()
    result = []
    for node in nodes:
        node_type = node.get("type")
        if node_type == "text":
            marks = node.get("marks", [])
            if marks:
                kept = [m for m in marks if m.get("type") in _NOTE_ALLOWED_MARKS]
                stripped = [m["type"] for m in marks if m.get("type") not in _NOTE_ALLOWED_MARKS]
                for mark_type in stripped:
                    if mark_type not in _warned:
                        print(
                            f"Warning: '{mark_type}' marks are not supported by the Notes API "
                            "and have been removed. Use plain text or --body-json for rich formatting.",
                            file=sys.stderr,
                        )
                        _warned.add(mark_type)
                new_node = {"type": "text", "text": node["text"]}
                if kept:
                    new_node["marks"] = kept
                result.append(new_node)
            else:
                result.append(node)
        else:
            # Recurse into any node that carries a content list (paragraphs,
            # list items, blockquotes, etc.) so marks are stripped at all depths.
            if "content" in node and isinstance(node["content"], list):
                node = dict(node)
                node["content"] = _strip_unsupported_note_marks(
                    node["content"], _warned=_warned
                )
            result.append(node)
    return result


def _strip_unsupported_marks_from_doc(doc: dict) -> dict:
    """Apply _strip_unsupported_note_marks to every content node in a doc.

    Returns a shallow copy of *doc* with its ``content`` list recursively
    stripped. Safe to call on docs produced by either _text_to_note_doc or
    _load_body_json.
    """
    if not isinstance(doc.get("content"), list):
        return doc
    return {**doc, "content": _strip_unsupported_note_marks(doc["content"])}


def _text_to_note_doc(text: str) -> dict:
    """Build a Note `bodyJson` ProseMirror document from plain text.

    Blank lines split paragraphs; within a paragraph, [text](url) links are
    parsed via the shared `_parse_inline` helper. **bold** and *italic* marks
    are stripped with a warning — the Notes API rejects `strong`/`em` marks
    (500 on POST /api/v1/comment/draft, issue #31). Raises ValueError on
    empty text.
    """
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    if not blocks:
        raise ValueError("Note text cannot be empty.")
    content = [
        {
            "type": "paragraph",
            "content": _strip_unsupported_note_marks(
                _parse_inline(block.replace("\n", " "))
            ),
        }
        for block in blocks
    ]
    return {"type": "doc", "attrs": {"schemaVersion": "v1", "title": None}, "content": content}


def _load_body_json(body_json: str) -> dict:
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
    s = str(raw).strip()
    if s.lower().startswith("c-"):
        s = s[2:]
    if not s.isdigit():
        raise ValueError(
            f"Invalid note id: {raw!r}. Expected a numeric comment id "
            "(optionally 'c-' prefixed, as shown in `notes list` entity_key)."
        )
    return int(s)


def _validate_image_paths(image_paths: Optional[List[str]]) -> List[str]:
    paths = list(image_paths or [])
    for p in paths:
        if not os.path.isfile(p):
            raise ValueError(f"Image file not found: {p}")
    return paths


def _parse_schedule(value: str, *, now: Optional[datetime] = None) -> str:
    raw = value.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(
            f"Invalid --schedule time {value!r}. Use ISO-8601, e.g. "
            "2026-10-12T09:00 (local time) or 2026-10-12T13:00Z (UTC)."
        ) from exc
    if dt.tzinfo is None:
        dt = dt.astimezone()
    dt = dt.astimezone(timezone.utc)
    if dt <= (now or datetime.now(timezone.utc)):
        raise ValueError(f"--schedule time {value!r} is not in the future.")
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _parse_since(value: str) -> datetime:
    """Parse an ISO-8601 date or datetime into an aware UTC datetime.

    Accepts "2026-10-01", "2026-10-01T12:00:00", "...Z", "...-04:00".
    A naive value (no offset) is treated as UTC. Raises ValueError on bad input.
    """
    raw = value.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(
            f"Invalid --since value {value!r}. Use ISO-8601, e.g. "
            "2026-10-01 or 2026-10-01T12:00:00Z."
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def _validate_url(url: str) -> None:
    """Raise ValueError if *url* is not an http/https URL."""
    if not url.startswith(("http://", "https://")):
        raise ValueError(
            f"Invalid URL {url!r}: --link requires an http or https URL."
        )


def create_note_link_attachment(client: SubstackClient, url: str) -> str:
    """Register a link (URL) as a Note attachment and return its attachment id.

    Sends POST /api/v1/comment/attachment on host "A" with
    ``{"url": "<url>", "type": "link"}``.  For Substack post URLs the API
    returns ``{"id": "<uuid>", "type": "post", "post": {...}}``.  For
    non-Substack URLs the type is ``"link"`` but the ``id`` field is present
    in both cases.

    The returned id is passed in the note's ``attachmentIds`` list alongside
    any image attachment ids.  All link attachments are created BEFORE the
    note is posted, preserving the fail-before-publish property.

    Ordering when both images and links are provided: image attachment ids
    come first (in the order their ``--image`` flags appear), then link
    attachment ids (in the order their ``--link`` flags appear).  Substack
    renders the first attachment as the primary card, so callers who want a
    link card to appear first should not mix it with images.

    Raises SubstackApiError if the response contains no ``id`` field.
    Raises ValueError for non-http/https URLs (validated before any network
    call).
    """
    _validate_url(url)
    attachment = client.post(
        "/api/v1/comment/attachment",
        host="A",
        json_body={"url": url, "type": "link"},
    )
    attachment_id = attachment.get("id") if isinstance(attachment, dict) else None
    if not attachment_id:
        raise SubstackApiError(
            f"Creating a link attachment for {url!r} returned no 'id' field."
        )
    return attachment_id


def create_note_image_attachment(client: SubstackClient, image_path: str) -> str:
    uploaded = upload_image(client, image_path, host="A")
    url = uploaded.get("url") if isinstance(uploaded, dict) else None
    if not url:
        raise SubstackApiError(f"Image upload for {image_path} returned no 'url' field.")
    attachment = client.post(
        "/api/v1/comment/attachment",
        host="A",
        json_body={"url": url, "type": "image"},
    )
    attachment_id = attachment.get("id") if isinstance(attachment, dict) else None
    if not attachment_id:
        raise SubstackApiError(
            f"Creating the note attachment for {image_path} returned no 'id' field."
        )
    return attachment_id


def create_note(
    client: SubstackClient,
    *,
    text: Optional[str] = None,
    body_json: Optional[str] = None,
    reply_minimum_role: str = DEFAULT_REPLY_MINIMUM_ROLE,
    image_paths: Optional[List[str]] = None,
    link_urls: Optional[List[str]] = None,
    draft: bool = False,
    trigger_at: Optional[str] = None,
) -> dict:
    """Publish a Note, or save it as a draft.

    `draft=True` saves to the Notes Drafts tab via POST /api/v1/comment/draft
    instead of publishing. `trigger_at` (Substack's UTC format, see
    `_parse_schedule`) schedules it: it is saved as a draft that Substack
    publishes automatically at that time. A trigger_at implies draft.
 `body_json` (a full ProseMirror doc) takes precedence
    over `text`. The `bodyJson` is sent as a nested object, NOT stringified.

    `image_paths` (local files) are uploaded and attached via
    `attachmentIds`. `link_urls` (http/https URLs) are registered as link
    attachments. All attachments are created BEFORE the note is posted, so a
    failed attachment call never leaves a half-published note.

    Ordering: image attachment ids come first (in ``--image`` flag order),
    then link attachment ids (in ``--link`` flag order).

    Requires SUBSTACK_ENABLE_WRITE=true (defense-in-depth — the command layer
    also gates)."""
    if not is_write_enabled():
        raise ValueError(
            "Write operations require SUBSTACK_ENABLE_WRITE=true "
            "(env var) or enable_write: true (config)"
        )
    _validate_reply_minimum_role(reply_minimum_role)
    if body_json is not None:
        doc = _strip_unsupported_marks_from_doc(_load_body_json(body_json))
    elif text is not None:
        doc = _text_to_note_doc(text)
    else:
        raise ValueError("Provide note text or --body-json.")
    paths = _validate_image_paths(image_paths)
    urls = list(link_urls or [])
    for u in urls:
        _validate_url(u)  # fail fast before any network call

    attachment_ids = [create_note_image_attachment(client, p) for p in paths]
    attachment_ids += [create_note_link_attachment(client, u) for u in urls]

    body: dict = {"bodyJson": doc, "replyMinimumRole": reply_minimum_role}
    if attachment_ids:
        body["attachmentIds"] = attachment_ids
    if draft or trigger_at:
        body.update({"tabId": "for-you", "surface": "feed"})
        if trigger_at:
            body["trigger_at"] = trigger_at
        return client.post("/api/v1/comment/draft", host="A", json_body=body)
    return client.post("/api/v1/comment/feed", host="A", json_body=body)


def list_draft_notes(client: SubstackClient, *, limit: int = 20) -> Any:
    return client.get("/api/v1/feed/drafts", host="A", limit=limit)


def reply_to_note(
    client: SubstackClient,
    parent_id: int,
    *,
    text: Optional[str] = None,
    body_json: Optional[str] = None,
    reply_minimum_role: str = DEFAULT_REPLY_MINIMUM_ROLE,
) -> dict:
    if not is_write_enabled():
        raise ValueError(
            "Write operations require SUBSTACK_ENABLE_WRITE=true "
            "(env var) or enable_write: true (config)"
        )
    _validate_reply_minimum_role(reply_minimum_role)
    if body_json is not None:
        doc = _strip_unsupported_marks_from_doc(_load_body_json(body_json))
    elif text is not None:
        doc = _text_to_note_doc(text)
    else:
        raise ValueError("Provide reply text or --body-json.")
    body = {
        "bodyJson": doc,
        "parent_id": parent_id,
        "tabId": "for-you",
        "surface": "feed",
        "replyMinimumRole": reply_minimum_role,
    }
    return client.post("/api/v1/comment/feed", host="A", json_body=body)


def _resolve_self_user_id(client: SubstackClient) -> int:
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
    fetch_all: bool = False,
    since: Optional[datetime] = None,
    max_pages: int = 50,
) -> Any:
    """List notes.

    Without --all: single request, behaviour identical to the old code.
    With fetch_all=True: paginates GET /api/v1/notes?author_id=<id>
    until nextCursor is null, items are older than `since`, or max_pages
    is reached. Returns {items, pages, nextCursor}.
    """
    if fetch_all:
        if user_id is None and not mine:
            raise ValueError("--all requires --mine or --user-id")
        if mine and user_id is None:
            user_id = _resolve_self_user_id(client)
        all_items: List[Any] = []
        cursor: Optional[str] = None
        pages = 0
        last_cursor: Optional[str] = None
        while pages < max_pages:
            params: dict = {"limit": limit}
            if cursor:
                params["cursor"] = cursor
            page = client.get(f"/api/v1/reader/feed/profile/{user_id}", host="A", **params)
            pages += 1
            items = page.get("items", []) if isinstance(page, dict) else []
            next_cursor = page.get("nextCursor") if isinstance(page, dict) else None
            if since is not None and items:
                filtered: List[Any] = []
                stop = False
                for item in items:
                    comment = item.get("comment", {}) if isinstance(item, dict) else {}
                    date_str = comment.get("date") if isinstance(comment, dict) else None
                    if date_str:
                        try:
                            item_dt = datetime.fromisoformat(
                                date_str.replace("Z", "+00:00")
                            ).astimezone(timezone.utc)
                            if item_dt < since:
                                stop = True
                                continue
                        except (ValueError, TypeError):
                            pass
                    filtered.append(item)
                all_items.extend(filtered)
                if stop:
                    next_cursor = None
            else:
                all_items.extend(items)
            last_cursor = next_cursor
            if not next_cursor:
                break
            cursor = next_cursor
        return {"items": all_items, "pages": pages, "nextCursor": last_cursor}

    # --- original single-page behaviour (unchanged) ---
    if mine and user_id is None:
        user_id = _resolve_self_user_id(client)
    if user_id is not None:
        return client.get(f"/api/v1/reader/feed/profile/{user_id}", host="A", limit=limit)
    return client.get("/api/v1/reader/feed", host="A", limit=limit)


def get_note(client: SubstackClient, comment_id: int) -> dict:
    return client.get(f"/api/v1/reader/comment/{comment_id}", host="A")


def delete_note(client: SubstackClient, comment_id: int) -> Any:
    if not is_write_enabled():
        raise ValueError(
            "Write operations require SUBSTACK_ENABLE_WRITE=true "
            "(env var) or enable_write: true (config)"
        )
    return client.delete(f"/api/v1/comment/{comment_id}", host="A")


def get_note_replies(
    client: SubstackClient,
    comment_id: int,
    *,
    cursor: Optional[str] = None,
    fetch_all: bool = False,
    fetch_descendants: bool = False,
    _depth: int = 0,
    _request_counter: Optional[List[int]] = None,
) -> dict:
    """Fetch replies for a Note via GET /api/v1/reader/comment/{id}/replies.

    Single-page (default): returns the raw API object:
      {rootComment, commentBranches:[{comment, descendantComments}],
       moreBranches, nextCursor, automodHiddenBranches}

    --all: follows nextCursor until null; returns synthetic envelope:
      {rootComment, commentBranches:<all>, automodHiddenBranches:<all>,
       moreBranches:0, nextCursor:null, pages:N}

    --descendants: for every branch (in commentBranches and
    automodHiddenBranches) whose comment.children_count > len(descendantComments),
    recursively fetches that reply's own /replies (all pages) and sets
    descendantComments to the depth-first flattened list of ALL descendants.
    Capped at depth _DESCENDANTS_MAX_DEPTH (5) and _DESCENDANTS_MAX_REQUESTS
    (200) extra requests total; if either cap is hit adds "truncated": true.
    --descendants implies --all.
    """
    if _request_counter is None:
        _request_counter = [0]

    def _fetch_one_page(cid: int, cur: Optional[str]) -> dict:
        params: dict = {}
        if cur:
            params["cursor"] = cur
        _request_counter[0] += 1
        return client.get(f"/api/v1/reader/comment/{cid}/replies", host="A", **params)

    do_all = fetch_all or fetch_descendants

    if do_all:
        first = _fetch_one_page(comment_id, cursor)
        root = first.get("rootComment")
        all_branches: List[dict] = list(first.get("commentBranches") or [])
        all_automod: List[dict] = list(first.get("automodHiddenBranches") or [])
        pages = 1
        next_cur = first.get("nextCursor")
        while next_cur:
            page = _fetch_one_page(comment_id, next_cur)
            all_branches.extend(page.get("commentBranches") or [])
            all_automod.extend(page.get("automodHiddenBranches") or [])
            pages += 1
            next_cur = page.get("nextCursor")
        result: dict = {
            "rootComment": root,
            "commentBranches": all_branches,
            "automodHiddenBranches": all_automod,
            "moreBranches": 0,
            "nextCursor": None,
            "pages": pages,
        }
    else:
        result = _fetch_one_page(comment_id, cursor)

    if fetch_descendants and _depth < _DESCENDANTS_MAX_DEPTH:
        truncated = False
        cb: List[dict] = result.get("commentBranches") or []
        ah: List[dict] = result.get("automodHiddenBranches") or []

        def _expand_branches(branches: List[dict]) -> None:
            nonlocal truncated
            for branch in branches:
                comment = branch.get("comment") or {}
                children_count = comment.get("children_count", 0) or 0
                # Normalise inline descendantComments to bare dicts now
                # so the children_count comparison is meaningful and the output
                # is uniform whether or not we recurse.
                existing = _normalize_descendants(
                    branch.get("descendantComments") or []
                )
                branch["descendantComments"] = existing
                # children_count = direct children of this comment.
                # existing is a flat list of ALL descendants (direct + deeper),
                # so existing may be longer than children_count even when complete.
                # Correct trigger: recurse when children_count > 0 and we have
                # fewer entries than children_count (meaning at least one direct
                # child is missing entirely from the inline list).
                if children_count > 0 and len(existing) < children_count:
                    if _request_counter[0] >= _DESCENDANTS_MAX_REQUESTS:
                        truncated = True
                        continue
                    cid = comment.get("id")
                    if not cid:
                        continue
                    sub = get_note_replies(
                        client,
                        cid,
                        fetch_all=True,
                        fetch_descendants=True,
                        _depth=_depth + 1,
                        _request_counter=_request_counter,
                    )
                    if sub.get("truncated"):
                        truncated = True
                    # Flatten depth-first: branch comment -> its descendants.
                    # sub_branch["comment"] is already a bare dict (the branch
                    # wrapper uses {"comment":{...}} not the wrapped API shape).
                    # sub_branch["descendantComments"] entries come from the API
                    # and may be wrapped as {type,comment}; normalise them.
                    flat: List[dict] = []
                    for sub_branch in (sub.get("commentBranches") or []) + (
                        sub.get("automodHiddenBranches") or []
                    ):
                        sub_comment = sub_branch.get("comment") or {}
                        if sub_comment.get("id") is not None:
                            flat.append(sub_comment)
                        flat.extend(
                            _normalize_descendants(
                                sub_branch.get("descendantComments") or []
                            )
                        )
                    branch["descendantComments"] = _normalize_descendants(
                        # Also normalise whatever inline entries already existed
                        # before we overwrote them, then fold in fetched ones.
                        flat
                    )

        _expand_branches(cb)
        _expand_branches(ah)
        result["commentBranches"] = cb
        result["automodHiddenBranches"] = ah
        if truncated:
            result["truncated"] = True

    elif fetch_descendants and _depth >= _DESCENDANTS_MAX_DEPTH:
        result["truncated"] = True

    return result


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def _make_client() -> SubstackClient:
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
        help="Note text. Blank lines split paragraphs; supports [text](url) links. "
        "Bold/italic are stripped with a warning (not supported by the Notes API). "
        "Omit when using --body-json.",
    ),
    body_json: str = typer.Option(
        None,
        "--body-json",
        help="Path to a JSON file or a raw ProseMirror bodyJson document. "
        "Overrides TEXT — use for rich content (mentions, images, etc.).",
    ),
    image: Optional[List[str]] = typer.Option(
        None,
        "--image",
        help="Local image file to attach (png/jpg/gif/webp). Repeat the flag "
        "to attach several. Uploaded before the note is posted.",
    ),
    link: Optional[List[str]] = typer.Option(
        None,
        "--link",
        help="URL to attach as a link/post card. Repeat the flag to attach "
        "several. For Substack post URLs this renders as a post card; for "
        "other URLs it renders as a link preview. Registered before the note "
        "is posted. Image attachments appear first, then link attachments.",
    ),
    reply_min_role: str = typer.Option(
        DEFAULT_REPLY_MINIMUM_ROLE,
        "--reply-min-role",
        help="Who may reply: everyone | paid_subscriber. "
        "('subscriber' was rejected by the API in Oct 2026; 'founding' is unverified.)",
    ),
    draft: bool = typer.Option(
        False,
        "--draft",
        help="Save to your Notes Drafts instead of publishing.",
    ),
    schedule: str = typer.Option(
        None,
        "--schedule",
        help="Schedule for a future time (ISO-8601). Requires --yes.",
    ),
    yes: bool = typer.Option(False, "--yes", help="Confirm publishing."),
    pretty: bool = False,
):
    """Publish a Note, save as draft (--draft), or schedule (--schedule).

    Published notes go out IMMEDIATELY and CANNOT be edited (only deleted), so
    publishing requires --yes. --draft saves privately to the Drafts tab of the
    Notes composer (no --yes needed). --schedule auto-publishes later, so it
    also requires --yes. Attach images with --image; attach link/post cards
    with --link (repeat either flag for multiple attachments).
    """
    if text is None and body_json is None:
        emit_error("Provide note text or --body-json.", pretty=pretty)
    trigger_at = None
    if schedule is not None:
        try:
            trigger_at = _parse_schedule(schedule)
        except ValueError as exc:
            emit_error(str(exc), pretty=pretty)
    if schedule is not None and not yes:
        emit_error(
            "Refusing to schedule note without --yes. A scheduled note "
            "publishes automatically at that time. Re-run with --yes to confirm, "
            "or use --draft alone to save it without a publish time.",
            pretty=pretty,
        )
    if not yes and not draft:
        emit_error(
            "Refusing to publish note without --yes. Notes publish immediately "
            "to your public feed and cannot be edited (only deleted). "
            "Re-run with --yes to confirm, or use --draft to save it unpublished.",
            pretty=pretty,
        )
    if not is_write_enabled():
        emit_error(
            "Write operations require SUBSTACK_ENABLE_WRITE=true "
            "(env var) or enable_write: true (config)",
            pretty=pretty,
        )
    try:
        _validate_image_paths(image)  # fail fast, before auth or any HTTP
        for u in list(link or []):
            _validate_url(u)  # fail fast, before auth or any HTTP
        client = _make_client()
        result = create_note(
            client,
            text=text,
            body_json=body_json,
            reply_minimum_role=reply_min_role,
            image_paths=image,
            link_urls=link,
            draft=draft,
            trigger_at=trigger_at,
        )
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@notes_app.command("reply")
def notes_reply_cmd(
    parent_id: str = typer.Argument(
        ..., help="Parent note/comment id to reply to (accepts 123 or c-123)."
    ),
    text: str = typer.Argument(
        None,
        help="Reply text. Blank lines split paragraphs; supports [text](url) links. "
        "Bold/italic are stripped with a warning (not supported by the Notes API). "
        "Omit when using --body-json.",
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
        help="Who may reply: everyone | paid_subscriber. "
        "('subscriber' was rejected by the API in Oct 2026; 'founding' is unverified.)",
    ),
    yes: bool = typer.Option(
        False, "--yes", help="Confirm publishing (notes are immediate + uneditable)."
    ),
    pretty: bool = False,
):
    """Reply to a Note. Requires --yes (publishes immediately)."""
    if text is None and body_json is None:
        emit_error("Provide reply text or --body-json.", pretty=pretty)
    if not yes:
        emit_error(
            "Refusing to publish reply without --yes. Notes publish immediately "
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
        pid = _normalize_comment_id(parent_id)
        client = _make_client()
        result = reply_to_note(
            client, pid, text=text, body_json=body_json, reply_minimum_role=reply_min_role
        )
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@notes_app.command("list")
def notes_list_cmd(
    mine: bool = typer.Option(False, "--mine", help="List YOUR published notes."),
    user_id: int = typer.Option(None, "--user-id", help="List a specific user's notes."),
    limit: int = typer.Option(25, "--limit", help="Max items per page."),
    fetch_all: bool = typer.Option(
        False, "--all",
        help="Paginate all pages (requires --mine or --user-id). "
        "Returns {items, pages, nextCursor}.",
    ),
    since: str = typer.Option(
        None, "--since",
        help="Stop when items are older than this ISO-8601 date/datetime. Used with --all.",
    ),
    max_pages: int = typer.Option(50, "--max-pages", help="Max pages to fetch with --all."),
    pretty: bool = False,
):
    """List notes. --all paginates GET /api/v1/notes?author_id=<id>&cursor=…"""
    since_dt: Optional[datetime] = None
    if since is not None:
        try:
            since_dt = _parse_since(since)
        except ValueError as exc:
            emit_error(str(exc), pretty=pretty)
    try:
        client = _make_client()
        result = list_notes(
            client,
            user_id=user_id,
            mine=mine,
            limit=limit,
            fetch_all=fetch_all,
            since=since_dt,
            max_pages=max_pages,
        )
        if fetch_all:
            output(result, pretty=pretty)
        else:
            output_list(result, pretty=pretty, title="Notes")
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@notes_app.command("drafts")
def notes_drafts_cmd(
    limit: int = typer.Option(20, "--limit", help="Max drafts to return."),
    pretty: bool = False,
):
    """List your saved and scheduled Note drafts."""
    try:
        client = _make_client()
        result = list_draft_notes(client, limit=limit)
        if isinstance(result, dict) and "drafts" in result:
            result = result["drafts"]
        output_list(result, pretty=pretty, title="Note drafts")
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
    """Delete one of your notes by id. Requires --yes."""
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


@notes_app.command("replies")
def notes_replies_cmd(
    note_id: str = typer.Argument(
        ..., help="Note id whose replies to fetch (accepts 123 or c-123)."
    ),
    fetch_all: bool = typer.Option(
        False, "--all",
        help="Follow nextCursor until null; returns merged envelope "
        "{rootComment, commentBranches, automodHiddenBranches, moreBranches:0, "
        "nextCursor:null, pages:N}.",
    ),
    descendants: bool = typer.Option(
        False, "--descendants",
        help="Recursively fetch nested replies (implies --all). "
        "Capped at depth 5 / 200 extra requests; adds 'truncated':true if hit.",
    ),
    cursor: str = typer.Option(None, "--cursor", help="Start from a specific cursor."),
    pretty: bool = False,
):
    """Fetch replies for a Note.

    Calls GET https://substack.com/api/v1/reader/comment/{id}/replies.

    Default (one page): raw API object
      {rootComment, commentBranches, moreBranches, nextCursor, automodHiddenBranches}.

    --all: follow pagination; returns merged envelope.

    --descendants: recursively expand branches whose children_count exceeds
    their returned descendantComments, replacing descendantComments with the
    full depth-first flattened list. Implies --all.
    """
    try:
        cid = _normalize_comment_id(note_id)
        client = _make_client()
        result = get_note_replies(
            client,
            cid,
            cursor=cursor,
            fetch_all=fetch_all,
            fetch_descendants=descendants,
        )
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)
