"""End-to-end integration tests — multi-step respx-mocked CLI flows.

These tests exercise the full CLI stack (Typer app → auth resolution →
SubstackClient → HTTP → response parsing → output) via CliRunner, with
all network calls mocked by respx. No real Substack credentials or
network calls are used.
"""
import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

# Import command modules to register their commands
from substack_cli import config as _c  # noqa: F401
from substack_cli import read as _r    # noqa: F401
from substack_cli import publish as _p  # noqa: F401
from substack_cli import manage as _m   # noqa: F401
from substack_cli.app import app
from substack_cli.client import SUBSTACK_COM


PUB_URL = "https://charliepgarcia.substack.com"


@respx.mock
def test_full_draft_lifecycle_create_prepublish_publish(authed_env, write_enabled_env, no_sleep):
    """Create → prepublish → publish — the core authoring loop."""
    # Step 1: resolve byline from self profile
    respx.get(f"{SUBSTACK_COM}/api/v1/user/profile/self").mock(
        return_value=httpx.Response(200, json={"id": 999, "publicationUserId": 888})
    )
    # Step 2: create draft
    respx.post(f"{PUB_URL}/api/v1/drafts").mock(
        return_value=httpx.Response(200, json={"id": 42, "draft_title": "Test"})
    )
    # Step 3: prepublish
    respx.get(f"{PUB_URL}/api/v1/drafts/42/prepublish").mock(
        return_value=httpx.Response(200, json={"errors": [], "suggestions": []})
    )
    # Step 4: publish
    respx.put(f"{PUB_URL}/api/v1/drafts/42/publish").mock(
        return_value=httpx.Response(200, json={"id": 42, "published": True})
    )

    runner = CliRunner()

    # Create
    result = runner.invoke(app, ["drafts", "create", "--title", "Test", "--body-markdown", "Hello"])
    assert result.exit_code == 0, f"create failed: {result.output}"
    draft = json.loads(result.stdout.strip())
    assert draft["id"] == 42

    # Prepublish
    result = runner.invoke(app, ["drafts", "prepublish", "42"])
    assert result.exit_code == 0
    pp = json.loads(result.stdout.strip())
    assert "errors" in pp

    # Publish
    result = runner.invoke(app, ["drafts", "publish", "42", "--yes"])
    assert result.exit_code == 0
    pub = json.loads(result.stdout.strip())
    assert pub["published"] is True


@respx.mock
def test_archive_pagination_across_two_pages(authed_env):
    """Fetch page 1, then page 2 using offset."""
    page1 = [{"id": i, "title": f"Post {i}"} for i in range(1, 6)]
    page2 = [{"id": i, "title": f"Post {i}"} for i in range(6, 11)]

    route = respx.get(f"{PUB_URL}/api/v1/archive").mock(
        side_effect=[
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
        ]
    )

    runner = CliRunner()

    result = runner.invoke(app, ["archive", "--limit", "5", "--offset", "0"])
    assert result.exit_code == 0
    items1 = json.loads(result.stdout.strip())
    assert len(items1) == 5

    result = runner.invoke(app, ["archive", "--limit", "5", "--offset", "5"])
    assert result.exit_code == 0
    items2 = json.loads(result.stdout.strip())
    assert len(items2) == 5
    assert items2[0]["id"] == 6


@respx.mock
def test_expired_cookie_401_flow_end_to_end(authed_env):
    """When the cookie expires (401), the CLI should surface a clear
    remediation message mentioning cookie re-extraction."""
    respx.get(f"{PUB_URL}/api/v1/archive").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )

    runner = CliRunner()
    result = runner.invoke(app, ["archive"])
    assert result.exit_code != 0
    combined = (result.stdout or "") + (result.output or "")
    assert "cookie" in combined.lower() or "expired" in combined.lower() or "auth" in combined.lower()


@respx.mock
def test_rate_limit_429_then_success_end_to_end(authed_env, no_sleep):
    """When hitting a 429, the CLI should retry and eventually succeed."""
    respx.get(f"{PUB_URL}/api/v1/archive").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json=[{"id": 1}]),
        ]
    )

    runner = CliRunner()
    result = runner.invoke(app, ["archive"])
    assert result.exit_code == 0
    items = json.loads(result.stdout.strip())
    assert items == [{"id": 1}]


@pytest.mark.parametrize(
    "cmd",
    [
        ["drafts", "create", "--title", "T"],
        ["drafts", "delete", "1", "--yes"],
        ["drafts", "publish", "1", "--yes"],
        ["drafts", "schedule", "1", "--at", "2026-01-01T00:00:00Z"],
        ["drafts", "unschedule", "1"],
        ["comments", "create", "1", "body"],
        ["comments", "delete", "1", "--yes"],
        ["comments", "react", "1"],
        ["comments", "unreact", "1"],
        ["subscribers", "add", "e@example.com"],
        ["subscribers", "remove", "1", "--yes"],
        ["recommendations", "add", "1"],
        ["recommendations", "remove", "1", "--yes"],
        ["tags", "create", "name"],
        ["tags", "delete", "1", "--yes"],
        ["tags", "attach", "1", "2"],
        ["tags", "detach", "1", "2"],
        ["image-upload", "/tmp/fake.png"],
        ["publication", "update", "--name", "x"],
    ],
)
@respx.mock
def test_write_gate_blocks_every_mutating_command(cmd, authed_env):
    """Every mutating command should refuse with exit 1 when
    SUBSTACK_ENABLE_WRITE is not set, and make zero HTTP calls."""
    # No respx routes registered — if any HTTP call is attempted, respx
    # would raise a RouteNotFoundError or the request would fail.
    runner = CliRunner()
    result = runner.invoke(app, cmd)
    assert result.exit_code != 0, f"Command {cmd} should have been blocked by write gate"
    assert len(respx.calls) == 0