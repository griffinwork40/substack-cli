"""Substack CLI — moderation helpers: shared client factory, dry-run output,
and read-only moderation query commands.

Extracted from moderation.py to keep that module within the 350-LOC limit.
Import only from moderation.py and tests — not from moderation_core.py (which
must remain command-free and import-cycle-free).

Note: this module also registers three read-only Typer commands
(``comments bans``, ``moderation report-categories``,
``moderation delete-reasons``) that were moved here during the #34 merge to
keep moderation.py under the LOC limit.  The imports for those commands
(app, typer, AuthError, etc.) appear below the helper functions rather than at
the top of the file; this is intentional — the command-registration block
depends on ``moderation_app`` being defined in this same module, and moving
the imports to the top would not change the registration order.
"""
from __future__ import annotations

from substack_cli.auth import resolve_cookies, resolve_publication_url
from substack_cli.client import SubstackClient, output


def _make_client() -> SubstackClient:
    """Resolve credentials and return a ready SubstackClient."""
    cookies = resolve_cookies()
    pub_url = resolve_publication_url()
    return SubstackClient(cookies=cookies, publication_url=pub_url)


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
# Read-only moderation query commands (moved here to keep moderation.py <=350 LOC)
# ---------------------------------------------------------------------------

from substack_cli.app import app, comments_app
from substack_cli.auth import AuthError
from substack_cli.client import SubstackApiError, emit_error
from substack_cli.moderation_core import (
    get_delete_reasons,
    get_report_categories,
    get_user_bans,
)

import typer

moderation_app = typer.Typer(
    help="Comment moderation helpers: report categories, delete reasons.",
    no_args_is_help=True,
)
app.add_typer(moderation_app, name="moderation")


@comments_app.command("bans")
def comments_bans_cmd(
    user_id: int = typer.Argument(..., help="User ID to look up bans for."),
    pretty: bool = typer.Option(False, "--pretty"),
):
    """List existing bans for a user (read-only). GET /api/v1/user/{user_id}/bans."""
    try:
        client = _make_client()
        result = get_user_bans(client, user_id)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@moderation_app.command("report-categories")
def moderation_report_categories_cmd(
    pretty: bool = typer.Option(False, "--pretty"),
):
    """Fetch Substack's reporting flow tree (report categories). Read-only."""
    try:
        client = _make_client()
        result = get_report_categories(client)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@moderation_app.command("delete-reasons")
def moderation_delete_reasons_cmd(
    pretty: bool = typer.Option(False, "--pretty"),
):
    """Fetch comment deletion reason codes. Read-only."""
    try:
        client = _make_client()
        result = get_delete_reasons(client)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)
