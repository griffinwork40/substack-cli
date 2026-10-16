"""Substack CLI — comment moderation CLI layer (Typer commands only).

Core logic, enums, and body builders live in moderation_core.py.
All write commands default to dry-run unless --no-dry-run / --yes is passed.
"""
from __future__ import annotations

from typing import Optional

import typer

from substack_cli.app import app, comments_app, moderation_app
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
from substack_cli.moderation_core import (
    VALID_CATEGORIES,
    VALID_EXPIRIES,
    VALID_VISIBILITIES,
    ban_commenter,
    build_ban_body,
    build_profile_report_body,
    build_report_body,
    get_delete_reasons,
    get_report_categories,
    get_user_bans,
    report_comment,
    report_user_profile,
)
import substack_cli.moderation_hidden_cmd  # noqa: F401,E402 — registers `moderation hidden`

# ---------------------------------------------------------------------------
# Helper: build client
# ---------------------------------------------------------------------------

def _make_client() -> SubstackClient:
    cookies = resolve_cookies()
    pub_url = resolve_publication_url()
    return SubstackClient(cookies=cookies, publication_url=pub_url)


# ---------------------------------------------------------------------------
# DRY-RUN helper
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
# CLI: comments report
# ---------------------------------------------------------------------------

@comments_app.command("report")
def comments_report_cmd(
    comment_id: int = typer.Argument(..., help="Comment ID to report."),
    category: str = typer.Option(
        "spam",
        "--category", "-c",
        help=(
            "Report category. One of: spam, impersonation, hate, "
            "private_information, plagiarism, illegal_activity, pornography, other."
        ),
    ),
    details: str = typer.Option(
        "",
        "--details", "-d",
        help="Human-readable description of the violation.",
    ),
    to_substack: bool = typer.Option(
        True,
        "--to-substack/--no-to-substack",
        help="Whether to also report to Substack trust & safety.",
    ),
    publication_id: Optional[int] = typer.Option(
        None,
        "--publication-id",
        help="Numeric publication ID for the report body (no resolver — find via DevTools).",
    ),
    report_context: Optional[str] = typer.Option(
        None, "--report-context",
        help="Optional JSON string for report_context (shape unknown; omit in most cases).",
    ),
    no_dry_run: bool = typer.Option(
        False,
        "--no-dry-run",
        "--yes",
        help="Actually send the request. Omit to print the request and exit.",
    ),
    pretty: bool = typer.Option(False, "--pretty"),
):
    """Report a comment by ID. Dry-run by default (prints request without sending)."""
    if not is_write_enabled():
        emit_error("Write operations require SUBSTACK_ENABLE_WRITE=true", pretty=pretty)
    if category not in VALID_CATEGORIES:
        emit_error(
            f"Invalid --category {category!r}. "
            f"Valid values: {sorted(VALID_CATEGORIES)}",
            pretty=pretty,
        )

    try:
        body = build_report_body(
            category, details, to_substack, publication_id, report_context
        )

        # Build URL from publication URL without making any HTTP call.
        pub_url = resolve_publication_url()
        url = f"{pub_url}/api/v1/comment/{comment_id}/report"

        if not no_dry_run:
            _dry_run_output("POST", url, body, pretty=pretty)
            return

        client = _make_client()
        result = report_comment(
            client, comment_id, category, details, to_substack, publication_id,
            report_context,
        )
        output(result, pretty=pretty)

    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


# ---------------------------------------------------------------------------
# CLI: comments report-user
# ---------------------------------------------------------------------------

