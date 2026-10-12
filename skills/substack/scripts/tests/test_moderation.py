"""Tests for substack_cli.moderation — report, ban, and read commands."""
import json

import httpx
import pytest
import respx

from substack_cli.app import comments_app
from substack_cli.client import SubstackClient, SUBSTACK_COM
from substack_cli.moderation_core import (
    ban_commenter,
    get_delete_reasons,
    get_report_categories,
    get_user_bans,
    report_comment,
    report_user_profile,
    VALID_CATEGORIES,
    VALID_EXPIRIES,
    VALID_VISIBILITIES,
    build_report_body,
    build_profile_report_body,
    build_ban_body,
)

# ---------------------------------------------------------------------------
# report_comment — unit tests
# ---------------------------------------------------------------------------


@respx.mock
def test_report_comment_posts_correct_body(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    route = respx.post(
        f"{fake_publication_url}/api/v1/comment/111/report"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))

    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    report_comment(
        client,
        comment_id=111,
        category="spam",
        details="scam account",
        reported_to_substack=True,
        publication_id=999,
        report_context=None,
    )

    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["reportCategory"] == "spam"
    assert body["details"] == "scam account"
    assert body["reportedToSubstack"] is True
    assert body["publication_id"] == 999
    assert "report_context" not in body


@respx.mock
def test_report_comment_omits_publication_id_when_none(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    route = respx.post(
        f"{fake_publication_url}/api/v1/comment/222/report"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))

    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    report_comment(
        client,
        comment_id=222,
        category="impersonation",
        details="",
        reported_to_substack=False,
        publication_id=None,
        report_context=None,
    )

    body = json.loads(route.calls[0].request.content)
    assert "publication_id" not in body
    assert body["reportedToSubstack"] is False
    assert body["reportCategory"] == "impersonation"


@respx.mock
def test_report_comment_passes_report_context_json(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    route = respx.post(
        f"{fake_publication_url}/api/v1/comment/333/report"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))

    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    report_comment(
        client,
        comment_id=333,
        category="spam",
        details="",
        reported_to_substack=True,
        publication_id=None,
        report_context='{"surface": "post"}',
    )

    body = json.loads(route.calls[0].request.content)
    assert body["report_context"] == {"surface": "post"}


def test_report_comment_requires_write_gate(
    fake_cookies, fake_publication_url, isolated_config, authed_env
):
    """No write gate → ValueError before any HTTP call fires."""
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(ValueError, match="SUBSTACK_ENABLE_WRITE"):
        report_comment(client, 1, "spam", "", True, None, None)


# ---------------------------------------------------------------------------
# report_user_profile — unit tests
# ---------------------------------------------------------------------------


@respx.mock
def test_report_user_profile_uses_substack_com_host(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    route = respx.post(
        f"{SUBSTACK_COM}/api/v1/users/456/profile/report"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))

    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    report_user_profile(
        client,
        user_id=456,
        category="impersonation",
        details="impersonating Charlie Garcia",
        reported_to_substack=True,
        report_context=None,
    )

    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["reportCategory"] == "impersonation"
    assert body["surface"] == "profile"
    assert body["reportedToSubstack"] is True
    assert "report_context" not in body


@respx.mock
def test_report_user_profile_includes_no_to_substack_false(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    route = respx.post(
        f"{SUBSTACK_COM}/api/v1/users/789/profile/report"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))

    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    report_user_profile(
        client,
        user_id=789,
        category="spam",
        details="spam bot",
        reported_to_substack=False,
        report_context=None,
    )

    body = json.loads(route.calls[0].request.content)
    assert body["reportedToSubstack"] is False


def test_report_user_profile_requires_write_gate(
    fake_cookies, fake_publication_url, isolated_config, authed_env
):
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(ValueError, match="SUBSTACK_ENABLE_WRITE"):
        report_user_profile(client, 1, "spam", "", True, None)


# ---------------------------------------------------------------------------
# ban_commenter — unit tests
# ---------------------------------------------------------------------------


