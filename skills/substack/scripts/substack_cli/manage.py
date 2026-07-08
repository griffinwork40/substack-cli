"""Substack CLI — manage operations (comments, reactions, subscribers,
recommendations, tags, publication settings). All mutating operations
require SUBSTACK_ENABLE_WRITE=true."""
from typing import Any, Optional

from substack_cli.app import (
    app,
    comments_app,
    publication_app,
    recommendations_app,
    subscribers_app,
    tags_app,
)
from substack_cli.auth import AuthError, is_write_enabled, resolve_cookies, resolve_publication_url
from substack_cli.client import (
    SubstackApiError,
    SubstackClient,
    emit_error,
    output,
    output_list,
)


def _make_client() -> SubstackClient:
    cookies = resolve_cookies()
    pub_url = resolve_publication_url()
    return SubstackClient(cookies=cookies, publication_url=pub_url)


# ---------------------------------------------------------------------------
# Comments & reactions
# ---------------------------------------------------------------------------


def create_comment(client: SubstackClient, post_id: int, body: str) -> dict:
    """Create a comment on a post."""
    if not is_write_enabled():
        raise ValueError("Write operations require SUBSTACK_ENABLE_WRITE=true")
    return client.post(f"/api/v1/post/{post_id}/comment", json_body={"body": body})


def delete_comment(client: SubstackClient, comment_id: int, *, host: str = "P") -> Any:
    """Delete a comment. Accepts host override for A vs P routing."""
    if not is_write_enabled():
        raise ValueError("Write operations require SUBSTACK_ENABLE_WRITE=true")
    return client.delete(f"/api/v1/comment/{comment_id}", host=host)


def react_to_post(client: SubstackClient, post_id: int, emoji: str = "❤") -> dict:
    """React to a post with a literal emoji character (not a named string
    like 'like' — the API requires the actual emoji codepoint)."""
    if not is_write_enabled():
        raise ValueError("Write operations require SUBSTACK_ENABLE_WRITE=true")
    body = {"reaction": emoji, "surface": "reader"}
    return client.post(f"/api/v1/post/{post_id}/reaction", json_body=body)


def remove_reaction(client: SubstackClient, post_id: int, emoji: str = "❤") -> Any:
    """Remove a reaction from a post."""
    if not is_write_enabled():
        raise ValueError("Write operations require SUBSTACK_ENABLE_WRITE=true")
    body = {"reaction": emoji, "surface": "reader"}
    return client.delete(f"/api/v1/post/{post_id}/reaction")


# ---------------------------------------------------------------------------
# Subscribers
# ---------------------------------------------------------------------------


def add_subscriber(client: SubstackClient, email: str, *, name: str = None) -> dict:
    """Add a subscriber by email."""
    if not is_write_enabled():
        raise ValueError("Write operations require SUBSTACK_ENABLE_WRITE=true")
    body: dict = {"email": email}
    if name:
        body["name"] = name
    return client.post("/api/v1/subscriber/add", json_body=body)


def remove_subscriber(client: SubstackClient, subscription_id: int) -> Any:
    """Remove a subscriber by subscription ID."""
    if not is_write_enabled():
        raise ValueError("Write operations require SUBSTACK_ENABLE_WRITE=true")
    return client.delete(f"/api/v1/subscriber/{subscription_id}")


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


def add_recommendation(client: SubstackClient, recommended_publication_id: int) -> dict:
    """Add a recommendation. CAVEAT: This endpoint is known to reject
    non-browser HTTP clients even with valid cookies (browser-vs-curl gap).
    On 403, surfaces a specific remediation message rather than the generic
    cookie expiry hint."""
    if not is_write_enabled():
        raise ValueError("Write operations require SUBSTACK_ENABLE_WRITE=true")
    try:
        return client.put(
            "/api/v1/recommendations",
            json_body={"recommended_publication_id": recommended_publication_id},
        )
    except SubstackApiError as exc:
        if exc.status_code == 403:
            raise SubstackApiError(
                "This endpoint is known to reject non-browser HTTP clients "
                "even with valid cookies (browser-vs-curl gap). See "
                "substack-api-reference.md Quirks. The cookie may still be "
                "valid — this is a known Substack limitation.",
                status_code=403,
            ) from exc
        raise


