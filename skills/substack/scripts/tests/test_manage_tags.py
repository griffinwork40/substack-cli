"""Tests for tag management: type fixes and name-resolution logic.

Bug: tag_id was declared as int, but Substack tag IDs are UUID strings.
Typer rejected any UUID before the function body ran.
Fix: tag_id is now str everywhere; resolve_tag_id() accepts UUID or name.
"""

import httpx
import pytest
import respx

from substack_cli.client import SubstackClient
from substack_cli.manage import (
    attach_tag,
    detach_tag,
    delete_tag,
    resolve_tag_id,
)

TAG_UUID = "870f66cb-0b72-4fc3-a8bd-4b31bc88ca48"
TAG_NAME = "AI"
FAKE_TAGS = [
    {"id": TAG_UUID, "name": TAG_NAME},
    {"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "name": "Python"},
]


# ---------------------------------------------------------------------------
# resolve_tag_id
# ---------------------------------------------------------------------------


@respx.mock
def test_resolve_tag_id_returns_uuid_unchanged(fake_cookies, fake_publication_url, isolated_config, write_enabled_env):
    """A valid UUID is passed through without a tags-list fetch."""
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    # No mock registered — any HTTP call would raise.
    result = resolve_tag_id(client, TAG_UUID)
    assert result == TAG_UUID


@respx.mock
def test_resolve_tag_id_resolves_name_case_insensitive(fake_cookies, fake_publication_url, isolated_config, write_enabled_env):
    """A tag name (any case) is resolved to its UUID via the tags list."""
    respx.get(f"{fake_publication_url}/api/v1/publication/post-tag").mock(
        return_value=httpx.Response(200, json=FAKE_TAGS)
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    assert resolve_tag_id(client, "ai") == TAG_UUID
    assert resolve_tag_id(client, "AI") == TAG_UUID
    assert resolve_tag_id(client, "Ai") == TAG_UUID


@respx.mock
def test_resolve_tag_id_raises_on_unknown_name(fake_cookies, fake_publication_url, isolated_config, write_enabled_env):
    """An unrecognised name raises ValueError with a helpful message."""
    respx.get(f"{fake_publication_url}/api/v1/publication/post-tag").mock(
        return_value=httpx.Response(200, json=FAKE_TAGS)
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(ValueError, match="No tag named 'nonexistent'"):
        resolve_tag_id(client, "nonexistent")


# ---------------------------------------------------------------------------
# attach_tag / detach_tag / delete_tag — UUID strings are accepted
# ---------------------------------------------------------------------------


@respx.mock
def test_attach_tag_accepts_uuid_string(fake_cookies, fake_publication_url, isolated_config, write_enabled_env):
    route = respx.post(
        f"{fake_publication_url}/api/v1/post/123/{TAG_UUID}"  # matches suffix
    ).mock(return_value=httpx.Response(200, json={"ok": True}))
    # Use a URL pattern that matches the actual endpoint
    route2 = respx.post(
        url__regex=rf"/api/v1/post/123/tag/{TAG_UUID}"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = attach_tag(client, 123, TAG_UUID)
    assert result == {"ok": True}


@respx.mock
def test_detach_tag_accepts_uuid_string(fake_cookies, fake_publication_url, isolated_config, write_enabled_env):
    respx.delete(
        url__regex=rf"/api/v1/post/123/tag/{TAG_UUID}"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = detach_tag(client, 123, TAG_UUID)
    assert result == {"ok": True}


@respx.mock
def test_delete_tag_accepts_uuid_string(fake_cookies, fake_publication_url, isolated_config, write_enabled_env):
    respx.delete(
        url__regex=rf"/api/v1/publication/post-tag/{TAG_UUID}"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = delete_tag(client, TAG_UUID)
    assert result == {"ok": True}
