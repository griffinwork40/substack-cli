"""Tests for substack_cli.chat — READ-ONLY Chat commands. All mocked via respx."""
import json

import httpx
import pytest
import respx

from substack_cli.client import SubstackApiError, SubstackClient, SUBSTACK_COM
from substack_cli.app import chat_app
from substack_cli import chat
from substack_cli.chat import (
    _clean_params,
    _validate_id,
    get_unread_count,
    list_replies,
    list_sub_replies,
    list_threads,
)


def test_clean_params_drops_none_values():
    assert _clean_params(before_id=None, after_id="5", limit=None) == {"after_id": "5"}


def test_clean_params_all_none_yields_empty_dict():
    assert _clean_params(before_id=None, after_id=None, limit=None) == {}


def test_clean_params_keeps_falsy_non_none_values():
    # 0 and "" are legitimate values, not "unset" — only None means unset.
    assert _clean_params(limit=0, before_id="") == {"limit": 0, "before_id": ""}


@pytest.mark.parametrize("bad", ["123/../admin", "123/456", "123?x=1", "123#frag", "a\\b"])
def test_validate_id_raises_on_malicious_input(bad):
    with pytest.raises(ValueError):
        _validate_id(bad, "thread_id")


@pytest.mark.parametrize("good", ["12345", "999", "0", "42"])
def test_validate_id_passes_clean_numeric_strings(good):
    assert _validate_id(good, "thread_id") == good


