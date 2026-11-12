"""Substack CLI — comment moderation CLI layer (Typer commands only).

Core logic, enums, and body builders live in moderation_core.py.
Shared helpers (_make_client, _dry_run_output) live in moderation_helpers.py.
All write commands default to dry-run unless --no-dry-run / --yes is passed.
"""
from __future__ import annotations

from typing import Optional

import typer

from substack_cli.app import app, comments_app
from substack_cli.auth import (
    AuthError,
    is_write_enabled,
    resolve_publication_url,
)
from substack_cli.client import (
    SUBSTACK_COM,
    SubstackApiError,
    emit_error,
    output,
)
from substack_cli.moderation_helpers import _dry_run_output, _make_client, moderation_app  # noqa: F401
from substack_cli.moderation_core import (
    VALID_CATEGORIES,
    VALID_EXPIRIES,
    VALID_VISIBILITIES,
    ban_commenter,
    build_ban_body,
    build_profile_report_body,
    build_report_body,
    report_comment,
    report_note_reply,
    report_user_profile,
)



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
    surface: str = typer.Option(
        "publication",
        "--surface",
        help=(
            "Which host to report against. "
            "'publication' (default) posts to your publication subdomain — use for "
            "post comments. "
            "'notes' posts to substack.com (host A) — use for notes and note replies, "
            "which live on substack.com, not on your publication host."
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
    """Report a comment or note reply by ID. Dry-run by default.

    Use --surface notes for note replies (they live on substack.com, not the
    publication host). Use --surface publication (default) for post comments.
    """
    if surface not in ("publication", "notes"):
        emit_error(
            f"Invalid --surface {surface!r}. Must be 'publication' or 'notes'.",
            pretty=pretty,
        )
        return
    if not is_write_enabled():
        emit_error("Write operations require SUBSTACK_ENABLE_WRITE=true", pretty=pretty)
        return
    if category not in VALID_CATEGORIES:  # core re-validates for programmatic callers
        emit_error(
            f"Invalid --category {category!r}. "
            f"Valid values: {sorted(VALID_CATEGORIES)}",
            pretty=pretty,
        )
        return

    try:
        body = build_report_body(
            category, details, to_substack, publication_id, report_context
        )

        if surface == "notes":
            # Note replies live on substack.com (host A), not the publication host.
            url = f"{SUBSTACK_COM}/api/v1/comment/{comment_id}/report"
        else:
            # Build URL from publication URL without making any HTTP call.
            pub_url = resolve_publication_url()
            url = f"{pub_url}/api/v1/comment/{comment_id}/report"

        if not no_dry_run:
            _dry_run_output("POST", url, body, pretty=pretty)
            return

        client = _make_client()
        if surface == "notes":
            result = report_note_reply(
                client, comment_id, category, details, to_substack, publication_id,
                report_context,
            )
        else:
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
        return
    if category not in VALID_CATEGORIES:  # core re-validates for programmatic callers
        emit_error(
            f"Invalid --category {category!r}. "
            f"Valid values: {sorted(VALID_CATEGORIES)}",
            pretty=pretty,
        )
        return

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
        return
    if visibility not in VALID_VISIBILITIES:
        emit_error(
            f"Invalid --visibility {visibility!r}. "
            f"Must be one of: {sorted(VALID_VISIBILITIES)}",
            pretty=pretty,
        )
        return
    if expiry not in VALID_EXPIRIES:
        emit_error(
            f"Invalid --expiry {expiry!r}. Must be one of: {sorted(VALID_EXPIRIES)}",
            pretty=pretty,
        )
        return

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

