"""Tests for substack_cli.read — archive listing, categories, sections."""
import httpx
import respx

from substack_cli.client import SubstackClient, SUBSTACK_COM
from substack_cli.read import get_archive, list_categories, list_sections


@respx.mock
def test_get_archive_default_sort_new(fake_cookies, fake_publication_url):
    route = respx.get(f"{fake_publication_url}/api/v1/archive").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "title": "Post"}])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_archive(client)
    assert route.called
    assert result == [{"id": 1, "title": "Post"}]


@respx.mock
def test_get_archive_sort_top_changes_ordering_param(fake_cookies, fake_publication_url):
    route = respx.get(f"{fake_publication_url}/api/v1/archive").mock(
        return_value=httpx.Response(200, json=[])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    get_archive(client, sort="top")
    assert "sort" in route.calls[0].request.url.params
    assert route.calls[0].request.url.params["sort"] == "top"


@respx.mock
def test_get_archive_offset_limit_passed_through(fake_cookies, fake_publication_url):
    route = respx.get(f"{fake_publication_url}/api/v1/archive").mock(
        return_value=httpx.Response(200, json=[])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    get_archive(client, offset=10, limit=5)
    params = route.calls[0].request.url.params
    assert params["offset"] == "10"
    assert params["limit"] == "5"


@respx.mock
def test_get_archive_search_param_passed_through(fake_cookies, fake_publication_url):
    route = respx.get(f"{fake_publication_url}/api/v1/archive").mock(
        return_value=httpx.Response(200, json=[])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    get_archive(client, search="AI")
    params = route.calls[0].request.url.params
    assert params["search"] == "AI"


@respx.mock
def test_get_archive_uses_extract_list_for_envelope_tolerance(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/api/v1/archive").mock(
        return_value=httpx.Response(200, json={"posts": [{"id": 1}], "hasMore": True})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_archive(client)
    assert result == [{"id": 1}]


@respx.mock
def test_list_categories_hits_host_a(fake_cookies, fake_publication_url):
    route = respx.get(f"{SUBSTACK_COM}/api/v1/categories").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "name": "Tech"}])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = list_categories(client)
    assert route.called
    assert result == [{"id": 1, "name": "Tech"}]


@respx.mock
def test_list_sections_hits_publication_host(fake_cookies, fake_publication_url):
    route = respx.get(f"{fake_publication_url}/api/v1/publication/sections").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "name": "Default"}])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = list_sections(client)
    assert route.called
    assert result == [{"id": 1, "name": "Default"}]


@respx.mock
def test_get_archive_omits_offset_when_zero(fake_cookies, fake_publication_url):
    # Substack (Nov 2026) returns 400 "Invalid value" for any offset param, even 0.
    route = respx.get(f"{fake_publication_url}/api/v1/archive").mock(
        return_value=httpx.Response(200, json={"posts": [], "nextCursor": None})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    get_archive(client)
    assert "offset" not in route.calls[0].request.url.params


@respx.mock
def test_get_archive_cursor_passed_through(fake_cookies, fake_publication_url):
    route = respx.get(f"{fake_publication_url}/api/v1/archive").mock(
        return_value=httpx.Response(200, json={"posts": [{"id": 2}], "nextCursor": "c2"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_archive(client, cursor="c1")
    assert route.calls[0].request.url.params["cursor"] == "c1"
    assert result == [{"id": 2}]
    assert route.call_count == 1  # without all_pages, one page only


@respx.mock
def test_get_archive_all_pages_follows_next_cursor(fake_cookies, fake_publication_url):
    route = respx.get(f"{fake_publication_url}/api/v1/archive").mock(
        side_effect=[
            httpx.Response(200, json={"posts": [{"id": 1}, {"id": 2}], "nextCursor": "c1"}),
            httpx.Response(200, json={"posts": [{"id": 3}], "nextCursor": "c2"}),
            httpx.Response(200, json={"posts": [], "nextCursor": None}),
        ]
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_archive(client, all_pages=True)
    assert [p["id"] for p in result] == [1, 2, 3]
    assert "cursor" not in route.calls[0].request.url.params
    assert route.calls[1].request.url.params["cursor"] == "c1"
    assert route.calls[2].request.url.params["cursor"] == "c2"


@respx.mock
def test_get_archive_all_pages_stops_on_repeated_cursor(fake_cookies, fake_publication_url):
    # A server that keeps returning the same cursor must not loop forever.
    route = respx.get(f"{fake_publication_url}/api/v1/archive").mock(
        return_value=httpx.Response(200, json={"posts": [{"id": 1}], "nextCursor": "same"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    get_archive(client, all_pages=True)
    assert route.call_count == 2
