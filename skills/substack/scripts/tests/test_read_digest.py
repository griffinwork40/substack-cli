"""Tests for substack_cli.read — bulk post analytics + digest (Milestone 1).

Covers the flywheel's Analyze step: get_posts_analytics (ranking, stat-shape
tolerance, graceful degradation) and get_digest (composition). All HTTP is
respx-mocked; `no_sleep` neutralizes the client throttle between the N+1 calls.
"""
import json

import httpx
import pytest
import respx

# Import command modules so their commands register on app/sub-apps.
from substack_cli import config as _config_module  # noqa: F401
from substack_cli import read as _read_module  # noqa: F401
from substack_cli import publish as _publish_module  # noqa: F401
from substack_cli import manage as _manage_module  # noqa: F401
from substack_cli import notes as _notes_module  # noqa: F401
from substack_cli.app import app
from substack_cli.client import SubstackClient
from substack_cli.read import (
    _extract_post_stats,
    _stat_value,
    get_digest,
    get_posts_analytics,
)

PUB = "https://charliepgarcia.substack.com"


# --- pure helpers (no network) ---------------------------------------------


def test_stat_value_missing_metric_is_zero():
    assert _stat_value({}, "signups") == 0.0


def test_stat_value_reads_first_present_alias():
    # "signups" absent but "free_signups" present -> alias resolves
    assert _stat_value({"free_signups": 7}, "signups") == 7.0


def test_stat_value_ignores_bool_masquerading_as_number():
    # bools are ints in Python; a True must NOT count as 1.0
    assert _stat_value({"signups": True}, "signups") == 0.0


def test_stat_value_non_dict_is_zero():
    assert _stat_value(None, "signups") == 0.0  # type: ignore[arg-type]


def test_extract_post_stats_nested_envelope():
    assert _extract_post_stats({"stats": {"views": 3}}) == {"views": 3}


def test_extract_post_stats_inline_metrics():
    assert _extract_post_stats({"views": 3, "opens": 1}) == {"views": 3, "opens": 1}


def test_extract_post_stats_posts_list_shape():
    detail = {"posts": [{"stats": {"clicks": 9}}]}
    assert _extract_post_stats(detail) == {"clicks": 9}


def test_extract_post_stats_non_dict_is_empty():
    assert _extract_post_stats("nope") == {}


# --- get_posts_analytics ----------------------------------------------------


@respx.mock
def test_posts_analytics_ranks_desc_by_signups(fake_cookies, no_sleep):
    respx.get(f"{PUB}/api/v1/archive").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 1, "title": "Low", "slug": "low", "post_date": "2026-01-01"},
                {"id": 2, "title": "High", "slug": "high", "post_date": "2026-01-02"},
            ],
        )
    )
    respx.get(f"{PUB}/api/v1/post_management/detail/1").mock(
        return_value=httpx.Response(200, json={"stats": {"signups": 5, "views": 100}})
    )
    respx.get(f"{PUB}/api/v1/post_management/detail/2").mock(
        return_value=httpx.Response(200, json={"stats": {"signups": 50, "views": 80}})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=PUB)
    result = get_posts_analytics(client, limit=25, sort="signups")
    assert [r["id"] for r in result] == [2, 1]
    assert result[0]["stats"]["signups"] == 50
    assert result[0]["title"] == "High"


@respx.mock
def test_posts_analytics_tolerates_inline_stats_and_sorts_open_rate(
    fake_cookies, no_sleep
):
    respx.get(f"{PUB}/api/v1/archive").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 7, "title": "A", "slug": "a"},
                {"id": 8, "title": "B", "slug": "b"},
            ],
        )
    )
    # stats inlined at top level (no nested "stats" key)
    respx.get(f"{PUB}/api/v1/post_management/detail/7").mock(
        return_value=httpx.Response(200, json={"open_rate": 0.42})
    )
    respx.get(f"{PUB}/api/v1/post_management/detail/8").mock(
        return_value=httpx.Response(200, json={"open_rate": 0.10})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=PUB)
    result = get_posts_analytics(client, limit=25, sort="open_rate")
    assert [r["id"] for r in result] == [7, 8]
    assert result[0]["stats"]["open_rate"] == 0.42


