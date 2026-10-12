"""CLI-layer tests for read-only moderation commands.

Covers item 3 from issue #20: cli_runner.invoke tests (happy path + auth-error)
for the read-only commands:
  - comments bans
  - moderation report-categories
  - moderation delete-reasons
"""
from __future__ import annotations

import httpx
import respx

from substack_cli.app import app, comments_app
from substack_cli.client import SUBSTACK_COM

# ---------------------------------------------------------------------------
# Helpers / shared data
# ---------------------------------------------------------------------------

_FAKE_PUB = "https://charliepgarcia.substack.com"
_BANS_URL = f"{_FAKE_PUB}/api/v1/user/123/bans"
_CATEGORIES_URL = f"{SUBSTACK_COM}/api/v1/reporting/flows"
_DELETE_REASONS_URL = f"{_FAKE_PUB}/api/v1/comment/moderation/delete_reasons"

_BANS_PAYLOAD = {"bans": [{"id": 1, "user_id": 123}], "sub": None}
_CATEGORIES_PAYLOAD = [{"id": "spam", "label": "Spam"}, {"id": "hate", "label": "Hate"}]
_DELETE_REASONS_PAYLOAD = ["spam", "slop", "off-topic"]


# ---------------------------------------------------------------------------
# comments bans — happy path
# ---------------------------------------------------------------------------


@respx.mock
def test_comments_bans_happy_path(
    isolated_config, authed_env, cli_runner
):
    """CLI `comments bans <user_id>` returns the API payload on success."""
    import substack_cli.moderation  # noqa: F401 — register commands

    respx.get(_BANS_URL).mock(
        return_value=httpx.Response(200, json=_BANS_PAYLOAD)
    )

    result = cli_runner.invoke(comments_app, ["bans", "123"])
    assert result.exit_code == 0, result.output
    import json
    data = json.loads(result.output)
    assert data["bans"][0]["user_id"] == 123


@respx.mock
def test_comments_bans_happy_path_empty_list(
    isolated_config, authed_env, cli_runner
):
    """Happy path with an empty bans list still exits 0."""
    import substack_cli.moderation  # noqa: F401

    respx.get(_BANS_URL).mock(
        return_value=httpx.Response(200, json={"bans": [], "sub": None})
    )

    result = cli_runner.invoke(comments_app, ["bans", "123"])
    assert result.exit_code == 0, result.output
    import json
    data = json.loads(result.output)
    assert data["bans"] == []


# ---------------------------------------------------------------------------
# comments bans — auth error path
# ---------------------------------------------------------------------------


@respx.mock
def test_comments_bans_auth_error(
    isolated_config, authed_env, cli_runner
):
    """CLI `comments bans` surfaces an error on 401 from the API."""
    import substack_cli.moderation  # noqa: F401

    respx.get(_BANS_URL).mock(
        return_value=httpx.Response(401, json={"error": "Unauthorized"})
    )

    result = cli_runner.invoke(comments_app, ["bans", "123"])
    assert result.exit_code != 0


@respx.mock
def test_comments_bans_403_error(
    isolated_config, authed_env, cli_runner
):
    """CLI `comments bans` surfaces an error on 403 from the API."""
    import substack_cli.moderation  # noqa: F401

    respx.get(_BANS_URL).mock(
        return_value=httpx.Response(403, json={"error": "Forbidden"})
    )

    result = cli_runner.invoke(comments_app, ["bans", "123"])
    assert result.exit_code != 0


