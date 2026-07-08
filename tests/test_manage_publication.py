"""Tests for substack_cli.manage — tags, publication settings."""
import json

import httpx
import pytest
import respx

from substack_cli.app import tags_app
from substack_cli.client import SubstackClient
from substack_cli.manage import (
    attach_tag,
    create_tag,
    delete_tag,
    detach_tag,
    list_tags,
    update_publication,
)


@respx.mock
def test_list_tags_returns_list(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/api/v1/publication/post-tag").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "name": "AI"}])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = list_tags(client)
    assert result == [{"id": 1, "name": "AI"}]


@respx.mock
def test_create_tag_posts_name(fake_cookies, fake_publication_url, isolated_config, write_enabled_env):
    route = respx.post(f"{fake_publication_url}/api/v1/publication/post-tag").mock(
        return_value=httpx.Response(200, json={"id": 1, "name": "AI"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    create_tag(client, "AI")
    sent_body = json.loads(route.calls[0].request.content)
    assert sent_body["name"] == "AI"


def test_delete_tag_requires_yes(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    result = cli_runner.invoke(tags_app, ["delete", "123"])
    assert result.exit_code != 0


@respx.mock
def test_attach_tag_posts_to_post_tag_path(fake_cookies, fake_publication_url, isolated_config, write_enabled_env):
    route = respx.post(f"{fake_publication_url}/api/v1/post/123/tag/456").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    attach_tag(client, 123, 456)
    assert route.called


@respx.mock
def test_detach_tag_uses_delete(fake_cookies, fake_publication_url, isolated_config, write_enabled_env):
    route = respx.delete(f"{fake_publication_url}/api/v1/post/123/tag/456").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    detach_tag(client, 123, 456)
    assert route.called
    assert route.calls[0].request.method == "DELETE"


@respx.mock
def test_update_publication_accepts_single_recognized_field(fake_cookies, fake_publication_url, isolated_config, write_enabled_env):
    route = respx.put(f"{fake_publication_url}/api/v1/publication").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    update_publication(client, name="New Name")
    sent_body = json.loads(route.calls[0].request.content)
    assert "name" in sent_body


def test_update_publication_rejects_multiple_fields_with_usage_error(fake_cookies, fake_publication_url):
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(ValueError):
        update_publication(client, name="X", language="en")


def test_update_publication_rejects_unrecognized_field(fake_cookies, fake_publication_url):
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(ValueError):
        update_publication(client, foo="bar")