@respx.mock
def test_posts_analytics_degrades_when_detail_errors(fake_cookies, no_sleep):
    respx.get(f"{PUB}/api/v1/archive").mock(
        return_value=httpx.Response(200, json=[{"id": 9, "title": "Y", "slug": "y"}])
    )
    respx.get(f"{PUB}/api/v1/post_management/detail/9").mock(
        return_value=httpx.Response(404, json={"error": "nope"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=PUB)
    result = get_posts_analytics(client, limit=25, sort="signups")
    assert len(result) == 1
    assert result[0]["id"] == 9
    assert result[0]["stats"] == {}  # kept, but no stats


@respx.mock
def test_posts_analytics_skips_stats_for_post_without_id(fake_cookies, no_sleep):
    # A post with no "id" must not trigger a detail call, and stays listed.
    respx.get(f"{PUB}/api/v1/archive").mock(
        return_value=httpx.Response(200, json=[{"title": "No id", "slug": "no-id"}])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=PUB)
    result = get_posts_analytics(client, limit=25, sort="signups")
    assert result[0]["id"] is None
    assert result[0]["stats"] == {}
    # only the archive call happened — no detail request
    assert len(respx.calls) == 1


def test_posts_analytics_rejects_unknown_sort(fake_cookies):
    client = SubstackClient(cookies=fake_cookies, publication_url=PUB)
    with pytest.raises(ValueError, match="sort must be one of"):
        get_posts_analytics(client, sort="bogus")


# --- get_digest -------------------------------------------------------------


@respx.mock
def test_get_digest_composes_summary_and_top_posts(fake_cookies, no_sleep):
    respx.get(f"{PUB}/api/v1/archive").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 1, "title": "A", "slug": "a"},
                {"id": 2, "title": "B", "slug": "b"},
            ],
        )
    )
    respx.get(f"{PUB}/api/v1/post_management/detail/1").mock(
        return_value=httpx.Response(200, json={"stats": {"signups": 10}})
    )
    respx.get(f"{PUB}/api/v1/post_management/detail/2").mock(
        return_value=httpx.Response(200, json={"stats": {"signups": 2}})
    )
    respx.get(f"{PUB}/api/v1/publish-dashboard/summary").mock(
        return_value=httpx.Response(200, json={"total_subscribers": 1234})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=PUB)
    result = get_digest(client, top=1)
    assert result["summary"]["total_subscribers"] == 1234
    assert result["ranked_by"] == "signups"
    assert result["post_count"] == 2
    assert len(result["top_posts"]) == 1
    assert result["top_posts"][0]["id"] == 1  # highest signups


# --- command wiring ---------------------------------------------------------


@respx.mock
def test_analytics_posts_command_runs_and_returns_json(authed_env, no_sleep):
    respx.get(f"{PUB}/api/v1/archive").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "title": "P", "slug": "p"}])
    )
    respx.get(f"{PUB}/api/v1/post_management/detail/1").mock(
        return_value=httpx.Response(200, json={"stats": {"signups": 3}})
    )
    from typer.testing import CliRunner

    result = CliRunner().invoke(app, ["analytics", "posts", "--limit", "5"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout.strip())
    assert parsed[0]["id"] == 1
    assert parsed[0]["stats"]["signups"] == 3


@respx.mock
def test_analytics_digest_command_runs_and_returns_json(authed_env, no_sleep):
    respx.get(f"{PUB}/api/v1/archive").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "title": "P", "slug": "p"}])
    )
    respx.get(f"{PUB}/api/v1/post_management/detail/1").mock(
        return_value=httpx.Response(200, json={"stats": {"signups": 3}})
    )
    respx.get(f"{PUB}/api/v1/publish-dashboard/summary").mock(
        return_value=httpx.Response(200, json={"total_subscribers": 42})
    )
    from typer.testing import CliRunner

    result = CliRunner().invoke(app, ["analytics", "digest", "--top", "3"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout.strip())
    assert parsed["summary"]["total_subscribers"] == 42
    assert parsed["top_posts"][0]["id"] == 1


@respx.mock
def test_analytics_posts_command_bad_sort_exits_nonzero_without_network(authed_env):
    from typer.testing import CliRunner

    result = CliRunner().invoke(app, ["analytics", "posts", "--sort", "bogus"])
    assert result.exit_code != 0
    # validation short-circuits before any HTTP call
    assert len(respx.calls) == 0
