"""Tests for --surface notes in `comments report` (issue #26).

Note replies live on substack.com (host A), not on the publication host.
These tests assert:
  - dry-run URL uses SUBSTACK_COM for --surface notes
  - dry-run URL uses the publication host for --surface publication (default)
  - dry-run fires zero HTTP calls in both cases
  - live path routes to host A via report_note_reply
  - write-gate is enforced for notes surface
  - invalid --surface is rejected
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from substack_cli.app import comments_app
from substack_cli.client import SUBSTACK_COM, SubstackClient
from substack_cli.moderation_core import report_note_reply, VALID_CATEGORIES


# ---------------------------------------------------------------------------
# report_note_reply — core unit tests
# ---------------------------------------------------------------------------


@respx.mock
def test_report_note_reply_posts_to_substack_com(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    """report_note_reply must POST to substack.com, not the publication host."""
    route = respx.post(
        f"{SUBSTACK_COM}/api/v1/comment/9001/report"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))

    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    report_note_reply(
        client,
        comment_id=9001,
        category="impersonation",
        details="fake Charlie Garcia reply under a note",
        reported_to_substack=True,
        publication_id=None,
        report_context=None,
    )

    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["reportCategory"] == "impersonation"
    assert body["details"] == "fake Charlie Garcia reply under a note"
    assert body["reportedToSubstack"] is True
    assert "publication_id" not in body


@respx.mock
def test_report_note_reply_does_not_hit_publication_host(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    """report_note_reply must NOT call the publication subdomain."""
    pub_route = respx.post(
        f"{fake_publication_url}/api/v1/comment/9001/report"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))
    respx.post(
        f"{SUBSTACK_COM}/api/v1/comment/9001/report"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))

    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    report_note_reply(client, 9001, "spam", "", True, None, None)

    assert not pub_route.called


@respx.mock
def test_report_note_reply_includes_publication_id_when_given(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    route = respx.post(
        f"{SUBSTACK_COM}/api/v1/comment/9002/report"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))

    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    report_note_reply(client, 9002, "spam", "", True, 42, None)

    body = json.loads(route.calls[0].request.content)
    assert body["publication_id"] == 42


def test_report_note_reply_requires_write_gate(
    fake_cookies, fake_publication_url, isolated_config, authed_env
):
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(ValueError, match="SUBSTACK_ENABLE_WRITE"):
        report_note_reply(client, 9001, "spam", "", True, None, None)


def test_report_note_reply_rejects_invalid_category(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(ValueError, match="Invalid category"):
        report_note_reply(client, 9001, "not_real", "", True, None, None)


# ---------------------------------------------------------------------------
# CLI dry-run: --surface notes → SUBSTACK_COM host
# ---------------------------------------------------------------------------


def test_comments_report_surface_notes_dry_run_uses_substack_com(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    """--surface notes dry-run URL must contain substack.com."""
    import substack_cli.moderation  # noqa: F401 — register CLI commands

    result = cli_runner.invoke(
        comments_app,
        [
            "report", "9001",
            "--category", "impersonation",
            "--details", "scam reply on a note",
            "--surface", "notes",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["dry_run"] is True
    assert data["method"] == "POST"
    assert SUBSTACK_COM in data["url"]
    assert "9001" in data["url"]
    assert data["body"]["reportCategory"] == "impersonation"
    assert data["body"]["details"] == "scam reply on a note"


def test_comments_report_surface_notes_dry_run_zero_http(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    """--surface notes dry-run must fire zero HTTP requests."""
    import substack_cli.moderation  # noqa: F401

    with respx.mock(assert_all_called=False) as router:
        result = cli_runner.invoke(
            comments_app,
            ["report", "9001", "--category", "spam", "--surface", "notes"],
        )
    assert result.exit_code == 0, result.output
    assert len(router.calls) == 0


# ---------------------------------------------------------------------------
# CLI dry-run: --surface publication (default) → publication host
# ---------------------------------------------------------------------------


def test_comments_report_surface_publication_dry_run_uses_pub_host(
    isolated_config, authed_env, write_enabled_env, cli_runner, fake_publication_url
):
    """Default (--surface publication) dry-run URL must use the publication host."""
    import substack_cli.moderation  # noqa: F401

    result = cli_runner.invoke(
        comments_app,
        ["report", "111", "--category", "spam"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["dry_run"] is True
    assert SUBSTACK_COM not in data["url"] or "charliepgarcia" in data["url"]
    assert "111" in data["url"]


def test_comments_report_surface_publication_explicit_dry_run(
    isolated_config, authed_env, write_enabled_env, cli_runner, fake_publication_url
):
    """Explicit --surface publication behaves identically to the default."""
    import substack_cli.moderation  # noqa: F401

    result = cli_runner.invoke(
        comments_app,
        ["report", "111", "--category", "spam", "--surface", "publication"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["dry_run"] is True
    assert "111" in data["url"]
    assert fake_publication_url in data["url"]


# ---------------------------------------------------------------------------
# CLI dry-run: notes surface does NOT include publication host
# ---------------------------------------------------------------------------


def test_comments_report_surface_notes_url_not_publication_host(
    isolated_config, authed_env, write_enabled_env, cli_runner, fake_publication_url
):
    """--surface notes URL must NOT contain the publication subdomain."""
    import substack_cli.moderation  # noqa: F401

    result = cli_runner.invoke(
        comments_app,
        ["report", "9001", "--category", "spam", "--surface", "notes"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert fake_publication_url not in data["url"]
    assert SUBSTACK_COM in data["url"]


# ---------------------------------------------------------------------------
# CLI live path: --surface notes routes to host A
# ---------------------------------------------------------------------------


@respx.mock
def test_comments_report_surface_notes_live_posts_to_substack_com(
    fake_cookies, fake_publication_url, isolated_config, authed_env,
    write_enabled_env, cli_runner,
):
    """Live --surface notes must POST to substack.com, not publication host."""
    pub_route = respx.post(
        f"{fake_publication_url}/api/v1/comment/9001/report"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))
    notes_route = respx.post(
        f"{SUBSTACK_COM}/api/v1/comment/9001/report"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))

    result = cli_runner.invoke(
        comments_app,
        [
            "report", "9001",
            "--category", "impersonation",
            "--surface", "notes",
            "--no-dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert notes_route.called
    assert not pub_route.called
    body = json.loads(notes_route.calls[0].request.content)
    assert body["reportCategory"] == "impersonation"


# ---------------------------------------------------------------------------
# CLI validation: invalid --surface
# ---------------------------------------------------------------------------


def test_comments_report_invalid_surface_rejected(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    """An unrecognised --surface value must exit non-zero."""
    import substack_cli.moderation  # noqa: F401

    result = cli_runner.invoke(
        comments_app,
        ["report", "111", "--category", "spam", "--surface", "chat"],
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# CLI validation: write-gate enforced for notes surface
# ---------------------------------------------------------------------------


def test_comments_report_notes_blocked_without_write_gate(
    isolated_config, authed_env, cli_runner
):
    """Without SUBSTACK_ENABLE_WRITE, --surface notes must exit non-zero."""
    import substack_cli.moderation  # noqa: F401

    result = cli_runner.invoke(
        comments_app,
        ["report", "9001", "--category", "spam", "--surface", "notes"],
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# report_note_reply — report_context forwarded to POST body
# ---------------------------------------------------------------------------


@respx.mock
def test_report_note_reply_includes_report_context_decoded(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    """report_note_reply with a non-None report_context must include it decoded in the POST body."""
    route = respx.post(
        f"{SUBSTACK_COM}/api/v1/comment/9003/report"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))

    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    report_note_reply(
        client,
        comment_id=9003,
        category="spam",
        details="",
        reported_to_substack=True,
        publication_id=None,
        report_context='{"foo": 1}',
    )

    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["report_context"] == {"foo": 1}
