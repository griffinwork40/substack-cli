"""Tests for substack_cli.read — category leaderboard (cross-publication
discovery). All HTTP mocked via respx with a small hand-built fixture —
never the live payload (a real category response is ~4MB / ~1M tokens).
"""
import httpx
import pytest
import respx

from substack_cli.client import SUBSTACK_COM, SubstackClient
from substack_cli.read import (
    LEADERBOARD_CATEGORY_ALIASES,
    _extract_plan_prices,
    _parse_subscriber_count,
    _project_leaderboard_entry,
    _resolve_leaderboard_category,
    get_category_leaderboard,
)

# ---------------------------------------------------------------------------
# Small hand-built fixture — 3 fake publications covering:
#   - "Standard Finance Weekly": monthly + yearly plan (simple case)
#   - "Founders Club Digest": TWO yearly plans (standard + pricier founding
#     tier) — exercises the min-amount-per-interval selection logic
#   - "Free Reach Rag": payments disabled, no `plans` at all, no ranking
#     bands — only shows up on /all, exercises None-handling
# ---------------------------------------------------------------------------

FAKE_PUBLICATIONS = [
    {
        "id": 1,
        "name": "Standard Finance Weekly",
        "author_name": "Jane Analyst",
        "base_url": "https://standardfinance.substack.com",
        "custom_domain": None,
        "payments_state": "enabled",
        "author_bestseller_tier": 1000,
        "rankingDetail": "Thousands of paid subscribers",
        "rankingDetailFreeIncluded": "Tens of thousands of subscribers",
        "freeSubscriberCount": "45,000",
        "plans": [
            {"interval": "month", "amount": 600, "currency": "usd"},
            {"interval": "year", "amount": 6000, "currency": "usd"},
        ],
    },
    {
        "id": 2,
        "name": "Founders Club Digest",
        "author_name": "Sam Founder",
        "base_url": "https://www.foundersclub.example",
        "custom_domain": "www.foundersclub.example",
        "payments_state": "enabled",
        "author_bestseller_tier": 10000,
        "rankingDetail": "Tens of thousands of paid subscribers",
        "rankingDetailFreeIncluded": "Hundreds of thousands of subscribers",
        "freeSubscriberCount": "300,000",
        "plans": [
            {"interval": "year", "amount": 9900, "currency": "usd"},
            {"interval": "year", "amount": 199900, "currency": "usd"},  # founding tier
        ],
    },
    {
        "id": 3,
        "name": "Free Reach Rag",
        "author_name": "Pat Publisher",
        "base_url": "https://freereach.substack.com",
        "custom_domain": None,
        "payments_state": "disabled",
        "author_bestseller_tier": 0,
        "rankingDetail": None,
        "rankingDetailFreeIncluded": None,
        "freeSubscriberCount": None,
        "plans": None,
    },
]

FAKE_ENVELOPE = {
    "publications": FAKE_PUBLICATIONS,
    "more": False,
    "title": "Top in Finance",
}


# ---------------------------------------------------------------------------
# (1) Hits the substack.com host + correct path for the chosen rank
# ---------------------------------------------------------------------------


