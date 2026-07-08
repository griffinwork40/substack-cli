"""Tests for anonymous/public read endpoints — archive, post, and feed must
work WITHOUT any cookie configured (they are public per SKILL.md and
references/substack-api.md), while authenticated commands must still raise
the auth error when no cookie is set.

Isolation: `isolated_config` redirects CONFIG_PATH to tmp and clears all
SUBSTACK_* env vars, so no real credentials/config are read. We then set only
SUBSTACK_PUBLICATION_URL (never a cookie) so the request has a target host but
remains unauthenticated — proving the ABSENCE of a cookie does not raise.
"""
import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

# Import command modules to register their commands on app/sub-apps.
from substack_cli import config as _config_module  # noqa: F401
from substack_cli import read as _read_module  # noqa: F401
from substack_cli import publish as _publish_module  # noqa: F401
from substack_cli import manage as _manage_module  # noqa: F401
from substack_cli.app import app

PUB = "https://charliepgarcia.substack.com"

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Capital Mischief</title>
    <item>
      <title>Post One</title>
      <link>https://example.com/p/post-one</link>
      <pubDate>Mon, 01 Jul 2025 10:00:00 GMT</pubDate>
      <description>Summary text</description>
    </item>
  </channel>
</rss>"""


@pytest.fixture
def anon_env(isolated_config, monkeypatch):
    """No cookies configured (isolated_config clears them); only a publication
    URL is set so requests have a target host but are unauthenticated."""
    monkeypatch.setenv("SUBSTACK_PUBLICATION_URL", "charliepgarcia")
    return isolated_config


# --- (a) public endpoints succeed with NO cookies ------------------------


@respx.mock
def test_archive_succeeds_without_cookies(anon_env):
    route = respx.get(f"{PUB}/api/v1/archive").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "title": "Public Post"}])
    )
    runner = CliRunner()
    result = runner.invoke(app, ["archive"])
    assert result.exit_code == 0, result.output
    assert route.called
    parsed = json.loads(result.stdout.strip())
    assert parsed == [{"id": 1, "title": "Public Post"}]


@respx.mock
def test_archive_without_cookies_sends_no_cookie_header(anon_env):
    """The absence of a cookie must produce a request with no (or empty)
    Cookie header — proving we did not fabricate credentials."""
    route = respx.get(f"{PUB}/api/v1/archive").mock(
        return_value=httpx.Response(200, json=[])
    )
    runner = CliRunner()
    result = runner.invoke(app, ["archive"])
    assert result.exit_code == 0, result.output
    cookie_header = route.calls[0].request.headers.get("cookie", "")
    assert cookie_header == ""


@respx.mock
def test_post_succeeds_without_cookies(anon_env):
    respx.get(f"{PUB}/api/v1/posts/my-public-slug").mock(
        return_value=httpx.Response(200, json={"id": 7, "slug": "my-public-slug"})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["post", "my-public-slug"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout.strip())
    assert parsed == {"id": 7, "slug": "my-public-slug"}


@respx.mock
def test_feed_succeeds_without_cookies(anon_env):
    respx.get(f"{PUB}/feed").mock(
        return_value=httpx.Response(
            200,
            content=SAMPLE_RSS.encode(),
            headers={"content-type": "application/xml"},
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["feed"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout.strip())
    assert isinstance(parsed, list)
    assert parsed and parsed[0]["title"] == "Post One"


@respx.mock
def test_feed_raw_succeeds_without_cookies(anon_env):
    respx.get(f"{PUB}/feed").mock(
        return_value=httpx.Response(
            200,
            content=SAMPLE_RSS.encode(),
            headers={"content-type": "application/xml"},
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["feed", "--raw"])
    assert result.exit_code == 0, result.output
    assert "<rss" in result.stdout


@respx.mock
def test_public_endpoint_still_uses_cookie_when_present(monkeypatch, isolated_config):
    """If a cookie IS configured, the public endpoint should still send it —
    anonymous means 'cookie optional', not 'cookie forbidden'."""
    monkeypatch.setenv("SUBSTACK_PUBLICATION_URL", "charliepgarcia")
    monkeypatch.setenv("SUBSTACK_COOKIES_STRING", "connect.sid=s%3Areal-token-abcdef")
    route = respx.get(f"{PUB}/api/v1/archive").mock(
        return_value=httpx.Response(200, json=[])
    )
    runner = CliRunner()
    result = runner.invoke(app, ["archive"])
    assert result.exit_code == 0, result.output
    cookie_header = route.calls[0].request.headers.get("cookie", "")
    assert "connect.sid=s%3Areal-token-abcdef" in cookie_header


# --- (b) authenticated commands STILL raise without a cookie -------------


@respx.mock
def test_subscribers_count_still_requires_cookie(anon_env):
    """An authenticated command must still emit the auth error and make zero
    HTTP calls when no cookie is configured (publication URL is set, so the
    failure is specifically the missing cookie)."""
    route = respx.get(f"{PUB}/api/v1/publish-dashboard/summary").mock(
        return_value=httpx.Response(200, json={"subscribers": 1})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["subscribers", "count"])
    assert result.exit_code != 0
    assert not route.called
    combined = (result.stdout or "") + (result.output or "")
    assert "SUBSTACK_COOKIES_STRING" in combined or "cookies" in combined.lower()


@respx.mock
def test_analytics_summary_still_requires_cookie(anon_env):
    route = respx.get(f"{PUB}/api/v1/publish-dashboard/summary").mock(
        return_value=httpx.Response(200, json={"subscribers": 1})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["analytics", "summary"])
    assert result.exit_code != 0
    assert not route.called
    combined = (result.stdout or "") + (result.output or "")
    assert "SUBSTACK_COOKIES_STRING" in combined or "cookies" in combined.lower()


@respx.mock
def test_search_remains_authenticated_conservative(anon_env):
    """`search` is deliberately left requiring auth (docs mark its filtering
    behavior unconfirmed), so it must still error without a cookie."""
    route = respx.get(f"{PUB}/api/v1/archive").mock(
        return_value=httpx.Response(200, json=[])
    )
    runner = CliRunner()
    result = runner.invoke(app, ["search", "AI"])
    assert result.exit_code != 0
    assert not route.called
    combined = (result.stdout or "") + (result.output or "")
    assert "SUBSTACK_COOKIES_STRING" in combined or "cookies" in combined.lower()