def test_comments_bans_no_auth_raises(
    isolated_config, cli_runner
):
    """Without credentials, `comments bans` exits non-zero (AuthError)."""
    import substack_cli.moderation  # noqa: F401

    result = cli_runner.invoke(comments_app, ["bans", "123"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# moderation report-categories — happy path
# ---------------------------------------------------------------------------


@respx.mock
def test_moderation_report_categories_happy_path(
    isolated_config, authed_env, cli_runner
):
    """CLI `moderation report-categories` returns the categories list."""
    import substack_cli.moderation  # noqa: F401

    respx.get(_CATEGORIES_URL).mock(
        return_value=httpx.Response(200, json=_CATEGORIES_PAYLOAD)
    )

    result = cli_runner.invoke(app, ["moderation", "report-categories"])
    assert result.exit_code == 0, result.output
    import json
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["id"] == "spam"


@respx.mock
def test_moderation_report_categories_empty(
    isolated_config, authed_env, cli_runner
):
    """Happy path with an empty categories list exits 0."""
    import substack_cli.moderation  # noqa: F401

    respx.get(_CATEGORIES_URL).mock(
        return_value=httpx.Response(200, json=[])
    )

    result = cli_runner.invoke(app, ["moderation", "report-categories"])
    assert result.exit_code == 0, result.output
    import json
    assert json.loads(result.output) == []


# ---------------------------------------------------------------------------
# moderation report-categories — auth error path
# ---------------------------------------------------------------------------


@respx.mock
def test_moderation_report_categories_auth_error(
    isolated_config, authed_env, cli_runner
):
    """CLI `moderation report-categories` surfaces error on 401."""
    import substack_cli.moderation  # noqa: F401

    respx.get(_CATEGORIES_URL).mock(
        return_value=httpx.Response(401, json={"error": "Unauthorized"})
    )

    result = cli_runner.invoke(app, ["moderation", "report-categories"])
    assert result.exit_code != 0


@respx.mock
def test_moderation_report_categories_403_error(
    isolated_config, authed_env, cli_runner
):
    """CLI `moderation report-categories` surfaces error on 403."""
    import substack_cli.moderation  # noqa: F401

    respx.get(_CATEGORIES_URL).mock(
        return_value=httpx.Response(403, json={"error": "Forbidden"})
    )

    result = cli_runner.invoke(app, ["moderation", "report-categories"])
    assert result.exit_code != 0


def test_moderation_report_categories_no_auth(
    isolated_config, cli_runner
):
    """Without credentials, `moderation report-categories` exits non-zero."""
    import substack_cli.moderation  # noqa: F401

    result = cli_runner.invoke(app, ["moderation", "report-categories"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# moderation delete-reasons — happy path
# ---------------------------------------------------------------------------


@respx.mock
def test_moderation_delete_reasons_happy_path(
    isolated_config, authed_env, cli_runner
):
    """CLI `moderation delete-reasons` returns the delete reasons list."""
    import substack_cli.moderation  # noqa: F401

    respx.get(_DELETE_REASONS_URL).mock(
        return_value=httpx.Response(200, json=_DELETE_REASONS_PAYLOAD)
    )

    result = cli_runner.invoke(app, ["moderation", "delete-reasons"])
    assert result.exit_code == 0, result.output
    import json
    data = json.loads(result.output)
    assert "spam" in data


@respx.mock
def test_moderation_delete_reasons_empty(
    isolated_config, authed_env, cli_runner
):
    """Happy path with an empty delete reasons list exits 0."""
    import substack_cli.moderation  # noqa: F401

    respx.get(_DELETE_REASONS_URL).mock(
        return_value=httpx.Response(200, json=[])
    )

    result = cli_runner.invoke(app, ["moderation", "delete-reasons"])
    assert result.exit_code == 0, result.output
    import json
    assert json.loads(result.output) == []


# ---------------------------------------------------------------------------
# moderation delete-reasons — auth error path
# ---------------------------------------------------------------------------


@respx.mock
def test_moderation_delete_reasons_auth_error(
    isolated_config, authed_env, cli_runner
):
    """CLI `moderation delete-reasons` surfaces error on 401."""
    import substack_cli.moderation  # noqa: F401

    respx.get(_DELETE_REASONS_URL).mock(
        return_value=httpx.Response(401, json={"error": "Unauthorized"})
    )

    result = cli_runner.invoke(app, ["moderation", "delete-reasons"])
    assert result.exit_code != 0


@respx.mock
def test_moderation_delete_reasons_403_error(
    isolated_config, authed_env, cli_runner
):
    """CLI `moderation delete-reasons` surfaces error on 403."""
    import substack_cli.moderation  # noqa: F401

    respx.get(_DELETE_REASONS_URL).mock(
        return_value=httpx.Response(403, json={"error": "Forbidden"})
    )

    result = cli_runner.invoke(app, ["moderation", "delete-reasons"])
    assert result.exit_code != 0


def test_moderation_delete_reasons_no_auth(
    isolated_config, cli_runner
):
    """Without credentials, `moderation delete-reasons` exits non-zero."""
    import substack_cli.moderation  # noqa: F401

    result = cli_runner.invoke(app, ["moderation", "delete-reasons"])
    assert result.exit_code != 0