@respx.mock
def test_get_category_leaderboard_hits_substack_com_host_and_path(
    fake_cookies, fake_publication_url
):
    route = respx.get(f"{SUBSTACK_COM}/api/v1/category/public/153/paid").mock(
        return_value=httpx.Response(200, json=FAKE_ENVELOPE)
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_category_leaderboard(client, 153, rank="paid")
    assert route.called
    assert result == FAKE_PUBLICATIONS


@respx.mock
@pytest.mark.parametrize("rank", ["paid", "all", "rising"])
def test_get_category_leaderboard_url_path_reflects_rank(
    rank, fake_cookies, fake_publication_url
):
    route = respx.get(f"{SUBSTACK_COM}/api/v1/category/public/153/{rank}").mock(
        return_value=httpx.Response(200, json=FAKE_ENVELOPE)
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    get_category_leaderboard(client, 153, rank=rank)
    assert route.called
    assert route.calls[0].request.url.path.endswith(f"/category/public/153/{rank}")


@respx.mock
def test_get_category_leaderboard_tolerates_bare_list_envelope(
    fake_cookies, fake_publication_url
):
    """Defensive: extract_list must also tolerate a bare-array shape, in
    case Substack ever drops the {publications, more, title} envelope."""
    respx.get(f"{SUBSTACK_COM}/api/v1/category/public/153/paid").mock(
        return_value=httpx.Response(200, json=FAKE_PUBLICATIONS)
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_category_leaderboard(client, 153, rank="paid")
    assert result == FAKE_PUBLICATIONS


# ---------------------------------------------------------------------------
# (2) Default rank is paid
# ---------------------------------------------------------------------------


@respx.mock
def test_get_category_leaderboard_default_rank_is_paid(fake_cookies, fake_publication_url):
    route = respx.get(f"{SUBSTACK_COM}/api/v1/category/public/153/paid").mock(
        return_value=httpx.Response(200, json=FAKE_ENVELOPE)
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    get_category_leaderboard(client, 153)  # rank omitted
    assert route.called


@respx.mock
def test_leaderboard_cli_default_rank_is_paid(authed_env):
    """CLI-level: omitting --rank must hit the /paid path, not /all."""
    from typer.testing import CliRunner

    from substack_cli import read as _read_module  # noqa: F401
    from substack_cli.app import app

    route = respx.get(f"{SUBSTACK_COM}/api/v1/category/public/153/paid").mock(
        return_value=httpx.Response(200, json=FAKE_ENVELOPE)
    )
    runner = CliRunner()
    result = runner.invoke(app, ["leaderboard", "153"])
    assert result.exit_code == 0, result.output
    assert route.called


def test_get_category_leaderboard_rejects_invalid_rank(fake_cookies, fake_publication_url):
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(ValueError):
        get_category_leaderboard(client, 153, rank="bogus")


# ---------------------------------------------------------------------------
# (3) Slug alias resolution
# ---------------------------------------------------------------------------


def test_resolve_leaderboard_category_finance_alias():
    assert _resolve_leaderboard_category("finance") == 153


def test_resolve_leaderboard_category_us_politics_alias():
    assert _resolve_leaderboard_category("us-politics") == 76739


def test_resolve_leaderboard_category_alias_case_insensitive():
    assert _resolve_leaderboard_category("Finance") == 153


def test_resolve_leaderboard_category_numeric_id_passthrough():
    assert _resolve_leaderboard_category("153") == 153
    assert _resolve_leaderboard_category("999999") == 999999


def test_resolve_leaderboard_category_unicode_digit_like_char_raises_clear_message():
    """Regression: '\u00b2' (superscript 2, U+00B2) passes str.isdigit() but
    int() rejects it — must fall through to the friendly alias/error message
    instead of leaking a raw 'invalid literal for int()' ValueError."""
    with pytest.raises(ValueError) as exc_info:
        _resolve_leaderboard_category("\u00b2")
    msg = str(exc_info.value)
    assert "numeric category id" in msg
    assert "finance" in msg and "us-politics" in msg


def test_resolve_leaderboard_category_unknown_slug_raises_clear_message():
    with pytest.raises(ValueError) as exc_info:
        _resolve_leaderboard_category("technology")
    msg = str(exc_info.value)
    assert "numeric category id" in msg
    assert "finance" in msg and "us-politics" in msg


def test_leaderboard_aliases_only_contains_live_verified_entries():
    """Guards against silently adding unverified aliases later."""
    assert LEADERBOARD_CATEGORY_ALIASES == {"finance": 153, "us-politics": 76739}


@respx.mock
def test_leaderboard_cli_resolves_finance_alias_to_153(authed_env):
    from typer.testing import CliRunner

    from substack_cli import read as _read_module  # noqa: F401
    from substack_cli.app import app

    route = respx.get(f"{SUBSTACK_COM}/api/v1/category/public/153/paid").mock(
        return_value=httpx.Response(200, json=FAKE_ENVELOPE)
    )
    runner = CliRunner()
    result = runner.invoke(app, ["leaderboard", "finance"])
    assert result.exit_code == 0, result.output
    assert route.called


@respx.mock
def test_leaderboard_cli_unknown_slug_errors_clearly_and_makes_no_http_call(authed_env):
    from typer.testing import CliRunner

    from substack_cli import read as _read_module  # noqa: F401
    from substack_cli.app import app

    runner = CliRunner()
    result = runner.invoke(app, ["leaderboard", "technology"])
    assert result.exit_code != 0
    combined = (result.stdout or "") + (result.output or "")
    assert "numeric category id" in combined
    assert len(respx.calls) == 0


# ---------------------------------------------------------------------------
# (4) Price parsing — cents to dollars, monthly vs annual
# ---------------------------------------------------------------------------


def test_extract_plan_prices_simple_monthly_and_yearly():
    plans = [
        {"interval": "month", "amount": 600, "currency": "usd"},
        {"interval": "year", "amount": 6000, "currency": "usd"},
    ]
    monthly, yearly = _extract_plan_prices(plans)
    assert monthly == 6.00
    assert yearly == 60.00


def test_extract_plan_prices_picks_minimum_when_founding_tier_present():
    """A second, pricier yearly plan (founding tier) must NOT overwrite the
    standard price — the minimum-amount plan per interval wins."""
    plans = [
        {"interval": "year", "amount": 9900, "currency": "usd"},
        {"interval": "year", "amount": 199900, "currency": "usd"},
    ]
    monthly, yearly = _extract_plan_prices(plans)
    assert monthly is None
    assert yearly == 99.00


def test_extract_plan_prices_none_when_no_plans():
    assert _extract_plan_prices(None) == (None, None)
    assert _extract_plan_prices([]) == (None, None)


def test_extract_plan_prices_ignores_non_usd_currency():
    plans = [{"interval": "month", "amount": 500, "currency": "eur"}]
    monthly, _ = _extract_plan_prices(plans)
    assert monthly is None


@respx.mock
def test_leaderboard_projects_correct_prices_for_all_three_fixture_pubs(
    fake_cookies, fake_publication_url
):
    respx.get(f"{SUBSTACK_COM}/api/v1/category/public/153/paid").mock(
        return_value=httpx.Response(200, json=FAKE_ENVELOPE)
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    raw = get_category_leaderboard(client, 153, rank="paid")
    projected = [_project_leaderboard_entry(pub, position=i + 1) for i, pub in enumerate(raw)]

    standard, founders, free_reach = projected
    assert standard["monthly_usd"] == 6.00
    assert standard["yearly_usd"] == 60.00
    assert founders["monthly_usd"] is None
    assert founders["yearly_usd"] == 99.00  # min of the two yearly plans, not 1999.00
    assert free_reach["monthly_usd"] is None
    assert free_reach["yearly_usd"] is None


# ---------------------------------------------------------------------------
# (5) --top trims results
# ---------------------------------------------------------------------------


@respx.mock
def test_leaderboard_cli_top_trims_to_first_n(authed_env):
    import json

    from typer.testing import CliRunner

    from substack_cli import read as _read_module  # noqa: F401
    from substack_cli.app import app

    respx.get(f"{SUBSTACK_COM}/api/v1/category/public/153/paid").mock(
        return_value=httpx.Response(200, json=FAKE_ENVELOPE)
    )
    runner = CliRunner()
    result = runner.invoke(app, ["leaderboard", "153", "--top", "2"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout.strip())
    assert len(parsed) == 2
    assert parsed[0]["name"] == "Standard Finance Weekly"
    assert parsed[1]["name"] == "Founders Club Digest"


@respx.mock
def test_leaderboard_cli_no_top_returns_all_results(authed_env):
    import json

    from typer.testing import CliRunner

    from substack_cli import read as _read_module  # noqa: F401
    from substack_cli.app import app

    respx.get(f"{SUBSTACK_COM}/api/v1/category/public/153/paid").mock(
        return_value=httpx.Response(200, json=FAKE_ENVELOPE)
    )
    runner = CliRunner()
    result = runner.invoke(app, ["leaderboard", "153"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout.strip())
    assert len(parsed) == 3


# ---------------------------------------------------------------------------
# (6) Band / total-subscriber fields extracted
# ---------------------------------------------------------------------------


def test_parse_subscriber_count_parses_comma_formatted_string():
    assert _parse_subscriber_count("774,000") == 774000
    assert _parse_subscriber_count("45,000") == 45000


def test_parse_subscriber_count_none_when_missing():
    assert _parse_subscriber_count(None) is None


def test_parse_subscriber_count_none_when_unparseable():
    assert _parse_subscriber_count("not a number") is None


def test_project_leaderboard_entry_extracts_band_and_total_subscribers():
    entry = _project_leaderboard_entry(FAKE_PUBLICATIONS[0], position=1)
    assert entry["paid_subscriber_band"] == "Thousands of paid subscribers"
    assert entry["total_subscribers"] == 45000
    assert entry["rank"] == 1


def test_project_leaderboard_entry_handles_missing_bands_gracefully():
    """Free Reach Rag has no ranking bands / subscriber count at all —
    projection must not raise, and must surface None rather than KeyError."""
    entry = _project_leaderboard_entry(FAKE_PUBLICATIONS[2], position=3)
    assert entry["paid_subscriber_band"] is None
    assert entry["total_subscribers"] is None
    assert entry["payments_state"] == "disabled"


def test_project_leaderboard_entry_projects_only_known_fields():
    """The raw fixture object carries other keys (id) that must NOT leak
    into the projection — only the documented, useful fields are emitted."""
    entry = _project_leaderboard_entry(FAKE_PUBLICATIONS[0], position=1)
    assert set(entry.keys()) == {
        "rank",
        "name",
        "author",
        "url",
        "monthly_usd",
        "yearly_usd",
        "paid_subscriber_band",
        "total_subscribers",
        "payments_state",
        "bestseller_tier",
    }


def test_project_leaderboard_entry_falls_back_to_custom_domain_for_url():
    pub = dict(FAKE_PUBLICATIONS[1])
    pub["base_url"] = None
    entry = _project_leaderboard_entry(pub, position=1)
    assert entry["url"] == "https://www.foundersclub.example"


# ---------------------------------------------------------------------------
# Full CLI wiring — anonymous access, no cookie or publication required
# ---------------------------------------------------------------------------


@respx.mock
def test_leaderboard_cli_works_with_no_cookies_and_no_publication(isolated_config):
    """leaderboard is public and cross-publication — it must succeed with
    NO cookie and NO configured publication URL at all."""
    import json

    from typer.testing import CliRunner

    from substack_cli import read as _read_module  # noqa: F401
    from substack_cli.app import app

    route = respx.get(f"{SUBSTACK_COM}/api/v1/category/public/153/paid").mock(
        return_value=httpx.Response(200, json=FAKE_ENVELOPE)
    )
    runner = CliRunner()
    result = runner.invoke(app, ["leaderboard", "153"])
    assert result.exit_code == 0, result.output
    assert route.called
    parsed = json.loads(result.stdout.strip())
    assert len(parsed) == 3
    assert parsed[0]["name"] == "Standard Finance Weekly"


@respx.mock
def test_leaderboard_cli_pretty_flag_renders_without_error(authed_env):
    from typer.testing import CliRunner

    from substack_cli import read as _read_module  # noqa: F401
    from substack_cli.app import app

    respx.get(f"{SUBSTACK_COM}/api/v1/category/public/153/paid").mock(
        return_value=httpx.Response(200, json=FAKE_ENVELOPE)
    )
    runner = CliRunner()
    result = runner.invoke(app, ["leaderboard", "153", "--pretty"])
    assert result.exit_code == 0, result.output
