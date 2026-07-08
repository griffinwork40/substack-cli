"""Tests for substack_cli.manage — subscribers and recommendations."""
import json

import httpx
import pytest
import respx

from substack_cli.app import subscribers_app, recommendations_app
from substack_cli.client import SubstackApiError, SubstackClient
from substack_cli.manage import add_recommendation, add_subscriber, remove_recommendation


@respx.mock
def test_add_subscriber_posts_email_and_optional_name(fake_cookies, fake_publication_url, isolated_config, write_enabled_env):
    route = respx.post(f"{fake_publication_url}/api/v1/subscriber/add").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    add_subscriber(client, "user@example.com", name="Test User")
    sent_body = json.loads(route.calls[0].request.content)
    assert sent_body["email"] == "user@example.com"
    assert sent_body.get("name") == "Test User"


def test_remove_subscriber_requires_yes(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    result = cli_runner.invoke(subscribers_app, ["remove", "123"])
    assert result.exit_code != 0


@respx.mock
def test_add_recommendation_403_surfaces_browser_vs_curl_specific_message(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    respx.put(f"{fake_publication_url}/api/v1/recommendations").mock(
        return_value=httpx.Response(403, json={"error": "forbidden"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError) as exc_info:
        add_recommendation(client, 999)
    msg = str(exc_info.value)
    assert "browser" in msg.lower() or "non-browser" in msg.lower() or "curl" in msg.lower()


@respx.mock
def test_remove_recommendation_uses_trailing_slash_path(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    route = respx.delete(f"{fake_publication_url}/api/v1/recommendations/").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    remove_recommendation(client, 123)
    assert route.called
    url = str(route.calls[0].request.url)
    assert url.endswith("/recommendations/")


def test_remove_recommendation_requires_yes(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    result = cli_runner.invoke(recommendations_app, ["remove", "123"])
    assert result.exit_code != 0