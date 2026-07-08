"""Tests for substack_cli.manage — comments, reactions."""
import json

import httpx
import pytest
import respx

from substack_cli.app import comments_app
from substack_cli.client import SubstackClient, SUBSTACK_COM
from substack_cli.manage import create_comment, delete_comment, react_to_post, remove_reaction


@respx.mock
def test_create_comment_posts_body_field(fake_cookies, fake_publication_url, isolated_config, write_enabled_env):
    route = respx.post(f"{fake_publication_url}/api/v1/post/123/comment").mock(
        return_value=httpx.Response(200, json={"id": 1, "ok": True})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    create_comment(client, 123, "Great post!")
    sent_body = json.loads(route.calls[0].request.content)
    assert sent_body["body"] == "Great post!"


def test_delete_comment_requires_write_gate_and_yes(
    isolated_config, authed_env, fake_cookies, fake_publication_url, cli_runner
):
    """No respx mock registered — write gate and --yes must block before any HTTP call."""
    result = cli_runner.invoke(comments_app, ["delete", "456"])
    assert result.exit_code != 0


@respx.mock
def test_delete_comment_host_override_accepted(fake_cookies, fake_publication_url, isolated_config, write_enabled_env):
    route = respx.delete(f"{SUBSTACK_COM}/api/v1/comment/456").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    delete_comment(client, 456, host="A")
    assert route.called


@respx.mock
def test_react_to_post_sends_literal_emoji_not_named_string(fake_cookies, fake_publication_url, isolated_config, write_enabled_env):
    route = respx.post(f"{fake_publication_url}/api/v1/post/123/reaction").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    react_to_post(client, 123, emoji="🔥")
    sent_body = json.loads(route.calls[0].request.content)
    assert sent_body["reaction"] == "🔥"
    assert sent_body["reaction"] != "like"
    assert sent_body["reaction"] != "heart"
    assert sent_body.get("surface") == "reader"


@respx.mock
def test_react_to_post_default_emoji_is_heart(fake_cookies, fake_publication_url, isolated_config, write_enabled_env):
    route = respx.post(f"{fake_publication_url}/api/v1/post/123/reaction").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    react_to_post(client, 123)
    sent_body = json.loads(route.calls[0].request.content)
    assert sent_body["reaction"] == "❤"


@respx.mock
def test_remove_reaction_uses_delete(fake_cookies, fake_publication_url, isolated_config, write_enabled_env):
    route = respx.delete(f"{fake_publication_url}/api/v1/post/123/reaction").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    remove_reaction(client, 123)
    assert route.called
    assert route.calls[0].request.method == "DELETE"