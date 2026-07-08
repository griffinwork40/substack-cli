"""Tests for substack_cli.read — comments listing."""
import httpx
import respx

from substack_cli.client import SubstackClient
from substack_cli.read import list_post_comments


@respx.mock
def test_list_post_comments_returns_comment_list(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/api/v1/post/123/comments").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "body": "Great post"}])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = list_post_comments(client, 123)
    assert result == [{"id": 1, "body": "Great post"}]


@respx.mock
def test_list_post_comments_uses_extract_list_for_comments_key(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/api/v1/post/123/comments").mock(
        return_value=httpx.Response(200, json={"comments": [{"id": 1, "body": "Nice"}]})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = list_post_comments(client, 123)
    assert result == [{"id": 1, "body": "Nice"}]


@respx.mock
def test_list_post_comments_handles_nested_children_field_without_crashing(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/api/v1/post/123/comments").mock(
        return_value=httpx.Response(200, json=[
            {"id": 1, "body": "Parent", "children": [{"id": 2, "body": "Reply"}]}
        ])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = list_post_comments(client, 123)
    assert len(result) == 1
    assert "children" in result[0]