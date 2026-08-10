"""Tests for substack_cli.chat — READ-ONLY Chat commands.

No test performs a real network call; every Substack API interaction is
mocked with respx. `chat replies`/`chat sub-replies`/`chat unread` use host
"A" (substack.com); `chat list` uses host "P" (the publication subdomain).
"""
import json

import httpx
import pytest
import respx

from substack_cli.client import SubstackApiError, SubstackClient, SUBSTACK_COM
from substack_cli.app import chat_app
from substack_cli import chat
from substack_cli.chat import (
    _clean_params,
    get_unread_count,
    list_replies,
    list_sub_replies,
    list_threads,
)


# ---------------------------------------------------------------------------
# _clean_params
# ---------------------------------------------------------------------------

def test_clean_params_drops_none_values():
    assert _clean_params(before_id=None, after_id="5", limit=None) == {"after_id": "5"}


def test_clean_params_all_none_yields_empty_dict():
    assert _clean_params(before_id=None, after_id=None, limit=None) == {}


def test_clean_params_keeps_falsy_non_none_values():
    # 0 and "" are legitimate values, not "unset" — only None means unset.
    assert _clean_params(limit=0, before_id="") == {"limit": 0, "before_id": ""}


# ---------------------------------------------------------------------------
# list_threads (`chat list`)
# ---------------------------------------------------------------------------

@respx.mock
def test_list_threads_hits_publication_posts_on_host_p(fake_cookies, fake_publication_url):
    route = respx.get(
        f"{fake_publication_url}/api/v1/community/publications/12345/posts"
    ).mock(return_value=httpx.Response(200, json={"posts": [{"id": 1, "body": "hi"}]}))
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = list_threads(client, "12345")
    assert route.called
    assert result["posts"][0]["id"] == 1


