"""CLI write-command coverage for moderation: dry-run zero-HTTP and write-gate tests.

Covers items 1 and 2 from issue #20:
  1. Zero-HTTP dry-run assertions for `comments ban` and `comments report-user`.
  2. CLI-layer write-gate refusal test for `comments report-user`.

All enum values used are VALID so these tests pass under both write-gate-first
and enum-validation-first orderings (see issue #19 sibling branch).
"""
from __future__ import annotations

import json

import respx

from substack_cli.app import comments_app
from substack_cli.client import SUBSTACK_COM

# ---------------------------------------------------------------------------
# comments ban — zero-HTTP dry-run
# ---------------------------------------------------------------------------


def test_comments_ban_dry_run_zero_http_calls(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    """Dry-run for `comments ban` must issue ZERO HTTP requests."""
    import substack_cli.moderation  # noqa: F401 — ensure CLI commands are registered

    with respx.mock(assert_all_called=False) as router:
        result = cli_runner.invoke(
            comments_app,
            ["ban", "555", "--user-id", "777", "--expiry", "1y"],
        )
    assert result.exit_code == 0, result.output
    assert len(router.calls) == 0


def test_comments_ban_dry_run_body_contents(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    """Dry-run output for `comments ban` contains the correct body fields."""
    import substack_cli.moderation  # noqa: F401

    with respx.mock(assert_all_called=False):
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
    assert data["method"] == "POST"
    assert "555" in data["url"]
    assert data["body"]["user_id"] == 777
    assert data["body"]["expiry"] == "1y"
    assert data["body"]["commentVisibility"] == "visible"


def test_comments_ban_dry_run_delete_all_zero_http(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    """Dry-run with delete_all_comments visibility also fires no HTTP."""
    import substack_cli.moderation  # noqa: F401

    with respx.mock(assert_all_called=False) as router:
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
    assert len(router.calls) == 0
    data = json.loads(result.output)
    assert data["body"]["commentVisibility"] == "delete_all_comments"


# ---------------------------------------------------------------------------
# comments report-user — zero-HTTP dry-run
# ---------------------------------------------------------------------------


def test_comments_report_user_dry_run_zero_http_calls(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    """Dry-run for `comments report-user` must issue ZERO HTTP requests."""
    import substack_cli.moderation  # noqa: F401

    with respx.mock(assert_all_called=False) as router:
        result = cli_runner.invoke(
            comments_app,
            ["report-user", "456", "--category", "spam"],
        )
    assert result.exit_code == 0, result.output
    assert len(router.calls) == 0


def test_comments_report_user_dry_run_body_contents(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    """Dry-run output for `comments report-user` has correct body and URL."""
    import substack_cli.moderation  # noqa: F401

    with respx.mock(assert_all_called=False):
        result = cli_runner.invoke(
            comments_app,
            [
                "report-user", "456",
                "--category", "impersonation",
                "--details", "fake account",
            ],
        )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["dry_run"] is True
    assert data["method"] == "POST"
    assert SUBSTACK_COM in data["url"]
    assert "456" in data["url"]
    assert data["body"]["reportCategory"] == "impersonation"
    assert data["body"]["surface"] == "profile"
    assert data["body"]["details"] == "fake account"


def test_comments_report_user_dry_run_all_categories_zero_http(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    """Dry-run with 'other' category (a valid enum) fires no HTTP."""
    import substack_cli.moderation  # noqa: F401

    with respx.mock(assert_all_called=False) as router:
        result = cli_runner.invoke(
            comments_app,
            ["report-user", "789", "--category", "other"],
        )
    assert result.exit_code == 0, result.output
    assert len(router.calls) == 0


# ---------------------------------------------------------------------------
# CLI write-gate refusal: comments report-user
# ---------------------------------------------------------------------------


def test_comments_report_user_blocked_without_write_gate(
    isolated_config, authed_env, cli_runner
):
    """Without SUBSTACK_ENABLE_WRITE, `comments report-user` must exit non-zero.

    Uses a valid category ('spam') so the test is independent of whether enum
    validation or the write-gate check fires first (issue #19 ordering).
    """
    import substack_cli.moderation  # noqa: F401

    result = cli_runner.invoke(
        comments_app,
        ["report-user", "456", "--category", "spam"],
    )
    assert result.exit_code != 0


def test_comments_report_user_write_gate_message(
    isolated_config, authed_env, cli_runner
):
    """Error output mentions SUBSTACK_ENABLE_WRITE when write gate fires."""
    import substack_cli.moderation  # noqa: F401

    result = cli_runner.invoke(
        comments_app,
        ["report-user", "456", "--category", "spam"],
    )
    assert result.exit_code != 0
    assert "SUBSTACK_ENABLE_WRITE" in result.output
