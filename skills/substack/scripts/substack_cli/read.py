"""Substack CLI — read operations (archive, posts, feed, comments, search,
subscriber stats, analytics). All GET-only except subscriber-stats (POST)."""
import xml.etree.ElementTree as ET
from typing import Any, Optional

import typer

from substack_cli.app import app, comments_app, subscribers_app
from substack_cli.auth import (
    AuthError,
    resolve_cookies,
    resolve_cookies_optional,
    resolve_publication_url,
)
from substack_cli.client import (
    SUBSTACK_COM,
    SubstackApiError,
    SubstackClient,
    emit_error,
    output,
    output_list,
)
from substack_cli.models import extract_list

# Analytics sub-app — created here and registered on app
analytics_app = typer.Typer(help="Post and publication analytics.")
app.add_typer(analytics_app, name="analytics")


def _make_client(anonymous: bool = False) -> SubstackClient:
    """Resolve auth and create a SubstackClient.

    When anonymous=True (public endpoints: archive, post, feed), a cookie is
    used if configured but its absence is NOT an error. Every other command
    leaves anonymous=False and still requires cookies.
    """
    cookies = resolve_cookies_optional() if anonymous else resolve_cookies()
    pub_url = resolve_publication_url()
    return SubstackClient(cookies=cookies, publication_url=pub_url)


def _make_leaderboard_client() -> SubstackClient:
    """Create a client for `leaderboard` — a public, cross-publication,
    host-'A'-only endpoint. No auth and no configured publication are
    required (mirrors notes.py's SUBSTACK_COM fallback), since a user
    should be able to discover OTHER publications without owning one."""
    cookies = resolve_cookies_optional()
    try:
        pub_url = resolve_publication_url()
    except AuthError:
        pub_url = SUBSTACK_COM
    return SubstackClient(cookies=cookies, publication_url=pub_url)


# ---------------------------------------------------------------------------
# Read functions
# ---------------------------------------------------------------------------


def get_archive(
    client: SubstackClient,
    *,
    sort: str = "new",
    offset: int = 0,
    limit: int = 25,
    search: Optional[str] = None,
) -> list:
    """List published posts from the archive. Uses /api/v1/archive (singular).
    Tolerates both bare-array and {posts, hasMore} envelope shapes via extract_list."""
    params: dict = {"sort": sort, "offset": offset, "limit": limit}
    if search is not None:
        params["search"] = search
    data = client.get("/api/v1/archive", **params)
    return extract_list(data, "posts")


def list_categories(client: SubstackClient) -> list:
    """List all Substack categories (global, not publication-specific).
    Uses host 'A' (substack.com)."""
    return client.get("/api/v1/categories", host="A")


def list_sections(client: SubstackClient) -> list:
    """List the publication's sections (multi-author/podcast sections).
    Uses the publication's own subdomain (host 'P')."""
    return client.get("/api/v1/publication/sections")


# ---------------------------------------------------------------------------
# Category leaderboard (cross-publication discovery)
# ---------------------------------------------------------------------------

# Only these two slugs are live-verified against the real category ids —
# do NOT add more without verifying, since a wrong id silently returns a
# leaderboard for the wrong category rather than erroring.
LEADERBOARD_CATEGORY_ALIASES = {
    "finance": 153,
    "us-politics": 76739,
}
LEADERBOARD_RANKS = ("paid", "all", "rising")


def _resolve_leaderboard_category(category: str) -> int:
    """Resolve a `leaderboard` CATEGORY argument to a numeric category id.

    Accepts a numeric id directly (always correct — discover ids via
    `substack categories`), or a live-verified slug alias (finance -> 153,
    us-politics -> 76739). Anything else raises ValueError with a message
    naming both remediation paths.
    """
    value = category.strip()
    if value.isdigit():
        # str.isdigit() is True for some Unicode numeral characters (e.g.
        # superscript "²", U+00B2) that int() then rejects — fall through
        # to the alias/error path instead of leaking a raw ValueError.
        try:
            return int(value)
        except ValueError:
            pass

    alias = LEADERBOARD_CATEGORY_ALIASES.get(value.lower())
    if alias is not None:
        return alias

    raise ValueError(
        "pass a numeric category id (discover ids via `substack categories`) "
        "or one of: finance, us-politics"
    )