@comments_app.command("report-user")
def comments_report_user_cmd(
    user_id: int = typer.Argument(..., help="User ID to report."),
    category: str = typer.Option(
        "spam",
        "--category", "-c",
        help=(
            "Report category. One of: spam, impersonation, hate, "
            "private_information, plagiarism, illegal_activity, pornography, other."
        ),
    ),
    details: str = typer.Option(
        "",
        "--details", "-d",
        help="Human-readable description of the violation.",
    ),
    to_substack: bool = typer.Option(
        True,
        "--to-substack/--no-to-substack",
        help="Whether to also report to Substack trust & safety.",
    ),
    report_context: Optional[str] = typer.Option(
        None,
        "--report-context",
        help="Optional JSON string for report_context field.",
    ),
    no_dry_run: bool = typer.Option(
        False,
        "--no-dry-run",
        "--yes",
        help="Actually send the request. Omit to print the request and exit.",
    ),
    pretty: bool = typer.Option(False, "--pretty"),
):
    """Report a user's profile. Dry-run by default."""
    if not is_write_enabled():
        emit_error("Write operations require SUBSTACK_ENABLE_WRITE=true", pretty=pretty)
    if category not in VALID_CATEGORIES:
        emit_error(
            f"Invalid --category {category!r}. "
            f"Valid values: {sorted(VALID_CATEGORIES)}",
            pretty=pretty,
        )

    try:
        body = build_profile_report_body(category, details, to_substack, report_context)
        url = f"{SUBSTACK_COM}/api/v1/users/{user_id}/profile/report"

        if not no_dry_run:
            _dry_run_output("POST", url, body, pretty=pretty)
            return

        client = _make_client()
        result = report_user_profile(
            client, user_id, category, details, to_substack, report_context
        )
        output(result, pretty=pretty)

    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


# ---------------------------------------------------------------------------
# CLI: comments ban
# ---------------------------------------------------------------------------

@comments_app.command("ban")
def comments_ban_cmd(
    comment_id: int = typer.Argument(..., help="Comment ID that triggered the ban."),
    user_id: int = typer.Option(..., "--user-id", "-u", help="User ID to ban."),
    expiry: str = typer.Option(
        ...,
        "--expiry",
        "-e",
        help=(
            "Ban duration: one of 1d, 30d, 90d, 1y, 1000y "
            "(1000y = Substack's 'Forever')."
        ),
    ),
    visibility: str = typer.Option(
        "visible",
        "--visibility",
        help=(
            "Comment visibility after ban. "
            "'visible' (default) keeps comments; "
            "'delete_this_comment' removes only this comment; "
            "'delete_all_comments' removes all of the user's comments (irreversible). "
            "Note: Substack reloads the page when visibility != 'visible'."
        ),
    ),
    no_dry_run: bool = typer.Option(
        False,
        "--no-dry-run",
        "--yes",
        help="Actually send the request. Omit to print the request and exit.",
    ),
    pretty: bool = typer.Option(False, "--pretty"),
):
    """Ban a commenter by comment ID. Dry-run by default."""
    if not is_write_enabled():
        emit_error("Write operations require SUBSTACK_ENABLE_WRITE=true", pretty=pretty)
    if visibility not in VALID_VISIBILITIES:
        emit_error(
            f"Invalid --visibility {visibility!r}. "
            f"Must be one of: {sorted(VALID_VISIBILITIES)}",
            pretty=pretty,
        )
    if expiry not in VALID_EXPIRIES:
        emit_error(
            f"Invalid --expiry {expiry!r}. Must be one of: {sorted(VALID_EXPIRIES)}",
            pretty=pretty,
        )

    try:
        body = build_ban_body(user_id, expiry, visibility)
        pub_url = resolve_publication_url()
        url = f"{pub_url}/api/v1/comment_ban/{comment_id}"

        if not no_dry_run:
            _dry_run_output("POST", url, body, pretty=pretty)
            return

        client = _make_client()
        result = ban_commenter(client, comment_id, user_id, expiry, visibility)
        output(result, pretty=pretty)

    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


# ---------------------------------------------------------------------------
# CLI: comments bans  (READ-ONLY)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# CLI: moderation report-categories  (READ-ONLY)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# CLI: moderation delete-reasons  (READ-ONLY)
# ---------------------------------------------------------------------------

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