@respx.mock
def test_ban_commenter_posts_correct_body(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    route = respx.post(
        f"{fake_publication_url}/api/v1/comment_ban/555"
    ).mock(return_value=httpx.Response(200, json={"bans": []}))

    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    ban_commenter(
        client,
        comment_id=555,
        user_id=777,
        expiry="1y",
        comment_visibility="visible",
    )

    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["user_id"] == 777
    assert body["expiry"] == "1y"
    assert body["commentVisibility"] == "visible"


@respx.mock
def test_ban_commenter_delete_all_comments_visibility(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    route = respx.post(
        f"{fake_publication_url}/api/v1/comment_ban/600"
    ).mock(return_value=httpx.Response(200, json={"bans": []}))

    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    ban_commenter(
        client,
        comment_id=600,
        user_id=888,
        expiry="1000y",
        comment_visibility="delete_all_comments",
    )

    body = json.loads(route.calls[0].request.content)
    assert body["commentVisibility"] == "delete_all_comments"


def test_ban_commenter_rejects_invalid_visibility(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(ValueError, match="Invalid commentVisibility"):
        ban_commenter(client, 1, 2, "1y", "hide")


def test_ban_commenter_requires_write_gate(
    fake_cookies, fake_publication_url, isolated_config, authed_env
):
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(ValueError, match="SUBSTACK_ENABLE_WRITE"):
        ban_commenter(client, 1, 2, "1y", "visible")


# ---------------------------------------------------------------------------
# get_user_bans — unit test (read-only)
# ---------------------------------------------------------------------------


@respx.mock
def test_get_user_bans_calls_correct_endpoint(
    fake_cookies, fake_publication_url, isolated_config, authed_env
):
    route = respx.get(
        f"{fake_publication_url}/api/v1/user/123/bans"
    ).mock(return_value=httpx.Response(200, json={"bans": [], "sub": None}))

    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_user_bans(client, 123)

    assert route.called
    assert result == {"bans": [], "sub": None}


# ---------------------------------------------------------------------------
# get_report_categories — unit test (read-only, uses substack.com host)
# ---------------------------------------------------------------------------


@respx.mock
def test_get_report_categories_uses_substack_com(
    fake_cookies, fake_publication_url, isolated_config, authed_env
):
    route = respx.get(
        f"{SUBSTACK_COM}/api/v1/reporting/flows"
    ).mock(return_value=httpx.Response(200, json=[{"id": "spam", "label": "Spam"}]))

    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_report_categories(client)

    assert route.called
    assert result[0]["id"] == "spam"


# ---------------------------------------------------------------------------
# get_delete_reasons — unit test (read-only)
# ---------------------------------------------------------------------------


@respx.mock
def test_get_delete_reasons_calls_correct_endpoint(
    fake_cookies, fake_publication_url, isolated_config, authed_env
):
    route = respx.get(
        f"{fake_publication_url}/api/v1/comment/moderation/delete_reasons"
    ).mock(return_value=httpx.Response(200, json=["spam", "slop", "clickbait"]))

    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_delete_reasons(client)

    assert route.called
    assert "spam" in result


# ---------------------------------------------------------------------------
# CLI dry-run behaviour
# ---------------------------------------------------------------------------


def test_comments_report_dry_run_prints_request(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    """Dry-run (no --no-dry-run) must print the request dict and exit 0."""
    from substack_cli.moderation import moderation_app  # noqa — ensure import wired

    result = cli_runner.invoke(
        comments_app,
        ["report", "111", "--category", "spam", "--details", "test scam"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["dry_run"] is True
    assert data["method"] == "POST"
    assert "111" in data["url"]
    assert data["body"]["reportCategory"] == "spam"
    assert data["body"]["details"] == "test scam"
    assert data["body"]["reportedToSubstack"] is True


def test_comments_report_dry_run_zero_http_calls(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    """Dry-run must issue ZERO HTTP requests — no network call at all."""
    from substack_cli.moderation import moderation_app  # noqa

    with respx.mock(assert_all_called=False) as router:
        result = cli_runner.invoke(
            comments_app,
            ["report", "111", "--category", "spam"],
        )
    assert result.exit_code == 0, result.output
    # No routes were registered, so any actual request would have raised.
    assert len(router.calls) == 0


def test_comments_report_dry_run_with_publication_id(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    """--publication-id is echoed in the dry-run body."""
    from substack_cli.moderation import moderation_app  # noqa

    result = cli_runner.invoke(
        comments_app,
        ["report", "111", "--category", "spam", "--publication-id", "42"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["dry_run"] is True
    assert data["body"]["publication_id"] == 42


@respx.mock
def test_comments_report_live_sends_publication_id(
    fake_cookies, fake_publication_url, isolated_config, authed_env,
    write_enabled_env, cli_runner,
):
    """--publication-id is sent in the live POST body."""
    route = respx.post(
        f"{fake_publication_url}/api/v1/comment/111/report"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))

    result = cli_runner.invoke(
        comments_app,
        [
            "report", "111",
            "--category", "spam",
            "--publication-id", "99",
            "--no-dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["publication_id"] == 99


def test_comments_report_user_dry_run_uses_substack_com(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    result = cli_runner.invoke(
        comments_app,
        ["report-user", "456", "--category", "impersonation", "--details", "fake account"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["dry_run"] is True
    assert SUBSTACK_COM in data["url"]
    assert "456" in data["url"]
    assert data["body"]["reportCategory"] == "impersonation"
    assert data["body"]["surface"] == "profile"


def test_comments_ban_dry_run_prints_request(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    result = cli_runner.invoke(
        comments_app,
        [
            "ban", "555",
            "--user-id", "777",
            "--expiry", "1y",
            "--visibility", "visible",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["dry_run"] is True
    assert "555" in data["url"]
    assert data["body"]["user_id"] == 777
    assert data["body"]["expiry"] == "1y"
    assert data["body"]["commentVisibility"] == "visible"


def test_comments_ban_dry_run_delete_all_visibility(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    result = cli_runner.invoke(
        comments_app,
        [
            "ban", "600",
            "--user-id", "888",
            "--expiry", "1000y",
            "--visibility", "delete_all_comments",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["body"]["commentVisibility"] == "delete_all_comments"


def test_comments_report_blocked_without_write_gate(
    isolated_config, authed_env, cli_runner
):
    """No SUBSTACK_ENABLE_WRITE → non-zero exit."""
    result = cli_runner.invoke(
        comments_app,
        ["report", "111", "--category", "spam"],
    )
    assert result.exit_code != 0


def test_comments_ban_blocked_without_write_gate(
    isolated_config, authed_env, cli_runner
):
    result = cli_runner.invoke(
        comments_app,
        ["ban", "555", "--user-id", "777", "--expiry", "1y"],
    )
    assert result.exit_code != 0


def test_comments_report_rejects_bad_category(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    result = cli_runner.invoke(
        comments_app,
        ["report", "111", "--category", "not_a_real_category"],
    )
    assert result.exit_code != 0


def test_comments_ban_rejects_bad_visibility(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    result = cli_runner.invoke(
        comments_app,
        [
            "ban", "555",
            "--user-id", "777",
            "--expiry", "1y",
            "--visibility", "bad_value",
        ],
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Enum coverage
# ---------------------------------------------------------------------------


def test_valid_categories_set():
    assert "spam" in VALID_CATEGORIES
    assert "impersonation" in VALID_CATEGORIES
    assert len(VALID_CATEGORIES) == 8


def test_valid_visibilities_set():
    assert "visible" in VALID_VISIBILITIES
    assert "delete_this_comment" in VALID_VISIBILITIES
    assert "delete_all_comments" in VALID_VISIBILITIES
    assert len(VALID_VISIBILITIES) == 3


def test_valid_expiries_match_bundle():
    assert VALID_EXPIRIES == {"1d", "30d", "90d", "1y", "1000y"}


def test_ban_commenter_rejects_iso_expiry(monkeypatch):
    """Substack's ban modal posts duration codes, not datetimes."""
    from unittest.mock import MagicMock
    import substack_cli.moderation_core as m
    monkeypatch.setattr(m, "is_write_enabled", lambda: True)
    client = MagicMock()
    with pytest.raises(ValueError, match="Invalid expiry"):
        m.ban_commenter(client, 1, 2, "2027-10-12T00:00:00Z", "visible")
    client.post.assert_not_called()


def test_comments_ban_cli_rejects_bad_expiry(monkeypatch):
    from typer.testing import CliRunner
    from substack_cli.app import app
    monkeypatch.setenv("SUBSTACK_ENABLE_WRITE", "true")
    result = CliRunner().invoke(
        app, ["comments", "ban", "555", "--user-id", "777", "--expiry", "forever"]
    )
    assert result.exit_code != 0
    assert "Invalid --expiry" in result.output


# ---------------------------------------------------------------------------
# Body builder unit tests (pure functions, no HTTP)
# ---------------------------------------------------------------------------


def test_build_report_body_with_publication_id():
    body = build_report_body("spam", "details", True, 42, None)
    assert body["publication_id"] == 42
    assert body["reportCategory"] == "spam"
    assert "report_context" not in body


def test_build_report_body_omits_publication_id_when_none():
    body = build_report_body("hate", "", False, None, None)
    assert "publication_id" not in body


def test_build_report_body_parses_report_context_json():
    body = build_report_body("spam", "", True, None, '{"k": 1}')
    assert body["report_context"] == {"k": 1}


def test_build_report_body_raises_on_bad_json():
    with pytest.raises(ValueError, match="not valid JSON"):
        build_report_body("spam", "", True, None, "not-json")


def test_build_profile_report_body_has_surface():
    body = build_profile_report_body("impersonation", "fake", True, None)
    assert body["surface"] == "profile"
    assert body["reportCategory"] == "impersonation"


def test_build_ban_body_correct_keys():
    body = build_ban_body(777, "1y", "visible")
    assert body == {"user_id": 777, "expiry": "1y", "commentVisibility": "visible"}