def _extract_plan_prices(plans: Optional[list]) -> tuple:
    """Given a publication's `plans` array (raw leaderboard shape), return
    (monthly_usd, yearly_usd) as dollar floats, or None where absent.

    Each plan's `amount` is in CENTS; `interval` is "month" or "year".
    Founding-member tiers show up as a SECOND, pricier plan on the same
    interval with no reliable "is founding" flag, so the MINIMUM amount
    per interval is taken as the standard price. Only usd-denominated
    plans are considered (all live-observed leaderboard data is usd).
    """
    if not plans:
        return None, None

    def _min_amount(interval: str) -> Optional[float]:
        amounts = [
            plan["amount"]
            for plan in plans
            if plan.get("interval") == interval
            and plan.get("currency", "usd") == "usd"
            and isinstance(plan.get("amount"), (int, float))
        ]
        return round(min(amounts) / 100, 2) if amounts else None

    return _min_amount("month"), _min_amount("year")


def _parse_subscriber_count(value: Optional[str]) -> Optional[int]:
    """Parse a comma-formatted subscriber count string (e.g. "774,000") to
    an int. Returns None if missing or unparseable."""
    if value is None:
        return None
    try:
        return int(str(value).replace(",", "").strip())
    except ValueError:
        return None


def get_category_leaderboard(
    client: SubstackClient, category_id: int, rank: str = "paid"
) -> list:
    """Fetch a category's public leaderboard (top 25 publications).

    Uses host 'A' (substack.com) — a PUBLIC endpoint, no auth required.
    `rank` selects the ranking variant:
      - "paid" (DEFAULT): ranked by paid-subscriber count (bestsellers).
        This is what "top N in <category>" almost always means.
      - "all": ranked by total reach; INCLUDES publications with payments
        disabled/paused (no `plans`). Produces a DIFFERENT ordering than
        "paid" — do not substitute this for a paid-bestsellers list.
      - "rising": newer / fast-growing publications.

    Tolerates the {publications, more, title} envelope via extract_list.
    """
    if rank not in LEADERBOARD_RANKS:
        raise ValueError(
            f"Invalid rank {rank!r}. Choices: {', '.join(LEADERBOARD_RANKS)}"
        )
    data = client.get(f"/api/v1/category/public/{category_id}/{rank}", host="A")
    return extract_list(data, "publications")


def _project_leaderboard_entry(pub: dict, position: int) -> dict:
    """Project ONE raw (100+ field) leaderboard publication object down to
    the fields useful for cross-publication comparison. `position` is the
    1-indexed rank within the requested ordering — the API returns no
    explicit numeric rank field; order in the response IS the rank.

    NOTE on `freeSubscriberCount`: despite the name, at this endpoint it
    holds the TOTAL subscriber count (free + paid) — it lines up with the
    `rankingDetailFreeIncluded` total-subscribers band, not a free-only
    count. Live-verified against real leaderboard data.
    """
    monthly_usd, yearly_usd = _extract_plan_prices(pub.get("plans"))
    url = pub.get("base_url") or (
        f"https://{pub['custom_domain']}" if pub.get("custom_domain") else None
    )
    return {
        "rank": position,
        "name": pub.get("name"),
        "author": pub.get("author_name"),
        "url": url,
        "monthly_usd": monthly_usd,
        "yearly_usd": yearly_usd,
        "paid_subscriber_band": pub.get("rankingDetail"),
        "total_subscribers": _parse_subscriber_count(pub.get("freeSubscriberCount")),
        "payments_state": pub.get("payments_state"),
        "bestseller_tier": pub.get("author_bestseller_tier"),
    }


def get_post(client: SubstackClient, slug: str) -> dict:
    """Get a single post by its slug."""
    return client.get(f"/api/v1/posts/{slug}")


