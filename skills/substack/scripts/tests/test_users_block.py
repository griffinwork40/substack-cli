"""Tests for `users block`, `users unblock`, and `users blocks` (issue #27).

Covers:
  1. Dry-run zero-HTTP assertions for `users block` and `users unblock`.
  2. Dry-run body/URL content assertions.
  3. Write-gate refusal (exit non-zero + SUBSTACK_ENABLE_WRITE in output).
  4. Read-only `users blocks` — happy path and auth error paths.

All tests are offline-safe: no real network calls are made.
"""
from __future__ import annotations

import json

import httpx
import respx

import substack_cli.users  # noqa: F401 — registers commands onto users_app at import time
from substack_cli.app import users_app, app
from substack_cli.client import SUBSTACK_COM

# Shared constants matching the candidate endpoints in user_blocks.py
_FAKE_USER_ID = 999
_BLOCK_URL = f"{SUBSTACK_COM}/api/v1/user/{_FAKE_USER_ID}/block"
_BLOCKS_LIST_URL = f"{SUBSTACK_COM}/api/v1/user/blocks"

_BLOCKS_PAYLOAD = {"blocks": [{"id": 1, "blocked_user_id": _FAKE_USER_ID}]}


# ===========================================================================
# users block — dry-run (zero HTTP)
# ===========================================================================


def test_users_block_dry_run_zero_http(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    """Dry-run for `users block` must issue ZERO HTTP requests."""

    with respx.mock(assert_all_called=False) as router:
        result = cli_runner.invoke(users_app, ["block", str(_FAKE_USER_ID)])

    assert result.exit_code == 0, result.output
    assert len(router.calls) == 0


def test_users_block_dry_run_body_contents(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    """Dry-run output for `users block` contains correct method, URL, body."""

    with respx.mock(assert_all_called=False):
        result = cli_runner.invoke(users_app, ["block", str(_FAKE_USER_ID)])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["dry_run"] is True
    assert data["method"] == "POST"
    assert SUBSTACK_COM in data["url"]
    assert str(_FAKE_USER_ID) in data["url"]
    assert data["body"]["user_id"] == _FAKE_USER_ID


# ===========================================================================
# users block — write-gate refusal
# ===========================================================================


def test_users_block_write_gate_refusal(
    isolated_config, authed_env, cli_runner
):
    """Without SUBSTACK_ENABLE_WRITE, `users block` must exit non-zero."""

    result = cli_runner.invoke(users_app, ["block", str(_FAKE_USER_ID)])
    assert result.exit_code != 0


def test_users_block_write_gate_message(
    isolated_config, authed_env, cli_runner
):
    """Error output mentions SUBSTACK_ENABLE_WRITE when write gate fires."""

    result = cli_runner.invoke(users_app, ["block", str(_FAKE_USER_ID)])
    assert result.exit_code != 0
    assert "SUBSTACK_ENABLE_WRITE" in result.output


# ===========================================================================
# users unblock — dry-run (zero HTTP)
# ===========================================================================


def test_users_unblock_dry_run_zero_http(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    """Dry-run for `users unblock` must issue ZERO HTTP requests."""

    with respx.mock(assert_all_called=False) as router:
        result = cli_runner.invoke(users_app, ["unblock", str(_FAKE_USER_ID)])

    assert result.exit_code == 0, result.output
    assert len(router.calls) == 0


def test_users_unblock_dry_run_body_contents(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    """Dry-run output for `users unblock` uses DELETE and correct URL."""

    with respx.mock(assert_all_called=False):
        result = cli_runner.invoke(users_app, ["unblock", str(_FAKE_USER_ID)])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["dry_run"] is True
    assert data["method"] == "DELETE"
    assert SUBSTACK_COM in data["url"]
    assert str(_FAKE_USER_ID) in data["url"]


# ===========================================================================
# users unblock — write-gate refusal
# ===========================================================================


def test_users_unblock_write_gate_refusal(
    isolated_config, authed_env, cli_runner
):
    """Without SUBSTACK_ENABLE_WRITE, `users unblock` must exit non-zero."""

    result = cli_runner.invoke(users_app, ["unblock", str(_FAKE_USER_ID)])
    assert result.exit_code != 0


def test_users_unblock_write_gate_message(
    isolated_config, authed_env, cli_runner
):
    """Error output mentions SUBSTACK_ENABLE_WRITE when write gate fires."""

    result = cli_runner.invoke(users_app, ["unblock", str(_FAKE_USER_ID)])
    assert result.exit_code != 0
    assert "SUBSTACK_ENABLE_WRITE" in result.output


# ===========================================================================
# users blocks (read-only) — happy path
# ===========================================================================


@respx.mock
def test_users_blocks_happy_path(
    isolated_config, authed_env, cli_runner
):
    """CLI `users blocks` returns the API payload on success."""

    respx.get(_BLOCKS_LIST_URL).mock(
        return_value=httpx.Response(200, json=_BLOCKS_PAYLOAD)
    )

    result = cli_runner.invoke(users_app, ["blocks"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["blocks"][0]["blocked_user_id"] == _FAKE_USER_ID


@respx.mock
def test_users_blocks_empty_list(
    isolated_config, authed_env, cli_runner
):
    """Happy path with an empty blocks list still exits 0."""

    respx.get(_BLOCKS_LIST_URL).mock(
        return_value=httpx.Response(200, json={"blocks": []})
    )

    result = cli_runner.invoke(users_app, ["blocks"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["blocks"] == []


# ===========================================================================
# users blocks (read-only) — auth error paths
# ===========================================================================


@respx.mock
def test_users_blocks_auth_error_401(
    isolated_config, authed_env, cli_runner
):
    """CLI `users blocks` surfaces an error on 401 from the API."""

    respx.get(_BLOCKS_LIST_URL).mock(
        return_value=httpx.Response(401, json={"error": "Unauthorized"})
    )

    result = cli_runner.invoke(users_app, ["blocks"])
    assert result.exit_code != 0


@respx.mock
def test_users_blocks_auth_error_403(
    isolated_config, authed_env, cli_runner
):
    """CLI `users blocks` surfaces an error on 403 from the API."""

    respx.get(_BLOCKS_LIST_URL).mock(
        return_value=httpx.Response(403, json={"error": "Forbidden"})
    )

    result = cli_runner.invoke(users_app, ["blocks"])
    assert result.exit_code != 0


def test_users_blocks_no_auth(
    isolated_config, cli_runner
):
    """Without credentials, `users blocks` exits non-zero (AuthError)."""

    result = cli_runner.invoke(users_app, ["blocks"])
    assert result.exit_code != 0


# ===========================================================================
# CLI wiring — users sub-app registered on root app
# ===========================================================================


def test_users_subapp_wired_to_root(cli_runner):
    """The `users` sub-app is registered and shows help from the root app."""

    result = cli_runner.invoke(app, ["users", "--help"])
    assert result.exit_code == 0, result.output
    assert "block" in result.output
    assert "unblock" in result.output
    assert "blocks" in result.output