def remove_recommendation(client: SubstackClient, recommendation_id: int) -> Any:
    """Remove a recommendation. The trailing slash on /api/v1/recommendations/
    is REQUIRED — the bare path 404s."""
    if not is_write_enabled():
        raise ValueError("Write operations require SUBSTACK_ENABLE_WRITE=true")
    return client.delete("/api/v1/recommendations/")


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


def list_tags(client: SubstackClient) -> list:
    """List all tags for the publication."""
    return client.get("/api/v1/publication/post-tag")


def create_tag(client: SubstackClient, name: str) -> dict:
    """Create a new tag."""
    if not is_write_enabled():
        raise ValueError("Write operations require SUBSTACK_ENABLE_WRITE=true")
    return client.post("/api/v1/publication/post-tag", json_body={"name": name})


def delete_tag(client: SubstackClient, tag_id: int) -> Any:
    """Delete a tag."""
    if not is_write_enabled():
        raise ValueError("Write operations require SUBSTACK_ENABLE_WRITE=true")
    return client.delete(f"/api/v1/publication/post-tag/{tag_id}")


def attach_tag(client: SubstackClient, post_id: int, tag_id: int) -> dict:
    """Attach a tag to a post."""
    if not is_write_enabled():
        raise ValueError("Write operations require SUBSTACK_ENABLE_WRITE=true")
    return client.post(f"/api/v1/post/{post_id}/tag/{tag_id}")


def detach_tag(client: SubstackClient, post_id: int, tag_id: int) -> Any:
    """Detach a tag from a post."""
    if not is_write_enabled():
        raise ValueError("Write operations require SUBSTACK_ENABLE_WRITE=true")
    return client.delete(f"/api/v1/post/{post_id}/tag/{tag_id}")


# ---------------------------------------------------------------------------
# Publication settings
# ---------------------------------------------------------------------------

_RECOGNIZED_PUB_FIELDS = {"name", "hero_text", "language", "welcome_email_content"}


def update_publication(client: SubstackClient, **fields) -> dict:
    """Update a publication setting. Only ONE recognized field per call
    (name, hero_text, language, welcome_email_content)."""
    if not is_write_enabled():
        raise ValueError("Write operations require SUBSTACK_ENABLE_WRITE=true")

    unrecognized = set(fields.keys()) - _RECOGNIZED_PUB_FIELDS
    if unrecognized:
        raise ValueError(
            f"Unrecognized publication field(s): {unrecognized}. "
            f"Recognized fields: {_RECOGNIZED_PUB_FIELDS}"
        )

    if len(fields) == 0:
        raise ValueError(
            "No fields provided. Recognized fields: "
            f"{_RECOGNIZED_PUB_FIELDS}"
        )

    if len(fields) > 1:
        raise ValueError(
            f"Only one field per call (got {set(fields.keys())}). "
            f"Recognized fields: {_RECOGNIZED_PUB_FIELDS}"
        )

    return client.put("/api/v1/publication", json_body=fields)


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


@comments_app.command("create")
def comments_create_cmd(post_id: int, body: str, pretty: bool = False):
    """Create a comment on a post."""
    if not is_write_enabled():
        emit_error("Write operations require SUBSTACK_ENABLE_WRITE=true", pretty=pretty)
    try:
        client = _make_client()
        result = create_comment(client, post_id, body)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@comments_app.command("delete")
def comments_delete_cmd(comment_id: int, yes: bool = False, host: str = "P", pretty: bool = False):
    """Delete a comment. Requires --yes to confirm."""
    if not yes:
        emit_error(f"Refusing to delete comment {comment_id} without --yes.", pretty=pretty)
    if not is_write_enabled():
        emit_error("Write operations require SUBSTACK_ENABLE_WRITE=true", pretty=pretty)
    try:
        client = _make_client()
        result = delete_comment(client, comment_id, host=host)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@comments_app.command("react")
def comments_react_cmd(post_id: int, emoji: str = "❤", pretty: bool = False):
    """React to a post with an emoji."""
    if not is_write_enabled():
        emit_error("Write operations require SUBSTACK_ENABLE_WRITE=true", pretty=pretty)
    try:
        client = _make_client()
        result = react_to_post(client, post_id, emoji=emoji)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@comments_app.command("unreact")