def get_self_profile(client: SubstackClient) -> dict:
    """Get the profile of the currently authenticated user.
    Uses host 'A' (substack.com)."""
    return client.get("/api/v1/user/profile/self", host="A")


def get_feed(client: SubstackClient, *, raw: bool = False) -> Any:
    """Fetch the publication's RSS feed (/feed endpoint).

    If raw=True, returns the raw XML string.
    If raw=False, parses the XML and returns a list of dicts with keys:
    title, link, pubDate, description (one dict per <item>).
    """
    response = client.get("/feed")
    if raw:
        return response if isinstance(response, str) else str(response)

    # Parse XML — client.get returns text for non-JSON content
    xml_text = response if isinstance(response, str) else str(response)
    items: list = []
    try:
        root = ET.fromstring(xml_text)
        for item_elem in root.findall(".//item"):
            item_dict: dict = {}
            for child in item_elem:
                tag = child.tag
                text = child.text or ""
                item_dict[tag] = text
            items.append(item_dict)
    except Exception:
        pass
    return items


def list_post_comments(client: SubstackClient, post_id: int) -> list:
    """List comments on a post. Uses extract_list to tolerate envelope shapes."""
    data = client.get(f"/api/v1/post/{post_id}/comments")
    return extract_list(data, "comments")


def search_archive(client: SubstackClient, query: str, *, limit: int = 25) -> list:
    """Search the publication's archive.

    CAVEAT: The search= parameter is accepted by /api/v1/archive, but
    Substack's filtering behavior is unconfirmed — the API may return
    the same results regardless of the search query. Do not rely on
    server-side filtering without verifying the results match.
    """
    data = client.get("/api/v1/archive", search=query, limit=limit)
    return extract_list(data, "posts")


def get_subscriber_stats(client: SubstackClient) -> dict:
    """Get subscriber statistics. Uses POST (per community documentation).
    If a 404 occurs, the verb may have changed — surfaces a clear message."""
    body: dict = {"filters": {}, "limit": 25, "offset": 0}
    try:
        return client.post("/api/v1/subscriber-stats", json_body=body)
    except SubstackApiError as exc:
        if exc.status_code == 404:
            raise SubstackApiError(
                "Unexpected 404 on subscriber-stats — the verb may have "
                "changed from POST. Verify the endpoint at "
                "substack-api-reference.md.",
                status_code=404,
            ) from exc
        raise


def get_publish_dashboard_summary(client: SubstackClient) -> dict:
    """Get the publish dashboard summary (subscriber count, open rates, etc.)."""
    return client.get("/api/v1/publish-dashboard/summary")


def get_post_analytics(client: SubstackClient, post_id: int) -> dict:
    """Get analytics (views, opens, clicks) for a specific post."""
    return client.get(f"/api/v1/post_management/detail/{post_id}")


# --- Bulk analytics + digest (Milestone 1: the flywheel's Analyze step) -----
#
# Defensive-by-design: the post list comes from the *verified* public archive
# endpoint, and per-post engagement stats from the *verified*
# post_management/detail endpoint (N+1 calls; the client throttles). A Phase-0
# spike may later show GET /api/v1/post_management/published carries a per-post
# `stats` block, collapsing this to ONE call — if so, swap the list source in
# get_posts_analytics() and drop the per-post loop. The exact upstream stat
# field names are unverified, so _extract_post_stats/_stat_value tolerate
# several envelope shapes and spellings. See
# docs/plans/amplify-content-flywheel.md (Phase 0, Milestone 1).

# Field-name aliases per sort metric; first present wins, else 0.
_STAT_SORT_FIELDS: dict = {
    "signups": ("signups", "free_signups", "total_signups", "new_free_subscriptions"),
    "open_rate": ("open_rate", "email_open_rate", "opens_rate"),
    "views": ("views", "total_views", "web_views"),
    "opens": ("opens", "email_opens", "unique_opens"),
    "clicks": ("clicks", "total_clicks", "unique_clicks"),
}


