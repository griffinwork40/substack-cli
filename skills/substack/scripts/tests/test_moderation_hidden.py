"""Tests for the AutoMod hidden-comment feature (Issue #29).

Covers:
- parse_since: shorthand and ISO-8601 parsing, bad input rejection
- get_hidden_comments: endpoint path, query params, envelope normalisation,
  client-side since-filter, bare-list response
- _format_item: field projection / graceful fallback
- CLI command: happy path, --since filter, --limit, auth error, no-auth,
  empty list, 404 (endpoint unverified)
"""
from __future__ import annotations

import json
import datetime

import httpx
import pytest
import respx

from substack_cli.app import app
from substack_cli.client import SubstackClient
import substack_cli.moderation_hidden as mh
from substack_cli.moderation_hidden import (
    AUTOMOD_HIDDEN_PATH,
    get_hidden_comments,
    parse_since,
    _format_item,
)

_FAKE_PUB = "https://charliepgarcia.substack.com"
_HIDDEN_URL = f"{_FAKE_PUB}{AUTOMOD_HIDDEN_PATH}"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SAMPLE_ITEM = {
    "id": 42,
    "type": "comment",
    "author": {"id": 7, "name": "Alice", "handle": "alice"},
    "subscriberState": "free",
    "body": "Hey buy crypto NOW",
    "automod_reason": "reply_rule:whatsapp",
    "hidden_at": "2026-10-13T10:00:00Z",
    "post_id": 99,
    "url": "https://charliepgarcia.substack.com/p/post#comment-42",
}

_SAMPLE_ITEM_2 = {
    "id": 43,
    "type": "note_reply",
    "author": {"id": 8, "name": "Bob", "handle": "bob"},
    "body": "DM me on Telegram",
    "automod_reason": "reply_rule:telegram",
    "hidden_at": "2026-10-15T08:00:00Z",
    "post_id": 100,
}


# ---------------------------------------------------------------------------
# parse_since — unit tests
# ---------------------------------------------------------------------------


def test_parse_since_none_returns_none():
    assert parse_since(None) is None


@pytest.mark.parametrize("shorthand", ["1h", "6h", "12h", "24h", "48h", "7d"])
def test_parse_since_shorthand_returns_past_datetime(shorthand):
    result = parse_since(shorthand)
    assert isinstance(result, datetime.datetime)
    assert result.tzinfo is not None
    assert result < datetime.datetime.now(datetime.timezone.utc)


def test_parse_since_24h_is_roughly_one_day_ago():
    before = datetime.datetime.now(datetime.timezone.utc)
    result = parse_since("24h")
    after = datetime.datetime.now(datetime.timezone.utc)
    # Should be between (now - 24h - 1s) and (now - 24h + 1s)
    expected = before - datetime.timedelta(hours=24)
    assert abs((result - expected).total_seconds()) < 2


def test_parse_since_iso_z():
    result = parse_since("2026-10-12T00:00:00Z")
    assert result.year == 2026
    assert result.month == 10
    assert result.day == 12


def test_parse_since_iso_offset():
    result = parse_since("2026-10-12T00:00:00+00:00")
    assert result.year == 2026


def test_parse_since_bad_value_raises():
    with pytest.raises(ValueError, match="not a recognised shorthand"):
        parse_since("yesterday")


def test_parse_since_bad_value_lists_shorthands():
    with pytest.raises(ValueError, match="24h"):
        parse_since("3weeks")


# ---------------------------------------------------------------------------
# _format_item — unit tests (pure function, no HTTP)
# ---------------------------------------------------------------------------


def test_format_item_extracts_author_fields():
    result = _format_item(_SAMPLE_ITEM)
    assert result["author_id"] == 7
    assert result["author_name"] == "Alice"
    assert result["author_handle"] == "alice"


def test_format_item_extracts_automod_reason():
    result = _format_item(_SAMPLE_ITEM)
    assert result["reason"] == "reply_rule:whatsapp"


def test_format_item_extracts_body_as_text():
    result = _format_item(_SAMPLE_ITEM)
    assert result["text"] == "Hey buy crypto NOW"


