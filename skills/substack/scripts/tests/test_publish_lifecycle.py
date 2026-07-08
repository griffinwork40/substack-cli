"""Tests for substack_cli.publish — prepublish/publish/schedule lifecycle.

No test in this suite performs a real network call; every Substack API
interaction is mocked with respx.
"""
import json

import httpx
import pytest
import respx

from substack_cli.app import drafts_app
from substack_cli.client import SubstackApiError, SubstackClient
from substack_cli.publish import (
    get_scheduled_release,
    prepublish_draft,
    publish_draft,
    schedule_draft,
    unschedule_draft,
)

# Importing substack_cli.publish (above) registers the drafts_app CLI
# commands as a side effect of module import.


# ---------------------------------------------------------------------------
# prepublish_draft
# ---------------------------------------------------------------------------

@respx.mock
def test_prepublish_uses_get_not_post(fake_cookies, fake_publication_url):
    route = respx.get(f"{fake_publication_url}/api/v1/drafts/123/prepublish").mock(
        return_value=httpx.Response(200, json={"errors": [], "suggestions": []})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)

    prepublish_draft(client, 123)

    assert route.called
    assert route.calls[0].request.method == "GET"


@respx.mock
def test_prepublish_returns_errors_and_suggestions_keys(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/api/v1/drafts/123/prepublish").mock(
        return_value=httpx.Response(200, json={"errors": [], "suggestions": []})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)

    result = prepublish_draft(client, 123)

    assert result == {"errors": [], "suggestions": []}


# ---------------------------------------------------------------------------
# publish_draft
# ---------------------------------------------------------------------------

@respx.mock
def test_publish_draft_attempts_put_first(fake_cookies, fake_publication_url):
    route = respx.put(f"{fake_publication_url}/api/v1/drafts/123/publish").mock(
        return_value=httpx.Response(200, json={"id": 123, "published": True})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)

    publish_draft(client, 123)

    assert route.called
    assert route.calls[0].request.method == "PUT"


@respx.mock
def test_publish_draft_falls_back_to_post_on_404_only(fake_cookies, fake_publication_url, no_sleep):
    put_route = respx.put(f"{fake_publication_url}/api/v1/drafts/123/publish").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    post_route = respx.post(f"{fake_publication_url}/api/v1/drafts/123/publish").mock(
        return_value=httpx.Response(200, json={"id": 123, "published": True})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)

    publish_draft(client, 123)

    assert put_route.called
    assert post_route.called
    assert len(respx.calls) == 2


@respx.mock
def test_publish_draft_does_not_fallback_on_401_or_403(fake_cookies, fake_publication_url):
    put_route = respx.put(f"{fake_publication_url}/api/v1/drafts/123/publish").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    post_route = respx.post(f"{fake_publication_url}/api/v1/drafts/123/publish").mock(
        return_value=httpx.Response(200, json={"id": 123, "published": True})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)

    with pytest.raises(SubstackApiError):
        publish_draft(client, 123)

    assert put_route.called
    assert not post_route.called


def test_publish_draft_requires_yes_flag_at_cli_layer(cli_runner, authed_env, write_enabled_env):
    result = cli_runner.invoke(drafts_app, ["publish", "123"])

    assert result.exit_code != 0
    assert "yes" in result.output.lower()


# ---------------------------------------------------------------------------
# schedule_draft
# ---------------------------------------------------------------------------

@respx.mock
def test_schedule_draft_body_uses_trigger_at_key_not_post_date(fake_cookies, fake_publication_url):
    route = respx.post(f"{fake_publication_url}/api/v1/drafts/123/scheduled_release").mock(
        return_value=httpx.Response(200, json={"trigger_at": "2026-01-01T00:00:00Z"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)

    schedule_draft(client, 123, trigger_at="2026-01-01T00:00:00Z")

    sent_body = json.loads(route.calls[0].request.content)
    assert "trigger_at" in sent_body
    assert sent_body["trigger_at"] == "2026-01-01T00:00:00Z"
    assert "post_date" not in sent_body
    assert "scheduled_at" not in sent_body


# ---------------------------------------------------------------------------
# unschedule_draft
# ---------------------------------------------------------------------------

@respx.mock
def test_unschedule_draft_uses_delete(fake_cookies, fake_publication_url):
    route = respx.delete(f"{fake_publication_url}/api/v1/drafts/123/scheduled_release").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)

    unschedule_draft(client, 123)

    assert route.called
    assert route.calls[0].request.method == "DELETE"


# ---------------------------------------------------------------------------
# get_scheduled_release
# ---------------------------------------------------------------------------

@respx.mock
def test_get_scheduled_release_uses_get(fake_cookies, fake_publication_url):
    route = respx.get(f"{fake_publication_url}/api/v1/drafts/123/scheduled_release").mock(
        return_value=httpx.Response(200, json={"trigger_at": "2026-01-01T00:00:00Z"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)

    get_scheduled_release(client, 123)

    assert route.called
    assert route.calls[0].request.method == "GET"
