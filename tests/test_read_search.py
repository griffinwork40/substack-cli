"""Tests for substack_cli.read — search."""
import httpx
import respx

from substack_cli.client import SubstackClient
from substack_cli.read import search_archive


@respx.mock
def test_search_archive_passes_query_as_search_param(fake_cookies, fake_publication_url):
    route = respx.get(f"{fake_publication_url}/api/v1/archive").mock(
        return_value=httpx.Response(200, json=[])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    search_archive(client, "AI infrastructure")
    params = route.calls[0].request.url.params
    assert params["search"] == "AI infrastructure"


@respx.mock
def test_search_archive_respects_limit(fake_cookies, fake_publication_url):
    route = respx.get(f"{fake_publication_url}/api/v1/archive").mock(
        return_value=httpx.Response(200, json=[])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    search_archive(client, "test", limit=10)
    params = route.calls[0].request.url.params
    assert params["limit"] == "10"


def test_search_archive_docstring_or_output_flags_unconfirmed_filtering():
    """Documentation-completeness test — the docstring must warn that
    Substack's search filtering behavior is unconfirmed."""
    from substack_cli import read
    doc = read.search_archive.__doc__ or ""
    assert "unconfirmed" in doc.lower() or "caveat" in doc.lower()