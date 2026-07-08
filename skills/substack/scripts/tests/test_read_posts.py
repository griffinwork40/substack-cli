"""Tests for substack_cli.read — post details and self profile."""
import httpx
import pytest
import respx

from substack_cli.client import SubstackApiError, SubstackClient, SUBSTACK_COM
from substack_cli.read import get_post, get_self_profile


@respx.mock
def test_get_post_by_slug_returns_full_object(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/api/v1/posts/my-slug").mock(
        return_value=httpx.Response(200, json={"id": 1, "title": "My Post", "slug": "my-slug"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_post(client, "my-slug")
    assert result == {"id": 1, "title": "My Post", "slug": "my-slug"}


@respx.mock
def test_get_post_uses_publication_host(fake_cookies, fake_publication_url):
    route = respx.get(f"{fake_publication_url}/api/v1/posts/my-slug").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    get_post(client, "my-slug")
    assert route.calls[0].request.url.host == "charliepgarcia.substack.com"


@respx.mock
def test_get_self_profile_hits_host_a(fake_cookies, fake_publication_url):
    route = respx.get(f"{SUBSTACK_COM}/api/v1/user/profile/self").mock(
        return_value=httpx.Response(200, json={"id": 999, "name": "Charlie"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_self_profile(client)
    assert route.called
    assert route.calls[0].request.url.host == "substack.com"
    assert result == {"id": 999, "name": "Charlie"}


@respx.mock
def test_get_self_profile_401_surfaces_auth_remediation(fake_cookies, fake_publication_url):
    from substack_cli.auth import cookie_expiry_hint

    respx.get(f"{SUBSTACK_COM}/api/v1/user/profile/self").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError) as exc_info:
        get_self_profile(client)
    hint = cookie_expiry_hint(401)
    assert hint is not None
    assert hint in str(exc_info.value)