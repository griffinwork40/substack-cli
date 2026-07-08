"""Substack CLI — draft CRUD, publish lifecycle, image upload, MD→ProseMirror."""
import base64
import json
import os
import re
from typing import Any, Optional

import typer

from substack_cli.app import app, drafts_app
from substack_cli.auth import (
    AuthError,
    is_write_enabled,
    resolve_cookies,
    resolve_publication_url,
)
from substack_cli.client import (
    SubstackApiError,
    SubstackClient,
    emit_error,
    output,
    output_list,
)
from substack_cli.models import extract_list


# ---------------------------------------------------------------------------
# Markdown → ProseMirror converter (minimal — paragraphs, headings, bold,
# italic, links only; raises NotImplementedError for anything else)
# ---------------------------------------------------------------------------


def _parse_inline(text: str) -> list:
    """Parse inline markdown (bold, italic, links) into ProseMirror text nodes."""
    nodes: list = []
    # Combined regex: **bold**, *italic*, [text](url)
    pattern = re.compile(
        r"\*\*(.+?)\*\*"  # bold
        r"|\*(.+?)\*"  # italic
        r"|\[(.+?)\]\((.+?)\)",  # link
    )
    pos = 0
    for m in pattern.finditer(text):
        # Text before the match
        if m.start() > pos:
            nodes.append({"type": "text", "text": text[pos : m.start()]})
        if m.group(1) is not None:  # bold
            nodes.append(
                {"type": "text", "text": m.group(1), "marks": [{"type": "strong"}]}
            )
        elif m.group(2) is not None:  # italic
            nodes.append(
                {"type": "text", "text": m.group(2), "marks": [{"type": "em"}]}
            )
        elif m.group(3) is not None:  # link
            nodes.append(
                {
                    "type": "text",
                    "text": m.group(3),
                    "marks": [{"type": "link", "attrs": {"href": m.group(4)}}],
                }
            )
        pos = m.end()
    # Trailing text
    if pos < len(text):
        nodes.append({"type": "text", "text": text[pos:]})
    return nodes if nodes else [{"type": "text", "text": text}]