def _extract_post_stats(detail: Any) -> dict:
    """Pull the engagement-stats dict out of a post_management/detail response,
    tolerating a nested {"stats": {...}} envelope, a single-item
    {"posts": [ {...} ]} list, or a top-level metrics object."""
    if not isinstance(detail, dict):
        return {}
    stats = detail.get("stats")
    if isinstance(stats, dict):
        return stats
    posts = detail.get("posts")
    if isinstance(posts, list) and posts and isinstance(posts[0], dict):
        inner = posts[0].get("stats")
        return inner if isinstance(inner, dict) else posts[0]
    return detail


def _stat_value(stats: dict, sort: str) -> float:
    """Numeric value for a sort metric, tolerant of field-name variance; 0 when absent."""
    if not isinstance(stats, dict):
        return 0.0
    for key in _STAT_SORT_FIELDS.get(sort, (sort,)):
        val = stats.get(key)
        if isinstance(val, bool):  # bools are ints in Python — skip them
            continue
        if isinstance(val, (int, float)):
            return float(val)
    return 0.0


def get_posts_analytics(
    client: SubstackClient, *, limit: int = 25, sort: str = "signups"
) -> list:
    """Rank published posts by an engagement metric.

    `sort` ∈ {signups, open_rate, views, opens, clicks}. Lists posts from the
    archive, fetches per-post stats, and returns objects
    {id, title, slug, post_date, stats} sorted descending by `sort` (missing
    metric → 0, post still listed). Degrades gracefully: a post whose detail
    call fails is kept with stats={}."""
    if sort not in _STAT_SORT_FIELDS:
        raise ValueError(f"sort must be one of: {', '.join(_STAT_SORT_FIELDS)}")
    posts = get_archive(client, sort="new", offset=0, limit=limit)
    ranked: list = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        post_id = post.get("id")
        stats: dict = {}
        if post_id is not None:
            try:
                detail = client.get(
                    f"/api/v1/post_management/detail/{post_id}", offset=0, limit=1
                )
                stats = _extract_post_stats(detail)
            except SubstackApiError:
                stats = {}  # degrade: keep the post, drop its stats
        ranked.append(
            {
                "id": post_id,
                "title": post.get("title"),
                "slug": post.get("slug"),
                "post_date": post.get("post_date"),
                "stats": stats,
            }
        )
    ranked.sort(key=lambda r: _stat_value(r["stats"], sort), reverse=True)
    return ranked


