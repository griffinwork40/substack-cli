"""CLI command: moderation hidden (Issue #29).

Registers the `hidden` subcommand on moderation_app.
Imported by moderation.py for its side-effect (command registration).
Core logic lives in moderation_hidden.py (no Typer dependency).
"""
from __future__ import annotations

from typing import Optional

import typer

from substack_cli.app import moderation_app
from substack_cli.auth import AuthError, resolve_cookies, resolve_publication_url
from substack_cli.client import SubstackApiError, SubstackClient, emit_error, output
from substack_cli.moderation_hidden import get_hidden_comments


@moderation_app.command("hidden")
def moderation_hidden_cmd(
    since: Optional[str] = typer.Option(
        None,
        "--since",
        help=(
            "Only show post comments hidden after this point. "
            "Accepts shorthand (1h, 6h, 12h, 24h, 48h, 7d) "
            "or an ISO-8601 datetime (e.g. 2026-10-12T00:00:00Z)."
        ),
    ),
    limit: int = typer.Option(
        100,
        "--limit",
        help="Maximum number of results to request from the API.",
    ),
    pretty: bool = typer.Option(False, "--pretty"),
    raw: bool = typer.Option(False, "--raw", help="Include full raw API response in each item."),
) -> None:
    """List post comments hidden by AutoMod or reply rules.

    For each hidden item shows: author, subscriber status, text, AutoMod
    reason (if exposed by the API), and a link.  Read-only — no state is
    changed.

    \b
    ENDPOINT STATUS: Unverified.
    Candidate: GET /api/v1/publication/moderation/hidden_comments.
    Capture the real path from the publication dashboard → Moderation →
    AutoMod decisions (DevTools Network tab) and update AUTOMOD_HIDDEN_PATH
    in moderation_hidden.py.
    Note: AutoMod-hidden note replies are accessible via
    `notes replies <id> --all` (automodHiddenBranches envelope).
    This command covers post comments only.
    """
    try:
        cookies = resolve_cookies()
        pub_url = resolve_publication_url()
        client = SubstackClient(cookies=cookies, publication_url=pub_url)
        items = get_hidden_comments(client, since=since, limit=limit, include_raw=raw)
        output(items, pretty=pretty)
    except ValueError as exc:
        emit_error(str(exc), pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)
