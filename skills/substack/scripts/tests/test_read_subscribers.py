"""Tests for substack_cli.read — subscriber stats and dashboard."""
import json

import httpx
import pytest
import respx

from substack_cli.client import SubstackApiError, SubstackClient
from substack_cli.read import get_publish_dashboard_summary, get_subscriber_stats


@respx.mock
def test_get_subscriber_stats_issues_post_with_filters_limit_offset_body(fake_cookies, fake_publication_url):
    route = respx.post(f"{fake_publication_url}/api/v1/subscriber-stats").mock(
        return_value=httpx.Response(200, json={"count": 0, "subscribers": [], "total": 22000, "free": 21000, "paid": 1000})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_subscriber_stats(client)
    assert route.called
    assert route.calls[0].request.method == "POST"
    body = json.loads(route.calls[0].request.content)
    assert "filters" in body
    assert "limit" in body
    assert "offset" in body
    # With count=0 and no subscribers, auto-pagination returns first page unchanged
    assert result["total"] == 22000


@respx.mock
def test_get_subscriber_stats_unexpected_404_raises_clear_verb_drift_message(fake_cookies, fake_publication_url):
    respx.post(f"{fake_publication_url}/api/v1/subscriber-stats").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError) as exc_info:
        get_subscriber_stats(client)
    msg = str(exc_info.value).lower()
    assert "verb" in msg or "subscriber-stats" in msg


@respx.mock
def test_get_subscriber_stats_explicit_limit_returns_single_page(fake_cookies, fake_publication_url):
    """--limit 10 issues one POST with limit=10 and returns it directly."""
    route = respx.post(f"{fake_publication_url}/api/v1/subscriber-stats").mock(
        return_value=httpx.Response(
            200,
            json={"count": 100, "subscribers": [{"id": i} for i in range(10)]},
        )
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_subscriber_stats(client, limit=10)
    assert route.call_count == 1
    body = json.loads(route.calls[0].request.content)
    assert body["limit"] == 10
    assert body["offset"] == 0
    assert len(result["subscribers"]) == 10


@respx.mock
def test_get_subscriber_stats_explicit_offset_is_forwarded(fake_cookies, fake_publication_url):
    """--offset 50 is passed through to the API body."""
    route = respx.post(f"{fake_publication_url}/api/v1/subscriber-stats").mock(
        return_value=httpx.Response(
            200,
            json={"count": 100, "subscribers": [{"id": i} for i in range(50, 75)]},
        )
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_subscriber_stats(client, offset=50, limit=25)
    assert route.call_count == 1
    body = json.loads(route.calls[0].request.content)
    assert body["offset"] == 50
    assert body["limit"] == 25
    assert result["subscribers"][0]["id"] == 50


@respx.mock
def test_get_subscriber_stats_auto_paginates_all(fake_cookies, fake_publication_url):
    """limit=0 (default) auto-paginates until all subscribers are collected."""
    page_size = 25
    total = 60  # 3 pages: 25 + 25 + 10

    def _page_response(request):
        body = json.loads(request.content)
        off = body["offset"]
        sz = body["limit"]
        subs = [{"id": i} for i in range(off, min(off + sz, total))]
        return httpx.Response(200, json={"count": total, "subscribers": subs})

    respx.post(f"{fake_publication_url}/api/v1/subscriber-stats").mock(
        side_effect=_page_response
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_subscriber_stats(client)  # limit=0 → fetch all
    assert len(result["subscribers"]) == total
    assert [s["id"] for s in result["subscribers"]] == list(range(total))


@respx.mock
def test_get_subscriber_stats_auto_paginate_stops_on_empty_page(fake_cookies, fake_publication_url):
    """Auto-pagination stops cleanly if an API page returns no subscribers."""
    call_count = 0

    def _page_response(request):
        nonlocal call_count
        call_count += 1
        body = json.loads(request.content)
        # Return subscribers only on first call; second call returns empty list
        if call_count == 1:
            subs = [{"id": i} for i in range(25)]
        else:
            subs = []
        return httpx.Response(200, json={"count": 50, "subscribers": subs})

    respx.post(f"{fake_publication_url}/api/v1/subscriber-stats").mock(
        side_effect=_page_response
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_subscriber_stats(client)
    # Stopped after empty page despite count=50
    assert len(result["subscribers"]) == 25


@respx.mock
def test_get_publish_dashboard_summary_returns_dict(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/api/v1/publish-dashboard/summary").mock(
        return_value=httpx.Response(200, json={"subscribers": 22000, "open_rate": 0.45})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_publish_dashboard_summary(client)
    assert result == {"subscribers": 22000, "open_rate": 0.45}