def get_digest(client: SubstackClient, *, top: int = 5) -> dict:
    """Compose a 'what's working' digest: the publication summary plus the top
    posts ranked by signups. Emits ranked DATA only — narrative interpretation
    is left to the caller (the amplify skill)."""
    summary = get_publish_dashboard_summary(client)
    posts = get_posts_analytics(client, limit=max(top * 4, 25), sort="signups")
    return {
        "ranked_by": "signups",
        "post_count": len(posts),
        "top_posts": posts[:top],
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


@app.command("archive")
def archive_cmd(
    sort: str = "new",
    offset: int = 0,
    limit: int = 25,
    search: str = None,
    pretty: bool = False,
):
    """List published posts from the archive. Public — works without auth."""
    try:
        client = _make_client(anonymous=True)
        result = get_archive(client, sort=sort, offset=offset, limit=limit, search=search)
        output_list(result, pretty=pretty, title="Archive")
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@app.command("post")
def post_cmd(slug: str, pretty: bool = False):
    """Get a single post by slug. Public — works without auth."""
    try:
        client = _make_client(anonymous=True)
        result = get_post(client, slug)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@app.command("feed")
def feed_cmd(raw: bool = False, pretty: bool = False):
    """Fetch the publication's RSS feed. Public — works without auth."""
    try:
        client = _make_client(anonymous=True)
        result = get_feed(client, raw=raw)
        if raw:
            print(result)
        else:
            output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@app.command("search")
def search_cmd(query: str, limit: int = 25, pretty: bool = False):
    """Search the archive. CAVEAT: filtering behavior is unconfirmed."""
    try:
        client = _make_client()
        result = search_archive(client, query, limit=limit)
        output_list(result, pretty=pretty, title="Search Results")
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@app.command("whoami")
def whoami_cmd(pretty: bool = False):
    """Show the profile of the currently authenticated user."""
    try:
        client = _make_client()
        result = get_self_profile(client)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@app.command("categories")
def categories_cmd(pretty: bool = False):
    """List all Substack categories."""
    try:
        client = _make_client()
        result = list_categories(client)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@app.command("sections")
def sections_cmd(pretty: bool = False):
    """List publication sections."""
    try:
        client = _make_client()
        result = list_sections(client)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@app.command("leaderboard")
def leaderboard_cmd(
    category: str = typer.Argument(
        ...,
        help="Numeric category id (see `substack categories`), or a slug "
        "alias: finance, us-politics.",
    ),
    rank: str = typer.Option(
        "paid",
        "--rank",
        help="Ranking variant: paid (bestsellers by paid subs, DEFAULT), "
        "all (total reach, includes payments-off pubs), rising (fast-growing).",
    ),
    top: int = typer.Option(
        None, "--top", help="Trim to the first N results (default: all 25)."
    ),
    pretty: bool = False,
):
    """Cross-publication category leaderboard — top 25 publications ranked
    by category. Public — works without auth or a configured publication.

    Defaults to --rank paid (bestsellers by paid-subscriber count), which is
    almost always what "top N in <category>" means. --rank all reorders by
    total reach and additionally includes publications with payments
    disabled/paused — a DIFFERENT list, not a superset ordering of `paid`.
    """
    try:
        if rank not in LEADERBOARD_RANKS:
            raise ValueError(
                f"Invalid --rank {rank!r}. Choices: {', '.join(LEADERBOARD_RANKS)}"
            )
        category_id = _resolve_leaderboard_category(category)
        client = _make_leaderboard_client()
        raw = get_category_leaderboard(client, category_id, rank=rank)
        projected = [
            _project_leaderboard_entry(pub, position=i + 1)
            for i, pub in enumerate(raw)
        ]
        if top is not None:
            projected = projected[:top]
        output_list(projected, pretty=pretty, title=f"Leaderboard: {category} ({rank})")
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@comments_app.command("list")
def comments_list_cmd(post_id: int, pretty: bool = False):
    """List comments on a post."""
    try:
        client = _make_client()
        result = list_post_comments(client, post_id)
        output_list(result, pretty=pretty, title="Comments")
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@subscribers_app.command("count")
def subscribers_count_cmd(pretty: bool = False):
    """Get subscriber count and summary."""
    try:
        client = _make_client()
        result = get_publish_dashboard_summary(client)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@subscribers_app.command("stats")
def subscribers_stats_cmd(pretty: bool = False):
    """Get detailed subscriber statistics."""
    try:
        client = _make_client()
        result = get_subscriber_stats(client)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@analytics_app.command("summary")
def analytics_summary_cmd(pretty: bool = False):
    """Get the publish dashboard summary (subscriber count, open rates)."""
    try:
        client = _make_client()
        result = get_publish_dashboard_summary(client)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@analytics_app.command("post")
def analytics_post_cmd(post_id: int, pretty: bool = False):
    """Get analytics (views, opens, clicks) for a specific post."""
    try:
        client = _make_client()
        result = get_post_analytics(client, post_id)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@analytics_app.command("posts")
def analytics_posts_cmd(
    limit: int = 25, sort: str = "signups", pretty: bool = False
):
    """Rank published posts by an engagement metric (signups|open_rate|views|opens|clicks)."""
    try:
        client = _make_client()
        result = get_posts_analytics(client, limit=limit, sort=sort)
        output_list(result, pretty=pretty, title=f"Posts by {sort}")
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@analytics_app.command("digest")
def analytics_digest_cmd(top: int = 5, pretty: bool = False):
    """'What's working' digest: top posts by signups + publication summary (ranked JSON)."""
    try:
        client = _make_client()
        result = get_digest(client, top=top)
        output(result, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)