@respx.mock
def test_list_threads_404_error_path(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/api/v1/community/publications/999/posts").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError) as excinfo:
        list_threads(client, "999")
    assert excinfo.value.status_code == 404


def test_cli_list_requires_publication_id_option(
    isolated_config, authed_env, cli_runner
):
    """--publication-id is REQUIRED — no resolver exists for it."""
    result = cli_runner.invoke(chat_app, ["list"])
    assert result.exit_code != 0
    assert "publication-id" in result.output.lower()


@respx.mock
def test_cli_list_happy_path(isolated_config, authed_env, fake_publication_url, cli_runner):
    respx.get(
        f"{fake_publication_url}/api/v1/community/publications/777/posts"
    ).mock(return_value=httpx.Response(200, json={"posts": [{"id": 42}]}))
    result = cli_runner.invoke(chat_app, ["list", "--publication-id", "777"])
    assert result.exit_code == 0
    assert json.loads(result.stdout.strip()) == [{"id": 42}]


@respx.mock
def test_cli_list_404_reports_endpoint_drift_hint(
    isolated_config, authed_env, fake_publication_url, cli_runner
):
    respx.get(
        f"{fake_publication_url}/api/v1/community/publications/999/posts"
    ).mock(return_value=httpx.Response(404, json={"error": "nope"}))
    result = cli_runner.invoke(chat_app, ["list", "--publication-id", "999"])
    assert result.exit_code != 0
    combined = (result.stdout or "") + (result.output or "")
    assert "endpoint" in combined.lower() or "drift" in combined.lower()


# ---------------------------------------------------------------------------
# list_replies (`chat replies`)
# ---------------------------------------------------------------------------

@respx.mock
def test_list_replies_hits_posts_comments_on_host_a_with_fixed_params(
    fake_cookies, fake_publication_url
):
    route = respx.get(f"{SUBSTACK_COM}/api/v1/community/posts/555/comments").mock(
        return_value=httpx.Response(
            200, json={"comments": [{"id": 1}], "moreBefore": False, "moreAfter": True}
        )
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = list_replies(client, "555")
    assert route.called
    request_url = route.calls[0].request.url
    assert request_url.params["order"] == "asc"
    assert request_url.params["initial"] == "true"
    assert result["comments"][0]["id"] == 1
    # extract_pagination_meta must NOT be wired: moreBefore/moreAfter pass
    # through untouched on the raw dict (not extracted into a `meta` key,
    # not dropped, not renamed to hasMore/nextCursor).
    assert result["moreBefore"] is False
    assert result["moreAfter"] is True


@respx.mock
def test_list_replies_unset_cursors_are_absent_from_query_string(
    fake_cookies, fake_publication_url
):
    """Regression test for finding 2: client._request does `params=params or
    None`, which only strips a WHOLLY empty dict — a None cursor nested in
    an otherwise-populated params dict would otherwise reach httpx and
    render as an empty query value (e.g. `before_id=`). This asserts the
    chat module's local None-filtering keeps that from happening."""
    route = respx.get(f"{SUBSTACK_COM}/api/v1/community/posts/555/comments").mock(
        return_value=httpx.Response(200, json={"comments": []})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    list_replies(client, "555")  # before_id/after_id/limit all default to None
    request_url = route.calls[0].request.url
    query = str(request_url)
    assert "before_id" not in query
    assert "after_id" not in query
    assert "limit" not in query
    # Sanity: the fixed params ARE present, proving this isn't an empty-dict fluke.
    assert "order=asc" in query
    assert "initial=true" in query


@respx.mock
def test_list_replies_set_cursors_are_present_in_query_string(
    fake_cookies, fake_publication_url
):
    route = respx.get(f"{SUBSTACK_COM}/api/v1/community/posts/555/comments").mock(
        return_value=httpx.Response(200, json={"comments": []})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    list_replies(client, "555", before_id="10", after_id="2", limit=5)
    request_url = route.calls[0].request.url
    assert request_url.params["before_id"] == "10"
    assert request_url.params["after_id"] == "2"
    assert request_url.params["limit"] == "5"


@respx.mock
def test_list_replies_404_error_path(fake_cookies, fake_publication_url):
    respx.get(f"{SUBSTACK_COM}/api/v1/community/posts/404/comments").mock(
        return_value=httpx.Response(404, json={"error": "gone"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError) as excinfo:
        list_replies(client, "404")
    assert excinfo.value.status_code == 404


@respx.mock
def test_cli_replies_happy_path(isolated_config, authed_env, cli_runner):
    respx.get(f"{SUBSTACK_COM}/api/v1/community/posts/555/comments").mock(
        return_value=httpx.Response(200, json={"comments": [{"id": 9}]})
    )
    result = cli_runner.invoke(chat_app, ["replies", "555"])
    assert result.exit_code == 0
    assert json.loads(result.stdout.strip()) == [{"id": 9}]


@respx.mock
def test_cli_replies_404_reports_endpoint_drift_hint(isolated_config, authed_env, cli_runner):
    respx.get(f"{SUBSTACK_COM}/api/v1/community/posts/999/comments").mock(
        return_value=httpx.Response(404, json={"error": "nope"})
    )
    result = cli_runner.invoke(chat_app, ["replies", "999"])
    assert result.exit_code != 0
    combined = (result.stdout or "") + (result.output or "")
    assert "endpoint" in combined.lower() or "drift" in combined.lower()


# ---------------------------------------------------------------------------
# list_sub_replies (`chat sub-replies`)
# ---------------------------------------------------------------------------

@respx.mock
def test_list_sub_replies_hits_comments_comments_on_host_a(fake_cookies, fake_publication_url):
    route = respx.get(f"{SUBSTACK_COM}/api/v1/community/comments/321/comments").mock(
        return_value=httpx.Response(200, json={"comments": [{"id": 2}]})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = list_sub_replies(client, "321")
    assert route.called
    request_url = route.calls[0].request.url
    assert request_url.params["order"] == "asc"
    assert request_url.params["initial"] == "true"
    assert result["comments"][0]["id"] == 2


@respx.mock
def test_list_sub_replies_unset_cursors_absent_from_query_string(
    fake_cookies, fake_publication_url
):
    """Same None-filtering regression guard as list_replies, for the
    sibling sub-replies endpoint."""
    route = respx.get(f"{SUBSTACK_COM}/api/v1/community/comments/321/comments").mock(
        return_value=httpx.Response(200, json={"comments": []})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    list_sub_replies(client, "321")
    query = str(route.calls[0].request.url)
    assert "before_id" not in query
    assert "after_id" not in query
    assert "limit" not in query


@respx.mock
def test_list_sub_replies_404_error_path(fake_cookies, fake_publication_url):
    respx.get(f"{SUBSTACK_COM}/api/v1/community/comments/404/comments").mock(
        return_value=httpx.Response(404, json={"error": "gone"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError) as excinfo:
        list_sub_replies(client, "404")
    assert excinfo.value.status_code == 404


@respx.mock
def test_cli_sub_replies_happy_path(isolated_config, authed_env, cli_runner):
    respx.get(f"{SUBSTACK_COM}/api/v1/community/comments/321/comments").mock(
        return_value=httpx.Response(200, json={"comments": [{"id": 7}]})
    )
    result = cli_runner.invoke(chat_app, ["sub-replies", "321", "--limit", "10"])
    assert result.exit_code == 0
    assert json.loads(result.stdout.strip()) == [{"id": 7}]


# ---------------------------------------------------------------------------
# get_unread_count (`chat unread`)
# ---------------------------------------------------------------------------

@respx.mock
def test_get_unread_count_hits_messages_unread_count_on_host_a(
    fake_cookies, fake_publication_url
):
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
    with pytest.raises(SubstackApiError) as excinfo:
        get_unread_count(client)
    assert excinfo.value.status_code == 401


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
    """Decided-for-you behavior: --pretty prints a bespoke highlighted line
    for pubChatUnreadCount without modifying the shared output() helper."""
    respx.get(f"{SUBSTACK_COM}/api/v1/messages/unread-count").mock(
        return_value=httpx.Response(200, json={"pubChatUnreadCount": 7, "unreadCount": 9})
    )
    result = cli_runner.invoke(chat_app, ["unread", "--pretty"])
    assert result.exit_code == 0
    assert "7" in result.output
    assert "Chat unread" in result.output or "chat unread" in result.output.lower()


@respx.mock
def test_cli_unread_404_reports_endpoint_drift_hint(isolated_config, authed_env, cli_runner):
    respx.get(f"{SUBSTACK_COM}/api/v1/messages/unread-count").mock(
        return_value=httpx.Response(404, json={"error": "nope"})
    )
    result = cli_runner.invoke(chat_app, ["unread"])
    assert result.exit_code != 0
    combined = (result.stdout or "") + (result.output or "")
    assert "endpoint" in combined.lower() or "drift" in combined.lower()


# ---------------------------------------------------------------------------
# Auth gating — chat commands require cookies like every other authed command
# ---------------------------------------------------------------------------

def test_cli_unread_requires_auth(isolated_config, cli_runner):
    result = cli_runner.invoke(chat_app, ["unread"])
    assert result.exit_code != 0
    combined = (result.stdout or "") + (result.output or "")
    assert "SUBSTACK_COOKIES_STRING" in combined or "substack config" in combined


# ---------------------------------------------------------------------------
# Scope guard — this module must stay read-only (no write verbs anywhere)
# ---------------------------------------------------------------------------

def test_chat_module_defines_no_write_commands():
    """Structural guard for the read-only scope contract: chat_app must
    register exactly the four GET-backed commands, nothing else (no send/
    reply/settings/delete/etc.)."""
    registered = {c.name for c in chat_app.registered_commands}
    assert registered == {"list", "replies", "sub-replies", "unread"}


def test_chat_module_never_calls_client_post_put_or_delete():
    """Static guard: substack_cli.chat must not reference the client's
    mutating verbs at all — this is a read-only subapp by design."""
    import inspect

    source = inspect.getsource(chat)
    assert "client.post(" not in source
    assert "client.put(" not in source
    assert "client.delete(" not in source