def _markdown_to_prosemirror_doc(markdown_text: str) -> dict:
    """Convert minimal Markdown to a ProseMirror document.

    Supports ONLY: blank-line-separated paragraphs, #/##/### headings,
    **bold**, *italic*, [text](url) links. Raises NotImplementedError
    with a message pointing callers at --body-json for anything else
    (lists, images, footnotes, paywall markers, embeds — all out of scope
    for v1).
    """
    lines = markdown_text.split("\n")
    content: list = []
    current_paragraph: list = []

    for line in lines:
        stripped = line.strip()

        # Empty line — flush current paragraph
        if not stripped:
            if current_paragraph:
                content.append(
                    {"type": "paragraph", "content": current_paragraph}
                )
                current_paragraph = []
            continue

        # Heading detection
        heading_match = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading_match:
            # Flush paragraph first
            if current_paragraph:
                content.append(
                    {"type": "paragraph", "content": current_paragraph}
                )
                current_paragraph = []
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2)
            content.append(
                {
                    "type": "heading",
                    "attrs": {"level": level},
                    "content": _parse_inline(heading_text),
                }
            )
            continue

        # List detection — unsupported
        if re.match(r"^[-*+]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
            raise NotImplementedError(
                "Lists are not supported by the minimal Markdown converter. "
                "Use --body-json with a full ProseMirror document for "
                "lists, images, footnotes, paywall markers, or embeds."
            )

        # Image detection — unsupported
        if stripped.startswith("!["):
            raise NotImplementedError(
                "Images are not supported by the minimal Markdown converter. "
                "Use --body-json with a full ProseMirror document, or "
                "upload the image first with `substack image-upload`."
            )

        # Regular paragraph text
        current_paragraph.extend(_parse_inline(stripped))

    # Flush remaining paragraph
    if current_paragraph:
        content.append({"type": "paragraph", "content": current_paragraph})

    return {"type": "doc", "content": content}


def _prosemirror_doc_to_body_string(doc: dict) -> str:
    """Stringify a ProseMirror doc — Substack requires a JSON string, not a
    nested dict. Submitting a nested object renders literal
    `{type:"doc",...}` text in the published post."""
    return json.dumps(doc)


# ---------------------------------------------------------------------------
# Draft CRUD
# ---------------------------------------------------------------------------


def list_drafts(client: SubstackClient, *, limit: int = 25) -> list:
    """List drafts. Tolerates both bare-array and {posts, hasMore, nextCursor}
    envelope shapes (Substack changed the shape in 2026-05)."""
    data = client.get("/api/v1/drafts", limit=limit)
    return extract_list(data, "posts")


def get_draft(client: SubstackClient, draft_id: int) -> dict:
    """Get a single draft by ID."""
    return client.get(f"/api/v1/drafts/{draft_id}")


def create_draft(
    client: SubstackClient,
    *,
    title: str,
    subtitle: Optional[str] = None,
    body_markdown: Optional[str] = None,
    body_json: Optional[str] = None,
    byline_ids: Optional[list] = None,
) -> dict:
    """Create a new draft. The draft_body MUST be a JSON string (per
    Substack's undocumented API), never a nested dict."""
    # Resolve body
    draft_body: Optional[str] = None
    if body_markdown is not None:
        doc = _markdown_to_prosemirror_doc(body_markdown)
        draft_body = _prosemirror_doc_to_body_string(doc)
    elif body_json is not None:
        # body_json can be a file path or a raw JSON string
        if os.path.isfile(body_json):
            with open(body_json) as f:
                doc = json.load(f)
        else:
            doc = json.loads(body_json)
        draft_body = _prosemirror_doc_to_body_string(doc)

    # Resolve bylines — auto-derive from self profile if not supplied.
    # Substack expects `draft_bylines` as a list of {id, is_guest} objects.
    if byline_ids is None:
        profile = client.get("/api/v1/user/profile/self", host="A")
        byline_ids = [{"id": profile.get("id"), "is_guest": False}]

    body: dict = {
        "draft_title": title,
        "draft_body": draft_body,
        "draft_bylines": byline_ids,
    }
    if subtitle is not None:
        body["draft_subtitle"] = subtitle

    return client.post("/api/v1/drafts", json_body=body)


def update_draft(client: SubstackClient, draft_id: int, **fields) -> dict:
    """Update a draft. Accepts draft_title, draft_subtitle, draft_body, etc."""
    return client.put(f"/api/v1/drafts/{draft_id}", json_body=fields)


def delete_draft(client: SubstackClient, draft_id: int) -> Any:
    """Delete a draft. Requires SUBSTACK_ENABLE_WRITE=true."""
    if not is_write_enabled():
        raise ValueError(
            "Write operations require SUBSTACK_ENABLE_WRITE=true "
            "(env var) or enable_write: true (config)"
        )
    return client.delete(f"/api/v1/drafts/{draft_id}")


# ---------------------------------------------------------------------------
# Publish lifecycle
# ---------------------------------------------------------------------------


def prepublish_draft(client: SubstackClient, draft_id: int) -> dict:
    """Run the prepublish validation pass. Returns {errors, suggestions}."""
    return client.get(f"/api/v1/drafts/{draft_id}/prepublish")


def publish_draft(
    client: SubstackClient,
    draft_id: int,
    *,
    send: bool = True,
    share_automatically: bool = False,
) -> dict:
    """Publish a draft. Attempts PUT first; falls back to POST on 404 only
    (documented verb conflict across community sources). Does NOT fall
    back on 401/403 (those are auth failures, not verb mismatches)."""
    body = {"send": send, "share_automatically": share_automatically}
    try:
        return client.put(f"/api/v1/drafts/{draft_id}/publish", json_body=body)
    except SubstackApiError as exc:
        if exc.status_code == 404:
            # Verb mismatch — retry with POST
            return client.post(f"/api/v1/drafts/{draft_id}/publish", json_body=body)
        raise


def schedule_draft(
    client: SubstackClient,
    draft_id: int,
    *,
    trigger_at: str,
    post_audience: str = "everyone",
) -> dict:
    """Schedule a draft for future publication. The body key MUST be
    `trigger_at` (not post_date/scheduled_at/publish_date)."""
    body = {
        "trigger_at": trigger_at,
        "post_audience": post_audience,
        "audience": post_audience,
    }
    return client.post(f"/api/v1/drafts/{draft_id}/scheduled_release", json_body=body)


def unschedule_draft(client: SubstackClient, draft_id: int) -> Any:
    """Cancel a scheduled publication."""
    return client.delete(f"/api/v1/drafts/{draft_id}/scheduled_release")


def get_scheduled_release(client: SubstackClient, draft_id: int) -> dict:
    """Get the current scheduled release info for a draft."""
    return client.get(f"/api/v1/drafts/{draft_id}/scheduled_release")


# ---------------------------------------------------------------------------
# Image upload
# ---------------------------------------------------------------------------


def upload_image(client: SubstackClient, image_path: str) -> dict:
    """Upload an image to Substack. Uses JSON + base64 encoding (not
    multipart — multipart reportedly 400s on this endpoint).

    Response fields: bytes, imageWidth, imageHeight, url.
    """
    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"Image file not found: {image_path}")

    # Determine extension
    ext = os.path.splitext(image_path)[1].lower().lstrip(".")
    if not ext:
        ext = "png"

    with open(image_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")

    body = {"image": f"data:image/{ext};base64,{encoded}"}
    return client.post("/api/v1/image", json_body=body)


# ---------------------------------------------------------------------------
# CLI commands — registered on drafts_app and app
# ---------------------------------------------------------------------------


def _make_client() -> SubstackClient:
    """Resolve auth and create a SubstackClient. Raises AuthError on failure."""
    cookies = resolve_cookies()
    pub_url = resolve_publication_url()
    return SubstackClient(cookies=cookies, publication_url=pub_url)


@drafts_app.command("list")
def drafts_list_cmd(limit: int = 25, pretty: bool = False):
    """List drafts."""
    try:
        client = _make_client()
        result = list_drafts(client, limit=limit)
        output_list(result, pretty=pretty, title="Drafts")
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@drafts_app.command("get")
def drafts_get_cmd(id: int, pretty: bool = False):
    """Get a single draft by ID."""
    try:
        client = _make_client()
        result = get_draft(client, id)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@drafts_app.command("create")
def drafts_create_cmd(
    title: str = typer.Option(..., "--title", "-t", help="Draft title"),
    subtitle: str = typer.Option(None, "--subtitle", help="Draft subtitle"),
    body_markdown: str = typer.Option(None, "--body-markdown", help="Markdown body (supports paragraphs, headings, bold, italic, links)"),
    body_json: str = typer.Option(None, "--body-json", help="Path to JSON file or raw ProseMirror JSON string"),
    pretty: bool = False,
):
    """Create a new draft."""
    if not is_write_enabled():
        emit_error(
            "Write operations require SUBSTACK_ENABLE_WRITE=true "
            "(env var) or enable_write: true (config)",
            pretty=pretty,
        )
    try:
        client = _make_client()
        result = create_draft(
            client,
            title=title,
            subtitle=subtitle,
            body_markdown=body_markdown,
            body_json=body_json,
        )
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@drafts_app.command("update")
def drafts_update_cmd(
    id: int,
    title: str = None,
    subtitle: str = None,
    body_markdown: str = None,
    pretty: bool = False,
):
    """Update a draft."""
    if not is_write_enabled():
        emit_error(
            "Write operations require SUBSTACK_ENABLE_WRITE=true",
            pretty=pretty,
        )
    try:
        client = _make_client()
        fields: dict = {}
        if title is not None:
            fields["draft_title"] = title
        if subtitle is not None:
            fields["draft_subtitle"] = subtitle
        if body_markdown is not None:
            doc = _markdown_to_prosemirror_doc(body_markdown)
            fields["draft_body"] = _prosemirror_doc_to_body_string(doc)
        result = update_draft(client, id, **fields)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@drafts_app.command("delete")
def drafts_delete_cmd(id: int, yes: bool = False, pretty: bool = False):
    """Delete a draft. Requires --yes to confirm."""
    if not yes:
        emit_error(
            f"Refusing to delete draft {id} without --yes. "
            "Re-run with --yes to confirm.",
            pretty=pretty,
        )
    if not is_write_enabled():
        emit_error(
            "Write operations require SUBSTACK_ENABLE_WRITE=true",
            pretty=pretty,
        )
    try:
        client = _make_client()
        result = delete_draft(client, id)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@drafts_app.command("prepublish")
def drafts_prepublish_cmd(id: int, pretty: bool = False):
    """Run prepublish validation on a draft."""
    try:
        client = _make_client()
        result = prepublish_draft(client, id)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@drafts_app.command("publish")
def drafts_publish_cmd(
    id: int,
    yes: bool = False,
    send: bool = True,
    share_automatically: bool = False,
    pretty: bool = False,
):
    """Publish a draft. Requires --yes to confirm."""
    if not yes:
        emit_error(
            f"Refusing to publish draft {id} without --yes. "
            "Publishing sends real email to subscribers. "
            "Re-run with --yes to confirm.",
            pretty=pretty,
        )
    if not is_write_enabled():
        emit_error(
            "Write operations require SUBSTACK_ENABLE_WRITE=true",
            pretty=pretty,
        )
    try:
        client = _make_client()
        result = publish_draft(
            client, id, send=send, share_automatically=share_automatically
        )
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@drafts_app.command("schedule")
def drafts_schedule_cmd(
    id: int,
    at: str = None,
    audience: str = "everyone",
    pretty: bool = False,
):
    """Schedule a draft for future publication. --at takes an ISO 8601 datetime."""
    if at is None:
        emit_error("--at is required (ISO 8601 datetime, e.g. 2026-07-11T09:00:00Z)", pretty=pretty)
    if not is_write_enabled():
        emit_error(
            "Write operations require SUBSTACK_ENABLE_WRITE=true",
            pretty=pretty,
        )
    try:
        client = _make_client()
        result = schedule_draft(client, id, trigger_at=at, post_audience=audience)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@drafts_app.command("unschedule")
def drafts_unschedule_cmd(id: int, pretty: bool = False):
    """Cancel a scheduled publication."""
    if not is_write_enabled():
        emit_error(
            "Write operations require SUBSTACK_ENABLE_WRITE=true",
            pretty=pretty,
        )
    try:
        client = _make_client()
        result = unschedule_draft(client, id)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@drafts_app.command("scheduled")
def drafts_scheduled_cmd(id: int, pretty: bool = False):
    """Get the current scheduled release info for a draft."""
    try:
        client = _make_client()
        result = get_scheduled_release(client, id)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@app.command("image-upload")
def image_upload_cmd(path: str, pretty: bool = False):
    """Upload an image and get the Substack-hosted URL."""
    if not is_write_enabled():
        emit_error(
            "Write operations require SUBSTACK_ENABLE_WRITE=true",
            pretty=pretty,
        )
    try:
        client = _make_client()
        result = upload_image(client, path)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except FileNotFoundError as exc:
        emit_error(str(exc), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)
