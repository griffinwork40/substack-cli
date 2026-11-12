"""Substack CLI — users sub-app: block / unblock / blocks (issue #27).

All write commands (block, unblock) default to dry-run unless
--no-dry-run / --yes is passed. The read command (blocks) is always live.

Endpoint note: see user_blocks.py header — endpoint unverified, operator
live-test required before closing #27.
"""
from __future__ import annotations

import typer

from substack_cli.app import app, users_app  # noqa: F401 — app imported for side-effect
from substack_cli.auth import (
    AuthError,
    is_write_enabled,
    resolve_cookies,
    resolve_publication_url,
)
from substack_cli.client import (
    SUBSTACK_COM,
    SubstackApiError,
    SubstackClient,
    emit_error,
    output,
)
from substack_cli.user_blocks import (
    BLOCK_PATH,
    block_user,
    build_block_body,
    get_blocks,
    unblock_user,
)

# ---------------------------------------------------------------------------
# Helper: build client
# ---------------------------------------------------------------------------


def _make_client() -> SubstackClient:
    """Resolve auth and create a client for user-block operations.

    All block endpoints use host "A" (substack.com), so a publication URL is
    NOT required — fall back to SUBSTACK_COM when none is configured.
    """
    cookies = resolve_cookies()
    try:
        pub_url = resolve_publication_url()
    except AuthError:
        pub_url = SUBSTACK_COM
    return SubstackClient(cookies=cookies, publication_url=pub_url)


# ---------------------------------------------------------------------------
# Dry-run helper (mirrors moderation.py exactly)
# ---------------------------------------------------------------------------


def _dry_run_output(method: str, url: str, body: dict, pretty: bool) -> None:
    """Print the exact request that would be fired, then exit 0."""
    payload = {
        "dry_run": True,
        "method": method,
        "url": url,
        "body": body,
    }
    output(payload, pretty=pretty)


# ---------------------------------------------------------------------------
# CLI: users block
# ---------------------------------------------------------------------------


@users_app.command("block")
def users_block_cmd(
    user_id: int = typer.Argument(..., help="Numeric user ID to block."),
    no_dry_run: bool = typer.Option(
        False,
        "--no-dry-run",
        "--yes",
        help="Actually send the request. Omit to print the request and exit.",
    ),
    pretty: bool = typer.Option(False, "--pretty"),
):
    """Block a user by ID. Dry-run by default (prints request without sending).

    \b
    NOTE: endpoint unverified — operator live-test required (issue #27).
    Candidate: POST https://substack.com/api/v1/user/{user_id}/block
    """
    if not is_write_enabled():
        emit_error("Write operations require SUBSTACK_ENABLE_WRITE=true", pretty=pretty)

    try:
        path = BLOCK_PATH.format(user_id=user_id)
        url = f"{SUBSTACK_COM}{path}"
        body = build_block_body(user_id)

        if not no_dry_run:
            _dry_run_output("POST", url, body, pretty=pretty)
            return

        client = _make_client()
        result = block_user(client, user_id)
        output(result, pretty=pretty)

    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


# ---------------------------------------------------------------------------
# CLI: users unblock
# ---------------------------------------------------------------------------


@users_app.command("unblock")
def users_unblock_cmd(
    user_id: int = typer.Argument(..., help="Numeric user ID to unblock."),
    no_dry_run: bool = typer.Option(
        False,
        "--no-dry-run",
        "--yes",
        help="Actually send the request. Omit to print the request and exit.",
    ),
    pretty: bool = typer.Option(False, "--pretty"),
):
    """Unblock a user by ID. Dry-run by default (prints request without sending).

    \b
    NOTE: endpoint unverified — operator live-test required (issue #27).
    Candidate: DELETE https://substack.com/api/v1/user/{user_id}/block
    """
    if not is_write_enabled():
        emit_error("Write operations require SUBSTACK_ENABLE_WRITE=true", pretty=pretty)

    try:
        path = BLOCK_PATH.format(user_id=user_id)
        url = f"{SUBSTACK_COM}{path}"

        if not no_dry_run:
            _dry_run_output("DELETE", url, None, pretty=pretty)
            return

        client = _make_client()
        result = unblock_user(client, user_id)
        output(result, pretty=pretty)

    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


# ---------------------------------------------------------------------------
# CLI: users blocks  (READ-ONLY)
# ---------------------------------------------------------------------------


@users_app.command("blocks")
def users_blocks_cmd(
    pretty: bool = typer.Option(False, "--pretty"),
):
    """List users you have blocked (read-only).

    \b
    NOTE: endpoint unverified — operator live-test required (issue #27).
    Candidate: GET https://substack.com/api/v1/user/blocks
    """
    try:
        client = _make_client()
        result = get_blocks(client)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)
