"""Substack CLI — moderation core: enums, body builders, and pure logic functions.

This module contains no Typer commands. It is imported by moderation.py (CLI
layer) and directly by tests. Keeping it command-free avoids the import-order
cycle and keeps the CLI layer thin.
"""
from __future__ import annotations

import json
from typing import Optional

from substack_cli.auth import is_write_enabled
from substack_cli.client import SubstackClient

# ---------------------------------------------------------------------------
# Validated enum values
# ---------------------------------------------------------------------------

# reportCategory values confirmed from bundle module 74588
VALID_CATEGORIES = {
    "spam",
    "impersonation",
    "hate",
    "private_information",
    "plagiarism",
    "illegal_activity",
    "pornography",
    "other",
}

# commentVisibility values confirmed from bundle module 5646 (ban modal select)
VALID_VISIBILITIES = {"visible", "delete_this_comment", "delete_all_comments"}

# expiry values confirmed from bundle module 92538 (ban modal "Ban duration"
# select). "1000y" is the UI's "Forever".
VALID_EXPIRIES = {"1d", "30d", "90d", "1y", "1000y"}


# ---------------------------------------------------------------------------
# Pure body builders (used by both CLI dry-run and live core functions)
# ---------------------------------------------------------------------------

def build_report_body(
    category: str,
    details: str,
    reported_to_substack: bool,
    publication_id: Optional[int],
    report_context: Optional[str],
) -> dict:
    """Build the POST body for /api/v1/comment/{id}/report."""
    body: dict = {
        "reportCategory": category,
        "details": details,
        "reportedToSubstack": reported_to_substack,
    }
    if publication_id is not None:
        body["publication_id"] = publication_id
    if report_context is not None:
        try:
            body["report_context"] = json.loads(report_context)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"--report-context is not valid JSON: {exc}"
            ) from exc
    return body


def build_profile_report_body(
    category: str,
    details: str,
    reported_to_substack: bool,
    report_context: Optional[str],
) -> dict:
    """Build the POST body for /api/v1/users/{id}/profile/report."""
    body: dict = {
        "reportCategory": category,
        "details": details,
        "reportedToSubstack": reported_to_substack,
        "surface": "profile",
    }
    if report_context is not None:
        try:
            body["report_context"] = json.loads(report_context)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"--report-context is not valid JSON: {exc}"
            ) from exc
    return body


def build_ban_body(user_id: int, expiry: str, comment_visibility: str) -> dict:
    """Build the POST body for /api/v1/comment_ban/{comment_id}."""
    return {
        "user_id": user_id,
        "expiry": expiry,
        "commentVisibility": comment_visibility,
    }


# ---------------------------------------------------------------------------
# Core logic functions (testable without CLI layer)
# ---------------------------------------------------------------------------

def report_comment(
    client: SubstackClient,
    comment_id: int,
    category: str,
    details: str,
    reported_to_substack: bool,
    publication_id: Optional[int],
    report_context: Optional[str],
) -> dict:
    """Report a comment via POST /api/v1/comment/{comment_id}/report."""
    if not is_write_enabled():
        raise ValueError("Write operations require SUBSTACK_ENABLE_WRITE=true")
    if category not in VALID_CATEGORIES:
        raise ValueError(
            f"Invalid category {category!r}. "
            f"Valid values: {sorted(VALID_CATEGORIES)}"
        )
    body = build_report_body(
        category, details, reported_to_substack, publication_id, report_context
    )
    return client.post(f"/api/v1/comment/{comment_id}/report", json_body=body)


def report_note_reply(
    client: SubstackClient,
    comment_id: int,
    category: str,
    details: str,
    reported_to_substack: bool,
    publication_id: Optional[int],
    report_context: Optional[str],
) -> dict:
    """Report a note or note reply via POST https://substack.com/api/v1/comment/{id}/report.

    Note replies live on substack.com (host "A"), not on the publication host.
    The endpoint path is identical to report_comment but the host differs.
    """
    # Intentional library-level defense for programmatic callers: re-run the
    # write-gate and category checks even though comments_report_cmd already
    # validates them at the CLI layer.  This keeps report_note_reply safe when
    # called directly (e.g. from scripts or tests) without going through the
    # CLI path.
    if not is_write_enabled():
        raise ValueError("Write operations require SUBSTACK_ENABLE_WRITE=true")
    if category not in VALID_CATEGORIES:
        raise ValueError(
            f"Invalid category {category!r}. "
            f"Valid values: {sorted(VALID_CATEGORIES)}"
        )
    body = build_report_body(
        category, details, reported_to_substack, publication_id, report_context
    )
    return client.post(
        f"/api/v1/comment/{comment_id}/report",
        host="A",
        json_body=body,
    )


def report_user_profile(
    client: SubstackClient,
    user_id: int,
    category: str,
    details: str,
    reported_to_substack: bool,
    report_context: Optional[str],
) -> dict:
    """Report a user profile via POST https://substack.com/api/v1/users/{user_id}/profile/report."""
    if not is_write_enabled():
        raise ValueError("Write operations require SUBSTACK_ENABLE_WRITE=true")
    if category not in VALID_CATEGORIES:
        raise ValueError(
            f"Invalid category {category!r}. "
            f"Valid values: {sorted(VALID_CATEGORIES)}"
        )
    body = build_profile_report_body(
        category, details, reported_to_substack, report_context
    )
    return client.post(
        f"/api/v1/users/{user_id}/profile/report",
        host="A",
        json_body=body,
    )


def ban_commenter(
    client: SubstackClient,
    comment_id: int,
    user_id: int,
    expiry: str,
    comment_visibility: str,
) -> dict:
    """Ban a commenter via POST /api/v1/comment_ban/{comment_id}."""
    if not is_write_enabled():
        raise ValueError("Write operations require SUBSTACK_ENABLE_WRITE=true")
    if comment_visibility not in VALID_VISIBILITIES:
        raise ValueError(
            f"Invalid commentVisibility {comment_visibility!r}. "
            f"Must be one of: {sorted(VALID_VISIBILITIES)}"
        )
    if expiry not in VALID_EXPIRIES:
        raise ValueError(
            f"Invalid expiry {expiry!r}. Must be one of: {sorted(VALID_EXPIRIES)}"
        )
    body = build_ban_body(user_id, expiry, comment_visibility)
    return client.post(f"/api/v1/comment_ban/{comment_id}", json_body=body)


def get_user_bans(client: SubstackClient, user_id: int) -> dict:
    """Read bans for a user: GET /api/v1/user/{user_id}/bans."""
    return client.get(f"/api/v1/user/{user_id}/bans")


def get_report_categories(client: SubstackClient) -> dict:
    """GET https://substack.com/api/v1/reporting/flows — report category tree."""
    return client.get("/api/v1/reporting/flows", host="A")


def get_delete_reasons(client: SubstackClient) -> dict:
    """GET /api/v1/comment/moderation/delete_reasons."""
    return client.get("/api/v1/comment/moderation/delete_reasons")
