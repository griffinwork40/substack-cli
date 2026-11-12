"""Substack CLI — `substack impersonation scan` command.

Wires the impersonation scan into the CLI as:
    substack impersonation scan [OPTIONS]

Import this module in app.py to register the sub-app, or run standalone via:
    python -m substack_cli.impersonation_cmd
"""
from __future__ import annotations

import typer

from substack_cli.app import app
from substack_cli.impersonation import run_cli
from substack_cli.impersonation_state import (
    DEFAULT_DAILY_CAP,
    DEFAULT_NOTES_LIMIT,
    DEFAULT_POST_LIMIT,
)

impersonation_app = typer.Typer(
    help="Impersonation detection: scan comments and Notes for fake Charlie accounts.",
    no_args_is_help=True,
)
app.add_typer(impersonation_app, name="impersonation")


@impersonation_app.command("scan")
def scan_cmd(
    post_limit: int = typer.Option(
        DEFAULT_POST_LIMIT,
        "--posts",
        help="Number of most-recent posts whose comments to scan.",
    ),
    notes_limit: int = typer.Option(
        DEFAULT_NOTES_LIMIT,
        "--notes",
        help="Number of most-recent Charlie Notes whose replies to scan.",
    ),
    daily_cap: int = typer.Option(
        DEFAULT_DAILY_CAP,
        "--daily-cap",
        help="Maximum would-report actions per calendar day (default 20).",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help=(
            "Dry-run mode (default ON): print would-be report calls, no POSTs. "
            "Live reporting is not yet implemented; --no-dry-run will refuse with an error."
        ),
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print progress to stdout."),
):
    """Scan recent posts and Notes for impersonation of Charlie Garcia.

    In dry-run mode (default), prints {dry_run, method, url, body} for each
    candidate — zero network writes. Live reporting is not yet implemented;
    --no-dry-run refuses with an error.

    State files written to ~/.afk/state/impersonation/:
      seen.json           — persistent dedupe of acted user_ids
      YYYY-MM-DD.json     — dated scan log
      YYYY-MM-DD.md       — morning summary for Andrew to forward to Substack
    """
    run_cli(
        post_limit=post_limit,
        notes_limit=notes_limit,
        daily_cap=daily_cap,
        dry_run=dry_run,
        verbose=verbose,
    )
