"""Tests for substack_cli.read — post analytics."""
import httpx
import respx

from substack_cli.client import SubstackClient
from substack_cli.read import get_post_analytics


@respx.mock
def test_get_post_analytics_hits_detail_endpoint_with_post_id(fake_cookies, fake_publication_url):
    route = respx.get(f"{fake_publication_url}/api/v1/post_management/detail/123").mock(
        return_value=httpx.Response(200, json={"views": 100, "opens": 50})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_post_analytics(client, 123)
    assert "123" in str(route.calls[0].request.url)
    assert result == {"views": 100, "opens": 50}


@respx.mock
def test_get_post_analytics_returns_stats_block(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/api/v1/post_management/detail/456").mock(
        return_value=httpx.Response(200, json={"views": 100, "opens": 50, "clicks": 10})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_post_analytics(client, 456)
    assert result == {"views": 100, "opens": 50, "clicks": 10}