def test_format_item_extracts_link():
    result = _format_item(_SAMPLE_ITEM)
    assert "comment-42" in result["link"]


def test_format_item_preserves_raw():
    result = _format_item(_SAMPLE_ITEM, include_raw=True)
    assert result["_raw"] == _SAMPLE_ITEM


def test_format_item_graceful_missing_fields():
    result = _format_item({})
    assert result["author_id"] is None
    assert result["text"] is None
    assert result["reason"] is None
    assert result["type"] == "comment"  # default


def test_format_item_flat_fields_no_author_obj():
    """Handles flat author_name / author_id fields (no nested author object)."""
    raw = {"id": 10, "author_id": 5, "author_name": "Carol", "body": "hi"}
    result = _format_item(raw)
    assert result["author_id"] == 5
    assert result["author_name"] == "Carol"


def test_format_item_alternate_subscriber_field():
    # Build a raw item with no top-level subscriberState so the nested
    # author.subscriberState is the only source.
    raw = {
        "id": 10,
        "author": {"id": 7, "name": "A", "subscriberState": "paid"},
    }
    result = _format_item(raw)
    assert result["subscriber_state"] == "paid"


# ---------------------------------------------------------------------------
# get_hidden_comments — unit tests with respx mocking
# ---------------------------------------------------------------------------


