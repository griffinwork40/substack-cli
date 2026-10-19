"""Substack CLI — draft CRUD, publish lifecycle, image upload, MD→ProseMirror."""
import base64
import json
import os
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

# Re-export converter symbols; backwards-compat underscored aliases for
# notes.py and any external callers.
from substack_cli.markdown_doc import (
    parse_inline,
    markdown_to_doc,
    doc_to_body_string,
)
from substack_cli.body_assembly import assemble_body

_parse_inline = parse_inline
_markdown_to_prosemirror_doc = markdown_to_doc
_prosemirror_doc_to_body_string = doc_to_body_string

# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def _make_client() -> SubstackClient:
    return SubstackClient(cookies=resolve_cookies(), publication_url=resolve_publication_url())

def _write_gate(pretty: bool) -> bool:
    """Emit error and return False if write is not enabled."""
    if not is_write_enabled():
        emit_error(
            "Write operations require SUBSTACK_ENABLE_WRITE=true "
            "(env var) or enable_write: true (config)",
            pretty=pretty,
        )
        return False
    return True

def _handle_exc(exc: Exception, pretty: bool) -> None:
    emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)


# ---------------------------------------------------------------------------
# Draft CRUD (library functions — no CLI concerns)
# ---------------------------------------------------------------------------

def list_drafts(client: SubstackClient, *, limit: int = 25) -> list:
    """List drafts. Tolerates bare-array and paginated envelope shapes."""
    return extract_list(client.get("/api/v1/drafts", limit=limit), "posts")


def get_draft(client: SubstackClient, draft_id: int) -> dict:
    return client.get(f"/api/v1/drafts/{draft_id}")


def create_draft(
    client: SubstackClient,
    *,
    title: str,
    subtitle: Optional[str] = None,
    body_markdown: Optional[str] = None,
    body_json: Optional[str] = None,
    draft_body: Optional[str] = None,
    byline_ids: Optional[list] = None,
) -> dict:
    """Create a draft. body_markdown / body_json / draft_body are mutually exclusive.
    draft_body is an already-serialised ProseMirror JSON string (CLI path)."""
    if sum(x is not None for x in (body_markdown, body_json, draft_body)) > 1:
        raise ValueError("body_markdown, body_json, and draft_body are mutually exclusive.")
    _assembled: Optional[str] = draft_body
    if body_markdown is not None:
        _assembled = doc_to_body_string(markdown_to_doc(body_markdown))
    elif body_json is not None:
        if os.path.isfile(body_json):
            with open(body_json) as f:
                doc = json.load(f)
        else:
            doc = json.loads(body_json)
        _assembled = doc_to_body_string(doc)
    draft_body = _assembled

    if byline_ids is None:
        profile = client.get("/api/v1/user/profile/self", host="A")
        byline_ids = [{"id": profile.get("id"), "is_guest": False}]

    body: dict = {"draft_title": title, "draft_body": draft_body, "draft_bylines": byline_ids}
    if subtitle is not None:
        body["draft_subtitle"] = subtitle
    return client.post("/api/v1/drafts", json_body=body)


def update_draft(client: SubstackClient, draft_id: int, **fields) -> dict:
    return client.put(f"/api/v1/drafts/{draft_id}", json_body=fields)


def delete_draft(client: SubstackClient, draft_id: int) -> Any:
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
    return client.get(f"/api/v1/drafts/{draft_id}/prepublish")


def publish_draft(client: SubstackClient, draft_id: int, *, send: bool = True, share_automatically: bool = False) -> dict:
    """Publish a draft; tries PUT, falls back to POST on 404."""
    body = {"send": send, "share_automatically": share_automatically}
    try:
        return client.put(f"/api/v1/drafts/{draft_id}/publish", json_body=body)
    except SubstackApiError as exc:
        if exc.status_code == 404:
            return client.post(f"/api/v1/drafts/{draft_id}/publish", json_body=body)
        raise