@respx.mock
def test_list_threads_hits_publication_posts_on_host_p(fake_cookies, fake_publication_url):
    route = respx.get(f"{fake_publication_url}/api/v1/community/publications/12345/posts").mock(
        return_value=httpx.Response(200, json={"posts": [{"id": 1}]})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = list_threads(client, 12345)
    assert route.called and result["posts"][0]["id"] == 1


@respx.mock
def test_list_threads_404_error_path(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/api/v1/community/publications/999/posts").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError) as exc:
        list_threads(client, 999)
    assert exc.value.status_code == 404


def test_cli_list_requires_publication_id_option(isolated_config, authed_env, cli_runner):
    result = cli_runner.invoke(chat_app, ["list"])
    assert result.exit_code != 0
    assert "publication-id" in result.output.lower()


@respx.mock
def test_cli_list_happy_path(isolated_config, authed_env, fake_publication_url, cli_runner):
    """Live API returns threads in a 'threads' key — must be unwrapped correctly."""
    respx.get(f"{fake_publication_url}/api/v1/community/publications/777/posts").mock(
        return_value=httpx.Response(
            200,
            json={"threads": [{"id": 42}], "more": False, "moreAfter": None},
        )
    )
    result = cli_runner.invoke(chat_app, ["list", "--publication-id", "777"])
    assert result.exit_code == 0 and json.loads(result.stdout.strip()) == [{"id": 42}]


@respx.mock
def test_cli_list_threads_key_not_posts_key(
    isolated_config, authed_env, fake_publication_url, cli_runner
):
    """Regression: 'posts' key (generic) must NOT satisfy the threads extractor."""
    respx.get(f"{fake_publication_url}/api/v1/community/publications/777/posts").mock(
        return_value=httpx.Response(200, json={"posts": [{"id": 1}]})
    )
    result = cli_runner.invoke(chat_app, ["list", "--publication-id", "777"])
    # 'posts' key is not 'threads' — extractor raises ValueError → CLI error
    assert result.exit_code != 0


@respx.mock
def test_cli_list_404_reports_endpoint_drift_hint(
    isolated_config, authed_env, fake_publication_url, cli_runner
):
    respx.get(f"{fake_publication_url}/api/v1/community/publications/999/posts").mock(
        return_value=httpx.Response(404, json={"error": "nope"})
    )
    result = cli_runner.invoke(chat_app, ["list", "--publication-id", "999"])
    assert result.exit_code != 0
    assert "endpoint" in (result.stdout + result.output).lower()


@respx.mock
def test_list_replies_hits_posts_comments_on_host_a_with_fixed_params(
    fake_cookies, fake_publication_url
):
    route = respx.get(f"{SUBSTACK_COM}/api/v1/community/posts/555/comments").mock(
        return_value=httpx.Response(200, json={"comments": [{"id": 1}], "moreAfter": True})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = list_replies(client, "555")
    url = route.calls[0].request.url
    assert url.params["order"] == "asc" and url.params["initial"] == "true"
    assert result["comments"][0]["id"] == 1 and result["moreAfter"] is True


@respx.mock
def test_list_replies_unset_cursors_are_absent_from_query_string(
    fake_cookies, fake_publication_url
):
    """None cursor values must not appear as empty query params."""
    route = respx.get(f"{SUBSTACK_COM}/api/v1/community/posts/555/comments").mock(
        return_value=httpx.Response(200, json={"comments": []})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    list_replies(client, "555")
    query = str(route.calls[0].request.url)
    assert "before_id" not in query
    assert "after_id" not in query
    assert "limit" not in query
    assert "order=asc" in query
    assert "initial=true" in query


@respx.mock
def test_list_replies_set_cursors_are_present_in_query_string(fake_cookies, fake_publication_url):
    route = respx.get(f"{SUBSTACK_COM}/api/v1/community/posts/555/comments").mock(
        return_value=httpx.Response(200, json={"comments": []})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    list_replies(client, "555", before_id="10", after_id="2", limit=5)
    p = route.calls[0].request.url.params
    assert p["before_id"] == "10" and p["after_id"] == "2" and p["limit"] == "5"


@respx.mock
def test_list_replies_404_error_path(fake_cookies, fake_publication_url):
    respx.get(f"{SUBSTACK_COM}/api/v1/community/posts/404/comments").mock(
        return_value=httpx.Response(404, json={"error": "gone"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError) as exc:
        list_replies(client, "404")
    assert exc.value.status_code == 404


@respx.mock
def test_cli_replies_happy_path(isolated_config, authed_env, cli_runner):
    respx.get(f"{SUBSTACK_COM}/api/v1/community/posts/555/comments").mock(
        return_value=httpx.Response(200, json={"comments": [{"id": 9}]})
    )
    result = cli_runner.invoke(chat_app, ["replies", "555"])
    assert result.exit_code == 0 and json.loads(result.stdout.strip()) == [{"id": 9}]


@respx.mock
def test_cli_replies_404_reports_endpoint_drift_hint(isolated_config, authed_env, cli_runner):
    respx.get(f"{SUBSTACK_COM}/api/v1/community/posts/999/comments").mock(
        return_value=httpx.Response(404, json={"error": "nope"})
    )
    result = cli_runner.invoke(chat_app, ["replies", "999"])
    assert result.exit_code != 0
    assert "endpoint" in (result.stdout + result.output).lower()


@respx.mock
def test_list_sub_replies_hits_comments_comments_on_host_a(fake_cookies, fake_publication_url):
    route = respx.get(f"{SUBSTACK_COM}/api/v1/community/comments/321/comments").mock(
        return_value=httpx.Response(200, json={"comments": [{"id": 2}]})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = list_sub_replies(client, "321")
    url = route.calls[0].request.url
    assert url.params["order"] == "asc" and result["comments"][0]["id"] == 2


@respx.mock
def test_list_sub_replies_unset_cursors_absent_from_query_string(fake_cookies, fake_publication_url):
    route = respx.get(f"{SUBSTACK_COM}/api/v1/community/comments/321/comments").mock(
        return_value=httpx.Response(200, json={"comments": []})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    list_sub_replies(client, "321")
    query = str(route.calls[0].request.url)
    assert "before_id" not in query and "after_id" not in query and "limit" not in query


@respx.mock
def test_list_sub_replies_404_error_path(fake_cookies, fake_publication_url):
    respx.get(f"{SUBSTACK_COM}/api/v1/community/comments/404/comments").mock(
        return_value=httpx.Response(404, json={"error": "gone"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError) as exc:
        list_sub_replies(client, "404")
    assert exc.value.status_code == 404


@respx.mock
def test_cli_sub_replies_happy_path(isolated_config, authed_env, cli_runner):
    respx.get(f"{SUBSTACK_COM}/api/v1/community/comments/321/comments").mock(
        return_value=httpx.Response(200, json={"comments": [{"id": 7}]})
    )
    result = cli_runner.invoke(chat_app, ["sub-replies", "321", "--limit", "10"])
    assert result.exit_code == 0 and json.loads(result.stdout.strip()) == [{"id": 7}]


@respx.mock
def test_get_unread_count_hits_messages_unread_count_on_host_a(fake_cookies, fake_publication_url):
    route = respx.get(f"{SUBSTACK_COM}/api/v1/messages/unread-count").mock(
        return_value=httpx.Response(200, json={"pubChatUnreadCount": 3, "unreadCount": 5})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_unread_count(client)
    assert route.called
    assert result["pubChatUnreadCount"] == 3


@respx.mock
def test_get_unread_count_401_error_path(fake_cookies, fake_publication_url):
    respx.get(f"{SUBSTACK_COM}/api/v1/messages/unread-count").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError) as exc:
        get_unread_count(client)
    assert exc.value.status_code == 401


@respx.mock
def test_cli_unread_happy_path_plain(isolated_config, authed_env, cli_runner):
    respx.get(f"{SUBSTACK_COM}/api/v1/messages/unread-count").mock(
        return_value=httpx.Response(200, json={"pubChatUnreadCount": 3, "unreadCount": 5})
    )
    result = cli_runner.invoke(chat_app, ["unread"])
    assert result.exit_code == 0
    assert json.loads(result.stdout.strip()) == {"pubChatUnreadCount": 3, "unreadCount": 5}


@respx.mock
def test_cli_unread_pretty_surfaces_pub_chat_unread_count_bespoke_line(
    isolated_config, authed_env, cli_runner
):
    respx.get(f"{SUBSTACK_COM}/api/v1/messages/unread-count").mock(
        return_value=httpx.Response(200, json={"pubChatUnreadCount": 7, "unreadCount": 9})
    )
    result = cli_runner.invoke(chat_app, ["unread", "--pretty"])
    assert result.exit_code == 0 and "7" in result.output
    assert "Chat unread" in result.output or "chat unread" in result.output.lower()


@respx.mock
def test_cli_unread_404_reports_endpoint_drift_hint(isolated_config, authed_env, cli_runner):
    respx.get(f"{SUBSTACK_COM}/api/v1/messages/unread-count").mock(
        return_value=httpx.Response(404, json={"error": "nope"})
    )
    result = cli_runner.invoke(chat_app, ["unread"])
    assert result.exit_code != 0
    assert "endpoint" in (result.stdout + result.output).lower()


def test_cli_unread_requires_auth(isolated_config, cli_runner):
    result = cli_runner.invoke(chat_app, ["unread"])
    assert result.exit_code != 0
    combined = result.stdout + result.output
    assert "SUBSTACK_COOKIES_STRING" in combined or "substack config" in combined


@respx.mock
def test_cli_unread_pretty_no_pub_chat_count_field(isolated_config, authed_env, cli_runner):
    respx.get(f"{SUBSTACK_COM}/api/v1/messages/unread-count").mock(
        return_value=httpx.Response(200, json={"unreadCount": 5})
    )
    result = cli_runner.invoke(chat_app, ["unread", "--pretty"])
    assert result.exit_code == 0
    assert "Publication Chat unread" not in result.output


@respx.mock
def test_cli_list_401_no_drift_hint(isolated_config, authed_env, fake_publication_url, cli_runner):
    respx.get(f"{fake_publication_url}/api/v1/community/publications/1/posts").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    result = cli_runner.invoke(chat_app, ["list", "--publication-id", "1"])
    assert result.exit_code != 0
    assert "drift" not in (result.stdout + result.output).lower()


@respx.mock
def test_cli_replies_401_no_drift_hint(isolated_config, authed_env, cli_runner):
    respx.get(f"{SUBSTACK_COM}/api/v1/community/posts/1/comments").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    result = cli_runner.invoke(chat_app, ["replies", "1"])
    assert result.exit_code != 0 and "drift" not in (result.stdout + result.output).lower()


@respx.mock
def test_cli_sub_replies_401_no_drift_hint(isolated_config, authed_env, cli_runner):
    respx.get(f"{SUBSTACK_COM}/api/v1/community/comments/1/comments").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    result = cli_runner.invoke(chat_app, ["sub-replies", "1"])
    assert result.exit_code != 0 and "drift" not in (result.stdout + result.output).lower()


@respx.mock
def test_make_client_fallback_hits_substack_com_when_no_pub_url(
    isolated_config, monkeypatch, fake_cookies, cli_runner
):
    monkeypatch.setenv("SUBSTACK_COOKIES_STRING", fake_cookies)
    route = respx.get(f"{SUBSTACK_COM}/api/v1/messages/unread-count").mock(
        return_value=httpx.Response(200, json={"unreadCount": 2})
    )
    result = cli_runner.invoke(chat_app, ["unread"])
    assert result.exit_code == 0 and route.called


def test_chat_module_defines_no_write_commands():
    registered = {c.name for c in chat_app.registered_commands}
    assert registered == {"list", "replies", "sub-replies", "unread"}


def test_chat_module_never_calls_client_post_put_or_delete():
    import inspect
    source = inspect.getsource(chat)
    assert "client.post(" not in source
    assert "client.put(" not in source
    assert "client.delete(" not in source
