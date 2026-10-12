"""Unit tests for issue #19 fixes (no respx required).

Three fix areas:
  1. Write-gate checked FIRST in CLI commands (report, report-user, ban).
  2. Core functions validate category (report_comment, report_user_profile).
  3. Invalid --report-context JSON raises ValueError instead of silent passthrough.
"""
import json
import pytest
from unittest.mock import MagicMock

# Register CLI commands so Typer wiring is in place.
import substack_cli.moderation  # noqa: F401

from substack_cli.app import comments_app
from substack_cli.moderation_core import (
    VALID_CATEGORIES,
    build_report_body,
    build_profile_report_body,
    report_comment,
    report_user_profile,
)


# ---------------------------------------------------------------------------
# Fix 1 — CLI write-gate checked BEFORE enum validation
# ---------------------------------------------------------------------------


def test_comments_report_write_gate_beats_bad_category(
    isolated_config, authed_env, cli_runner
):
    """Gate off + bad category → write-gate error, not enum error."""
    # SUBSTACK_ENABLE_WRITE is NOT set (isolated_config clears it)
    result = cli_runner.invoke(
        comments_app,
        ["report", "111", "--category", "totally_invalid"],
    )
    assert result.exit_code != 0
    err = json.loads(result.output)
    assert "SUBSTACK_ENABLE_WRITE" in err["message"]
    assert "totally_invalid" not in err["message"]


def test_comments_report_user_write_gate_beats_bad_category(
    isolated_config, authed_env, cli_runner
):
    """Gate off + bad category → write-gate error for report-user."""
    result = cli_runner.invoke(
        comments_app,
        ["report-user", "456", "--category", "totally_invalid"],
    )
    assert result.exit_code != 0
    err = json.loads(result.output)
    assert "SUBSTACK_ENABLE_WRITE" in err["message"]
    assert "totally_invalid" not in err["message"]


def test_comments_ban_write_gate_beats_bad_visibility(
    isolated_config, authed_env, cli_runner
):
    """Gate off + bad visibility → write-gate error for ban."""
    result = cli_runner.invoke(
        comments_app,
        ["ban", "555", "--user-id", "777", "--expiry", "1y", "--visibility", "bad_vis"],
    )
    assert result.exit_code != 0
    err = json.loads(result.output)
    assert "SUBSTACK_ENABLE_WRITE" in err["message"]
    assert "bad_vis" not in err["message"]


def test_comments_ban_write_gate_beats_bad_expiry(
    isolated_config, authed_env, cli_runner
):
    """Gate off + bad expiry → write-gate error for ban."""
    result = cli_runner.invoke(
        comments_app,
        ["ban", "555", "--user-id", "777", "--expiry", "forever"],
    )
    assert result.exit_code != 0
    err = json.loads(result.output)
    assert "SUBSTACK_ENABLE_WRITE" in err["message"]
    assert "forever" not in err["message"]