@respx.mock
def test_get_hidden_comments_calls_correct_endpoint(
    fake_cookies, fake_publication_url, isolated_config, authed_env
):
    route = respx.get(_HIDDEN_URL).mock(
        return_value=httpx.Response(200, json=[_SAMPLE_ITEM])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_hidden_comments(client)
    assert route.called
    assert len(result) == 1
    assert result[0]["id"] == 42


@respx.mock
def test_get_hidden_comments_sends_limit_param(
    fake_cookies, fake_publication_url, isolated_config, authed_env
):
    route = respx.get(_HIDDEN_URL).mock(
        return_value=httpx.Response(200, json=[])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    get_hidden_comments(client, limit=50)
    assert route.called
    assert "limit=50" in str(route.calls[0].request.url)


@respx.mock
def test_get_hidden_comments_sends_since_param(
    fake_cookies, fake_publication_url, isolated_config, authed_env
):
    route = respx.get(_HIDDEN_URL).mock(
        return_value=httpx.Response(200, json=[])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    get_hidden_comments(client, since="24h")
    assert route.called
    url_str = str(route.calls[0].request.url)
    assert "since=" in url_str


@respx.mock
def test_get_hidden_comments_normalises_envelope_dict(
    fake_cookies, fake_publication_url, isolated_config, authed_env
):
    """API returns {"comments": [...]} envelope — must be unwrapped."""
    respx.get(_HIDDEN_URL).mock(
        return_value=httpx.Response(200, json={"comments": [_SAMPLE_ITEM]})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_hidden_comments(client)
    assert len(result) == 1
    assert result[0]["id"] == 42


@respx.mock
def test_get_hidden_comments_normalises_hidden_comments_key(
    fake_cookies, fake_publication_url, isolated_config, authed_env
):
    respx.get(_HIDDEN_URL).mock(
        return_value=httpx.Response(200, json={"hidden_comments": [_SAMPLE_ITEM]})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_hidden_comments(client)
    assert len(result) == 1


@respx.mock
def test_get_hidden_comments_bare_list_response(
    fake_cookies, fake_publication_url, isolated_config, authed_env
):
    """Bare list response (no envelope) works."""
    respx.get(_HIDDEN_URL).mock(
        return_value=httpx.Response(200, json=[_SAMPLE_ITEM, _SAMPLE_ITEM_2])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_hidden_comments(client)
    assert len(result) == 2


@respx.mock
def test_get_hidden_comments_empty_list(
    fake_cookies, fake_publication_url, isolated_config, authed_env
):
    respx.get(_HIDDEN_URL).mock(return_value=httpx.Response(200, json=[]))
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_hidden_comments(client)
    assert result == []


@respx.mock
def test_get_hidden_comments_unrecognised_envelope_returns_empty(
    fake_cookies, fake_publication_url, isolated_config, authed_env, capsys
):
    """Unrecognised dict envelope → returns [] and warns to stderr."""
    respx.get(_HIDDEN_URL).mock(
        return_value=httpx.Response(200, json={"unknown_key": "surprise"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_hidden_comments(client)
    assert result == []
    captured = capsys.readouterr()
    assert "Warning" in captured.err
    assert "unrecognised" in captured.err


@respx.mock
def test_get_hidden_comments_client_side_since_filter(
    fake_cookies, fake_publication_url, isolated_config, authed_env
):
    """Items older than --since cutoff are filtered out client-side."""
    old_item = {**_SAMPLE_ITEM, "id": 1, "hidden_at": "2026-10-10T00:00:00Z"}
    new_item = {**_SAMPLE_ITEM, "id": 2, "hidden_at": "2026-10-15T12:00:00Z"}
    respx.get(_HIDDEN_URL).mock(
        return_value=httpx.Response(200, json=[old_item, new_item])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    # "since" set to 2026-10-12 — old_item (Oct 10) should be dropped
    result = get_hidden_comments(client, since="2026-10-12T00:00:00Z")
    ids = [r["id"] for r in result]
    assert 1 not in ids
    assert 2 in ids


@respx.mock
def test_get_hidden_comments_keeps_items_with_no_timestamp(
    fake_cookies, fake_publication_url, isolated_config, authed_env
):
    """Items with no hidden_at timestamp are kept (cannot exclude what we can't date)."""
    no_ts = {**_SAMPLE_ITEM, "id": 99, "hidden_at": None}
    respx.get(_HIDDEN_URL).mock(
        return_value=httpx.Response(200, json=[no_ts])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_hidden_comments(client, since="24h")
    assert any(r["id"] == 99 for r in result)


@respx.mock
def test_get_hidden_comments_raises_on_404(
    fake_cookies, fake_publication_url, isolated_config, authed_env
):
    """404 from the API (unverified endpoint) raises SubstackApiError."""
    from substack_cli.client import SubstackApiError
    respx.get(_HIDDEN_URL).mock(return_value=httpx.Response(404, json={"error": "not found"}))
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError):
        get_hidden_comments(client)


def test_get_hidden_comments_raises_on_bad_since(
    fake_cookies, fake_publication_url, isolated_config, authed_env
):
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(ValueError, match="not a recognised shorthand"):
        get_hidden_comments(client, since="last-week")


# ---------------------------------------------------------------------------
# CLI — happy path
# ---------------------------------------------------------------------------


@respx.mock
def test_moderation_hidden_cli_happy_path(
    isolated_config, authed_env, cli_runner
):
    """CLI `moderation hidden` exits 0 and returns JSON list."""
    import substack_cli.moderation  # noqa: F401 — register commands

    respx.get(_HIDDEN_URL).mock(
        return_value=httpx.Response(200, json=[_SAMPLE_ITEM])
    )

    result = cli_runner.invoke(app, ["moderation", "hidden"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["id"] == 42
    assert data[0]["reason"] == "reply_rule:whatsapp"


@respx.mock
def test_moderation_hidden_cli_empty_list(
    isolated_config, authed_env, cli_runner
):
    """Empty AutoMod queue exits 0 and returns []."""
    import substack_cli.moderation  # noqa: F401

    respx.get(_HIDDEN_URL).mock(return_value=httpx.Response(200, json=[]))

    result = cli_runner.invoke(app, ["moderation", "hidden"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


@respx.mock
def test_moderation_hidden_cli_since_flag(
    isolated_config, authed_env, cli_runner
):
    """--since flag is forwarded; only matching items are returned."""
    import substack_cli.moderation  # noqa: F401

    old = {**_SAMPLE_ITEM, "id": 1, "hidden_at": "2026-10-10T00:00:00Z"}
    recent = {**_SAMPLE_ITEM, "id": 2, "hidden_at": "2026-10-15T09:00:00Z"}
    respx.get(_HIDDEN_URL).mock(return_value=httpx.Response(200, json=[old, recent]))

    result = cli_runner.invoke(app, ["moderation", "hidden", "--since", "2026-10-12T00:00:00Z"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    ids = [d["id"] for d in data]
    assert 1 not in ids
    assert 2 in ids


@respx.mock
def test_moderation_hidden_cli_since_shorthand(
    isolated_config, authed_env, cli_runner
):
    """--since 24h shorthand is accepted without error."""
    import substack_cli.moderation  # noqa: F401

    respx.get(_HIDDEN_URL).mock(return_value=httpx.Response(200, json=[]))
    result = cli_runner.invoke(app, ["moderation", "hidden", "--since", "24h"])
    assert result.exit_code == 0, result.output


@respx.mock
def test_moderation_hidden_cli_limit_flag(
    isolated_config, authed_env, cli_runner
):
    """--limit flag is forwarded to the API call."""
    import substack_cli.moderation  # noqa: F401

    route = respx.get(_HIDDEN_URL).mock(return_value=httpx.Response(200, json=[]))
    result = cli_runner.invoke(app, ["moderation", "hidden", "--limit", "25"])
    assert result.exit_code == 0, result.output
    assert "limit=25" in str(route.calls[0].request.url)


# ---------------------------------------------------------------------------
# CLI — error paths
# ---------------------------------------------------------------------------


@respx.mock
def test_moderation_hidden_cli_404_exits_nonzero(
    isolated_config, authed_env, cli_runner
):
    """404 from the API (unverified endpoint) exits non-zero with error JSON."""
    import substack_cli.moderation  # noqa: F401

    respx.get(_HIDDEN_URL).mock(return_value=httpx.Response(404, json={"error": "not found"}))

    result = cli_runner.invoke(app, ["moderation", "hidden"])
    assert result.exit_code != 0


@respx.mock
def test_moderation_hidden_cli_401_exits_nonzero(
    isolated_config, authed_env, cli_runner
):
    """401 from the API exits non-zero."""
    import substack_cli.moderation  # noqa: F401

    respx.get(_HIDDEN_URL).mock(return_value=httpx.Response(401, json={"error": "Unauthorized"}))
    result = cli_runner.invoke(app, ["moderation", "hidden"])
    assert result.exit_code != 0


def test_moderation_hidden_cli_no_auth_exits_nonzero(
    isolated_config, cli_runner
):
    """Without credentials, `moderation hidden` exits non-zero (AuthError)."""
    import substack_cli.moderation  # noqa: F401

    result = cli_runner.invoke(app, ["moderation", "hidden"])
    assert result.exit_code != 0


def test_moderation_hidden_cli_bad_since_exits_nonzero(
    isolated_config, authed_env, cli_runner
):
    """Invalid --since value exits non-zero with error message."""
    import substack_cli.moderation  # noqa: F401

    result = cli_runner.invoke(app, ["moderation", "hidden", "--since", "yesterday"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Endpoint path constant is isolated (patchable)
# ---------------------------------------------------------------------------


def test_automod_hidden_path_constant_is_a_string():
    assert isinstance(AUTOMOD_HIDDEN_PATH, str)
    assert AUTOMOD_HIDDEN_PATH.startswith("/api/")


def test_automod_hidden_path_used_in_get_hidden_comments(
    fake_cookies, fake_publication_url, isolated_config, authed_env, monkeypatch
):
    """Patching AUTOMOD_HIDDEN_PATH redirects the GET to the new path."""
    custom_path = "/api/v1/test/custom_hidden"
    monkeypatch.setattr(mh, "AUTOMOD_HIDDEN_PATH", custom_path)

    with respx.mock:
        route = respx.get(f"{_FAKE_PUB}{custom_path}").mock(
            return_value=httpx.Response(200, json=[])
        )
        client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
        # Must re-call so the monkeypatched value is read
        result = mh.get_hidden_comments(client)
        assert route.called
        assert result == []
