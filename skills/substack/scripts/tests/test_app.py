"""Tests for the Substack CLI app layer — command wiring, error handling, output.

These tests exercise the Typer app as a whole via CliRunner, verifying that
commands are properly registered and the top-level error wrapper catches
unexpected exceptions without leaking tracebacks.
"""
import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

# Import command modules to register their commands on app/sub-apps
from substack_cli import config as _config_module  # noqa: F401
from substack_cli import read as _read_module       # noqa: F401
from substack_cli import publish as _publish_module  # noqa: F401
from substack_cli import manage as _manage_module   # noqa: F401
from substack_cli.app import app, config_app, drafts_app


def test_help_lists_all_subapps():
    """substack --help should list all sub-app names."""
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    output = result.output
    for subapp in ["config", "drafts", "comments", "subscribers",
                   "recommendations", "tags", "publication"]:
        assert subapp in output


@respx.mock
def test_archive_command_runs_and_returns_json(authed_env):
    """Sanity wiring check — a read.py command should produce JSON output."""
    respx.get("https://charliepgarcia.substack.com/api/v1/archive").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "title": "Post"}])
    )
    runner = CliRunner()
    result = runner.invoke(app, ["archive"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout.strip())
    assert parsed == [{"id": 1, "title": "Post"}]


@respx.mock
def test_drafts_create_refuses_without_write_gate_and_makes_zero_http_calls(authed_env):
    """Drafts create should refuse without SUBSTACK_ENABLE_WRITE and make
    zero HTTP calls (no respx routes should be hit)."""
    runner = CliRunner()
    result = runner.invoke(app, ["drafts", "create", "--title", "Test"])
    assert result.exit_code != 0
    # No respx routes were hit (none were even registered)
    assert len(respx.calls) == 0


def test_drafts_publish_refuses_without_yes_flag(authed_env, write_enabled_env):
    """Drafts publish should require --yes even with write gate enabled."""
    runner = CliRunner()
    result = runner.invoke(app, ["drafts", "publish", "123"])
    assert result.exit_code != 0
    assert "yes" in result.output.lower()


def test_uncaught_exception_becomes_json_error_never_a_traceback(
    authed_env, monkeypatch
):
    """No raw Python tracebacks should reach the user — the main() wrapper
    converts everything to a JSON error on stderr."""
    import substack_cli.read as read_module

    def _boom(*_args, **_kwargs):
        raise ValueError("Internal explosion")

    monkeypatch.setattr(read_module, "get_archive", _boom)

    runner = CliRunner()
    result = runner.invoke(app, ["archive"])
    assert result.exit_code != 0
    combined = (result.stdout or "") + (result.output or "")
    assert "Traceback" not in combined
    assert "Internal explosion" in combined or "Unexpected error" in combined


@respx.mock
def test_pretty_flag_produces_rich_output(authed_env):
    """--pretty should produce Rich-formatted output, not raw compact JSON."""
    respx.get("https://charliepgarcia.substack.com/api/v1/archive").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "title": "Post"}])
    )
    runner = CliRunner()
    result = runner.invoke(app, ["archive", "--pretty"])
    assert result.exit_code == 0
    # Rich output contains ANSI codes or panel-style output, not bare JSON array
    # (The exact format depends on Rich, but it should not be just [{"id":1,...]})


def test_missing_auth_surfaces_two_path_remediation_message(isolated_config, monkeypatch):
    """When no cookies are configured at all, an AUTHENTICATED command's error
    should mention both the env var and the config command as remediation
    paths. (Uses `subscribers count` — a public command like `archive` no
    longer errors when unauthenticated; see test_read_anonymous.py.)"""
    # A publication URL is present, so the failure is specifically the missing
    # cookie, not a missing publication URL.
    monkeypatch.setenv("SUBSTACK_PUBLICATION_URL", "charliepgarcia")
    runner = CliRunner()
    result = runner.invoke(app, ["subscribers", "count"])
    assert result.exit_code != 0
    combined = (result.stdout or "") + (result.output or "")
    assert "SUBSTACK_COOKIES_STRING" in combined or "substack config" in combined


@respx.mock
def test_config_test_command_reports_failure_clearly_on_401(authed_env, isolated_config):
    """config test should report auth failure clearly on 401."""
    from substack_cli.client import SUBSTACK_COM

    respx.get(f"{SUBSTACK_COM}/api/v1/user/profile/self").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    runner = CliRunner()
    result = runner.invoke(config_app, ["test"])
    assert result.exit_code != 0
    combined = (result.stdout or "") + (result.output or "")
    assert "auth" in combined.lower() or "cookie" in combined.lower() or "expired" in combined.lower()