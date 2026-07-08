"""Tests for substack_cli.read — subscriber stats and dashboard."""
import httpx
import pytest
import respx

from substack_cli.client import SubstackApiError, SubstackClient
from substack_cli.read import get_publish_dashboard_summary, get_subscriber_stats


@respx.mock
def test_get_subscriber_stats_issues_post_with_filters_limit_offset_body(fake_cookies, fake_publication_url):
    route = respx.post(f"{fake_publication_url}/api/v1/subscriber-stats").mock(
        return_value=httpx.Response(200, json={"total": 22000, "free": 21000, "paid": 1000})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_subscriber_stats(client)
    assert route.called
    assert route.calls[0].request.method == "POST"
    import json
    body = json.loads(route.calls[0].request.content)
    assert "filters" in body
    assert "limit" in body
    assert "offset" in body
    assert result == {"total": 22000, "free": 21000, "paid": 1000}


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
def test_get_publish_dashboard_summary_returns_dict(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/api/v1/publish-dashboard/summary").mock(
        return_value=httpx.Response(200, json={"subscribers": 22000, "open_rate": 0.45})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_publish_dashboard_summary(client)
    assert result == {"subscribers": 22000, "open_rate": 0.45}