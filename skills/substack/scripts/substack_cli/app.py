"""Substack CLI — app setup, command registration, and entry point."""
import typer

app = typer.Typer(
    name="substack",
    help="Substack (unofficial API) CLI — read publication content and manage drafts, comments, subscribers.",
    no_args_is_help=True,
    add_completion=False,
)
config_app = typer.Typer(help="Manage CLI configuration.", no_args_is_help=True)
drafts_app = typer.Typer(help="Draft CRUD and publish lifecycle.", no_args_is_help=True)
comments_app = typer.Typer(help="Comment moderation and reactions.", no_args_is_help=True)
subscribers_app = typer.Typer(help="Subscriber management.", no_args_is_help=True)
recommendations_app = typer.Typer(help="Cross-publication recommendations.", no_args_is_help=True)
tags_app = typer.Typer(help="Post tag CRUD.", no_args_is_help=True)
publication_app = typer.Typer(help="Publication settings.", no_args_is_help=True)

app.add_typer(config_app, name="config")
app.add_typer(drafts_app, name="drafts")
app.add_typer(comments_app, name="comments")
app.add_typer(subscribers_app, name="subscribers")
app.add_typer(recommendations_app, name="recommendations")
app.add_typer(tags_app, name="tags")
app.add_typer(publication_app, name="publication")


# --- config test command (lives here, not in config.py, because it needs
#     client.py which imports auth.py which imports config.py — see §1.3.1).
@config_app.command("test")
def config_test_cmd(pretty: bool = False):
    """Verify auth setup by hitting the Substack API."""
    from substack_cli.auth import AuthError, resolve_cookies, resolve_publication_url
    from substack_cli.client import SubstackClient, SubstackApiError, emit_error, output

    try:
        cookies = resolve_cookies()
        pub_url = resolve_publication_url()
        client = SubstackClient(cookies=cookies, publication_url=pub_url)

        try:
            profile = client.get("/api/v1/user/profile/self", host="A")
            output(
                {"status": "ok", "user": profile.get("name", "unknown")},
                pretty=pretty,
            )
        except SubstackApiError as exc:
            if exc.status_code in (401, 403):
                emit_error(
                    f"Authentication failed ({exc.status_code}). "
                    "Your session cookies may have expired. "
                    "Re-extract them from your browser DevTools "
                    "and run `substack config set-cookies <STRING>`.",
                    status_code=exc.status_code,
                    pretty=pretty,
                )
            raise
    except AuthError as exc:
        emit_error(str(exc), pretty=pretty)
    except SubstackApiError as exc:
        emit_error(str(exc), status_code=exc.status_code, pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


def main() -> None:
    """Top-level entry point — wraps the Typer app in a try/except so no
    raw Python tracebacks ever reach the user."""
    from substack_cli.client import SubstackApiError, emit_error
    from substack_cli.auth import AuthError

    try:
        app()
    except (SubstackApiError, AuthError) as exc:
        emit_error(
            str(exc),
            status_code=getattr(exc, "status_code", None),
        )
    except SystemExit:
        raise
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}")