def comments_unreact_cmd(post_id: int, emoji: str = "❤", pretty: bool = False):
    """Remove a reaction from a post."""
    if not is_write_enabled():
        emit_error("Write operations require SUBSTACK_ENABLE_WRITE=true", pretty=pretty)
    try:
        client = _make_client()
        result = remove_reaction(client, post_id, emoji=emoji)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@subscribers_app.command("add")
def subscribers_add_cmd(email: str, name: str = None, pretty: bool = False):
    """Add a subscriber by email."""
    if not is_write_enabled():
        emit_error("Write operations require SUBSTACK_ENABLE_WRITE=true", pretty=pretty)
    try:
        client = _make_client()
        result = add_subscriber(client, email, name=name)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@subscribers_app.command("remove")
def subscribers_remove_cmd(subscription_id: int, yes: bool = False, pretty: bool = False):
    """Remove a subscriber. Requires --yes to confirm."""
    if not yes:
        emit_error(f"Refusing to remove subscriber {subscription_id} without --yes.", pretty=pretty)
    if not is_write_enabled():
        emit_error("Write operations require SUBSTACK_ENABLE_WRITE=true", pretty=pretty)
    try:
        client = _make_client()
        result = remove_subscriber(client, subscription_id)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@recommendations_app.command("add")
def recommendations_add_cmd(publication_id: int, pretty: bool = False):
    """Add a recommendation."""
    if not is_write_enabled():
        emit_error("Write operations require SUBSTACK_ENABLE_WRITE=true", pretty=pretty)
    try:
        client = _make_client()
        result = add_recommendation(client, publication_id)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@recommendations_app.command("remove")
def recommendations_remove_cmd(recommendation_id: int, yes: bool = False, pretty: bool = False):
    """Remove a recommendation. Requires --yes to confirm."""
    if not yes:
        emit_error(f"Refusing to remove recommendation {recommendation_id} without --yes.", pretty=pretty)
    if not is_write_enabled():
        emit_error("Write operations require SUBSTACK_ENABLE_WRITE=true", pretty=pretty)
    try:
        client = _make_client()
        result = remove_recommendation(client, recommendation_id)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@tags_app.command("list")
def tags_list_cmd(pretty: bool = False):
    """List all tags."""
    try:
        client = _make_client()
        result = list_tags(client)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@tags_app.command("create")
def tags_create_cmd(name: str, pretty: bool = False):
    """Create a new tag."""
    if not is_write_enabled():
        emit_error("Write operations require SUBSTACK_ENABLE_WRITE=true", pretty=pretty)
    try:
        client = _make_client()
        result = create_tag(client, name)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@tags_app.command("delete")
def tags_delete_cmd(tag_id: int, yes: bool = False, pretty: bool = False):
    """Delete a tag. Requires --yes to confirm."""
    if not yes:
        emit_error(f"Refusing to delete tag {tag_id} without --yes.", pretty=pretty)
    if not is_write_enabled():
        emit_error("Write operations require SUBSTACK_ENABLE_WRITE=true", pretty=pretty)
    try:
        client = _make_client()
        result = delete_tag(client, tag_id)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@tags_app.command("attach")
def tags_attach_cmd(post_id: int, tag_id: int, pretty: bool = False):
    """Attach a tag to a post."""
    if not is_write_enabled():
        emit_error("Write operations require SUBSTACK_ENABLE_WRITE=true", pretty=pretty)
    try:
        client = _make_client()
        result = attach_tag(client, post_id, tag_id)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@tags_app.command("detach")
def tags_detach_cmd(post_id: int, tag_id: int, pretty: bool = False):
    """Detach a tag from a post."""
    if not is_write_enabled():
        emit_error("Write operations require SUBSTACK_ENABLE_WRITE=true", pretty=pretty)
    try:
        client = _make_client()
        result = detach_tag(client, post_id, tag_id)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@publication_app.command("update")
def publication_update_cmd(
    name: str = None,
    hero_text: str = None,
    language: str = None,
    pretty: bool = False,
):
    """Update a publication setting. Only one field per call."""
    if not is_write_enabled():
        emit_error("Write operations require SUBSTACK_ENABLE_WRITE=true", pretty=pretty)
    try:
        client = _make_client()
        fields: dict = {}
        if name is not None:
            fields["name"] = name
        if hero_text is not None:
            fields["hero_text"] = hero_text
        if language is not None:
            fields["language"] = language
        result = update_publication(client, **fields)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)