# Sanity: gate ON + bad category → enum error (not write-gate error)
def test_comments_report_enum_error_when_gate_on(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    result = cli_runner.invoke(
        comments_app,
        ["report", "111", "--category", "totally_invalid"],
    )
    assert result.exit_code != 0
    err = json.loads(result.output)
    assert "totally_invalid" in err["message"]
    assert "SUBSTACK_ENABLE_WRITE" not in err["message"]


# ---------------------------------------------------------------------------
# Fix 2 — Core functions validate category
# ---------------------------------------------------------------------------


def test_report_comment_core_rejects_invalid_category(monkeypatch):
    """report_comment raises ValueError for bad category (write gate on)."""
    import substack_cli.moderation_core as m
    monkeypatch.setattr(m, "is_write_enabled", lambda: True)
    client = MagicMock()
    with pytest.raises(ValueError, match="Invalid category"):
        report_comment(client, 1, "not_a_category", "", True, None, None)
    client.post.assert_not_called()


def test_report_comment_core_accepts_valid_category(monkeypatch):
    """report_comment does NOT raise for a valid category."""
    import substack_cli.moderation_core as m
    monkeypatch.setattr(m, "is_write_enabled", lambda: True)
    client = MagicMock()
    client.post.return_value = {"ok": True}
    for cat in VALID_CATEGORIES:
        client.reset_mock()
        result = report_comment(client, 1, cat, "", True, None, None)
        assert result == {"ok": True}
        client.post.assert_called_once()


def test_report_user_profile_core_rejects_invalid_category(monkeypatch):
    """report_user_profile raises ValueError for bad category (write gate on)."""
    import substack_cli.moderation_core as m
    monkeypatch.setattr(m, "is_write_enabled", lambda: True)
    client = MagicMock()
    with pytest.raises(ValueError, match="Invalid category"):
        report_user_profile(client, 1, "bad_cat", "", True, None)
    client.post.assert_not_called()


def test_report_user_profile_core_accepts_valid_category(monkeypatch):
    """report_user_profile does NOT raise for valid categories."""
    import substack_cli.moderation_core as m
    monkeypatch.setattr(m, "is_write_enabled", lambda: True)
    client = MagicMock()
    client.post.return_value = {"ok": True}
    for cat in VALID_CATEGORIES:
        client.reset_mock()
        result = report_user_profile(client, 1, cat, "", True, None)
        assert result == {"ok": True}


# ---------------------------------------------------------------------------
# Fix 3 — Invalid --report-context JSON raises ValueError
# ---------------------------------------------------------------------------


def test_build_report_body_raises_on_invalid_json():
    """build_report_body raises ValueError (not silent passthrough) for bad JSON."""
    with pytest.raises(ValueError, match="not valid JSON"):
        build_report_body("spam", "", True, None, "not-json")


def test_build_report_body_raises_on_partial_json():
    with pytest.raises(ValueError, match="not valid JSON"):
        build_report_body("spam", "", True, None, '{"key": }')


def test_build_profile_report_body_raises_on_invalid_json():
    """build_profile_report_body raises ValueError for bad JSON."""
    with pytest.raises(ValueError, match="not valid JSON"):
        build_profile_report_body("spam", "", True, "not-json")


def test_build_profile_report_body_raises_on_partial_json():
    with pytest.raises(ValueError, match="not valid JSON"):
        build_profile_report_body("spam", "", True, "[1, 2,")


def test_build_report_body_accepts_valid_json():
    """Valid JSON string is parsed to a dict, not forwarded as string."""
    body = build_report_body("spam", "", True, None, '{"surface": "post"}')
    assert body["report_context"] == {"surface": "post"}


def test_build_profile_report_body_accepts_valid_json():
    body = build_profile_report_body("spam", "", True, '{"surface": "comment"}')
    assert body["report_context"] == {"surface": "comment"}


def test_build_report_body_no_report_context_when_none():
    """None report_context → key absent (unchanged behaviour)."""
    body = build_report_body("spam", "", True, None, None)
    assert "report_context" not in body


def test_build_profile_report_body_no_report_context_when_none():
    body = build_profile_report_body("spam", "", True, None)
    assert "report_context" not in body


def test_report_comment_core_raises_on_bad_report_context(monkeypatch):
    """report_comment propagates ValueError from build_report_body for bad JSON."""
    import substack_cli.moderation_core as m
    monkeypatch.setattr(m, "is_write_enabled", lambda: True)
    client = MagicMock()
    with pytest.raises(ValueError, match="not valid JSON"):
        report_comment(client, 1, "spam", "", True, None, "bad-json")
    client.post.assert_not_called()


def test_report_user_profile_core_raises_on_bad_report_context(monkeypatch):
    """report_user_profile propagates ValueError from build_profile_report_body."""
    import substack_cli.moderation_core as m
    monkeypatch.setattr(m, "is_write_enabled", lambda: True)
    client = MagicMock()
    with pytest.raises(ValueError, match="not valid JSON"):
        report_user_profile(client, 1, "spam", "", True, "bad-json")
    client.post.assert_not_called()