def schedule_draft(client: SubstackClient, draft_id: int, *, trigger_at: str, post_audience: str = "everyone") -> dict:
    body = {"trigger_at": trigger_at, "post_audience": post_audience, "audience": post_audience}
    return client.post(f"/api/v1/drafts/{draft_id}/scheduled_release", json_body=body)


def unschedule_draft(client: SubstackClient, draft_id: int) -> Any:
    return client.delete(f"/api/v1/drafts/{draft_id}/scheduled_release")


def get_scheduled_release(client: SubstackClient, draft_id: int) -> dict:
    return client.get(f"/api/v1/drafts/{draft_id}/scheduled_release")


# ---------------------------------------------------------------------------
# Image upload
# ---------------------------------------------------------------------------

def upload_image(client: SubstackClient, image_path: str, *, host: str = "P") -> dict:
    """Upload an image (JSON+base64). Response: bytes, imageWidth, imageHeight, url."""
    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"Image file not found: {image_path}")
    ext = os.path.splitext(image_path)[1].lower().lstrip(".") or "png"
    with open(image_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return client.post("/api/v1/image", host=host, json_body={"image": f"data:image/{ext};base64,{encoded}"})


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

@drafts_app.command("list")
def drafts_list_cmd(limit: int = 25, pretty: bool = False):
    """List drafts."""
    try:
        output_list(list_drafts(_make_client(), limit=limit), pretty=pretty, title="Drafts")
    except Exception as exc:
        _handle_exc(exc, pretty)


@drafts_app.command("get")
def drafts_get_cmd(id: int, pretty: bool = False):
    """Get a single draft by ID."""
    try:
        output(get_draft(_make_client(), id), pretty=pretty)
    except Exception as exc:
        _handle_exc(exc, pretty)


@drafts_app.command("create")
def drafts_create_cmd(
    title: str = typer.Option(..., "--title", "-t", help="Draft title"),
    subtitle: str = typer.Option(None, "--subtitle", help="Draft subtitle"),
    body_markdown: str = typer.Option(None, "--body-markdown", help="Markdown body (paragraphs, headings, bold, italic, links, ---, line breaks)"),
    body_json: str = typer.Option(None, "--body-json", help="Path to JSON file or raw ProseMirror JSON string"),
    append_markdown: str = typer.Option(None, "--append-markdown", help="Markdown snippet (or file) to append"),
    prepend_markdown: str = typer.Option(None, "--prepend-markdown", help="Markdown snippet (or file) to prepend"),
    pretty: bool = False,
):
    """Create a new draft."""
    if not _write_gate(pretty):
        return
    try:
        draft_body = assemble_body(
            body_markdown=body_markdown, body_json=body_json,
            append_markdown=append_markdown, prepend_markdown=prepend_markdown,
        )
        result = create_draft(_make_client(), title=title, subtitle=subtitle, draft_body=draft_body)
        output(result, pretty=pretty)
    except (ValueError, NotImplementedError) as exc:
        emit_error(str(exc), pretty=pretty)
    except Exception as exc:
        _handle_exc(exc, pretty)


@drafts_app.command("update")
def drafts_update_cmd(
    id: int,
    title: str = typer.Option(None, "--title", "-t"),
    subtitle: str = typer.Option(None, "--subtitle"),
    body_markdown: str = typer.Option(None, "--body-markdown", help="New body as Markdown"),
    body_json: str = typer.Option(None, "--body-json", help="New body as ProseMirror JSON or file path"),
    append_markdown: str = typer.Option(None, "--append-markdown", help="Markdown snippet (or file) to append"),
    prepend_markdown: str = typer.Option(None, "--prepend-markdown", help="Markdown snippet (or file) to prepend"),
    pretty: bool = False,
):
    """Update a draft."""
    if not _write_gate(pretty):
        return
    try:
        client = _make_client()
        fields: dict = {}
        if title is not None:
            fields["draft_title"] = title
        if subtitle is not None:
            fields["draft_subtitle"] = subtitle
        has_body = body_markdown is not None or body_json is not None
        has_snippet = append_markdown is not None or prepend_markdown is not None
        if has_body or has_snippet:
            draft_body = assemble_body(
                body_markdown=body_markdown, body_json=body_json,
                append_markdown=append_markdown, prepend_markdown=prepend_markdown,
                is_update=True,
            )
            if draft_body is not None:
                fields["draft_body"] = draft_body
        output(update_draft(client, id, **fields), pretty=pretty)
    except (ValueError, NotImplementedError) as exc:
        emit_error(str(exc), pretty=pretty)
    except Exception as exc:
        _handle_exc(exc, pretty)


@drafts_app.command("set-cover")
def drafts_set_cover_cmd(draft_id: int, image_path: str, pretty: bool = False):
    """Set a draft's cover image."""
    if not _write_gate(pretty):
        return
    try:
        client = _make_client()
        url = upload_image(client, image_path)["url"]
        output(update_draft(client, draft_id, cover_image=url), pretty=pretty)
    except (FileNotFoundError, SubstackApiError, AuthError) as exc:
        _handle_exc(exc, pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@drafts_app.command("delete")
def drafts_delete_cmd(id: int, yes: bool = False, pretty: bool = False):
    """Delete a draft. Requires --yes to confirm."""
    if not yes:
        emit_error(f"Refusing to delete draft {id} without --yes. Re-run with --yes to confirm.", pretty=pretty)
    if not _write_gate(pretty):
        return
    try:
        output(delete_draft(_make_client(), id), pretty=pretty)
    except Exception as exc:
        _handle_exc(exc, pretty)


@drafts_app.command("prepublish")
def drafts_prepublish_cmd(id: int, pretty: bool = False):
    """Run prepublish validation on a draft."""
    try:
        output(prepublish_draft(_make_client(), id), pretty=pretty)
    except Exception as exc:
        _handle_exc(exc, pretty)


@drafts_app.command("publish")
def drafts_publish_cmd(id: int, yes: bool = False, send: bool = True, share_automatically: bool = False, pretty: bool = False):
    """Publish a draft. Requires --yes to confirm."""
    if not yes:
        emit_error(f"Refusing to publish draft {id} without --yes. Publishing sends real email to subscribers.", pretty=pretty)
    if not _write_gate(pretty):
        return
    try:
        output(publish_draft(_make_client(), id, send=send, share_automatically=share_automatically), pretty=pretty)
    except Exception as exc:
        _handle_exc(exc, pretty)


@drafts_app.command("schedule")
def drafts_schedule_cmd(id: int, at: str = None, audience: str = "everyone", pretty: bool = False):
    """Schedule a draft. --at takes an ISO 8601 datetime."""
    if at is None:
        emit_error("--at is required (ISO 8601 datetime, e.g. 2026-07-11T09:00:00Z)", pretty=pretty)
    if not _write_gate(pretty):
        return
    try:
        output(schedule_draft(_make_client(), id, trigger_at=at, post_audience=audience), pretty=pretty)
    except Exception as exc:
        _handle_exc(exc, pretty)


@drafts_app.command("unschedule")
def drafts_unschedule_cmd(id: int, pretty: bool = False):
    """Cancel a scheduled publication."""
    if not _write_gate(pretty):
        return
    try:
        output(unschedule_draft(_make_client(), id), pretty=pretty)
    except Exception as exc:
        _handle_exc(exc, pretty)


@drafts_app.command("scheduled")
def drafts_scheduled_cmd(id: int, pretty: bool = False):
    """Get the current scheduled release info for a draft."""
    try:
        output(get_scheduled_release(_make_client(), id), pretty=pretty)
    except Exception as exc:
        _handle_exc(exc, pretty)


@app.command("image-upload")
def image_upload_cmd(path: str, pretty: bool = False):
    """Upload an image and get the Substack-hosted URL."""
    if not _write_gate(pretty):
        return
    try:
        output(upload_image(_make_client(), path), pretty=pretty)
    except (FileNotFoundError, SubstackApiError, AuthError) as exc:
        _handle_exc(exc, pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)
