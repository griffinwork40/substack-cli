"""Tests for substack_cli.notes — Substack Notes CRUD.

No test performs a real network call; every Substack API interaction is
mocked with respx. Notes endpoints all use host "A" (substack.com).
"""
import json

import httpx
import pytest
import respx

from substack_cli.client import SubstackApiError, SubstackClient, SUBSTACK_COM
from substack_cli.app import notes_app
from substack_cli.notes import (
    _load_body_json,
    _make_client,
    _normalize_comment_id,
    _text_to_note_doc,
    create_note,
    delete_note,
    get_note,
    list_notes,
)


# ---------------------------------------------------------------------------
# _text_to_note_doc — body builder
# ---------------------------------------------------------------------------

def test_text_to_note_doc_single_paragraph_shape():
    doc = _text_to_note_doc("Hello world.")
    assert doc["type"] == "doc"
    assert doc["attrs"]["schemaVersion"] == "v1"
    assert len(doc["content"]) == 1
    para = doc["content"][0]
    assert para["type"] == "paragraph"
    assert para["content"][0] == {"type": "text", "text": "Hello world."}


def test_text_to_note_doc_blank_lines_split_paragraphs():
    doc = _text_to_note_doc("First para.\n\nSecond para.")
    assert len(doc["content"]) == 2
    assert doc["content"][0]["content"][0]["text"] == "First para."
    assert doc["content"][1]["content"][0]["text"] == "Second para."


def test_text_to_note_doc_parses_inline_marks():
    doc = _text_to_note_doc("**bold** and [link](https://x.com)")
    nodes = doc["content"][0]["content"]
    bold = next(n for n in nodes if n.get("marks") and n["marks"][0]["type"] == "strong")
    assert bold["text"] == "bold"
    link = next(n for n in nodes if n.get("marks") and n["marks"][0]["type"] == "link")
    assert link["marks"][0]["attrs"]["href"] == "https://x.com"


def test_text_to_note_doc_empty_raises():
    with pytest.raises(ValueError):
        _text_to_note_doc("   \n\n  ")


# ---------------------------------------------------------------------------
# _load_body_json
# ---------------------------------------------------------------------------

def test_load_body_json_raw_doc_string():
    raw = '{"type": "doc", "content": []}'
    assert _load_body_json(raw) == {"type": "doc", "content": []}


def test_load_body_json_unwraps_bodyjson_key():
    raw = '{"bodyJson": {"type": "doc", "content": []}, "extra": 1}'
    assert _load_body_json(raw) == {"type": "doc", "content": []}


def test_load_body_json_from_file(tmp_path):
    p = tmp_path / "doc.json"
    p.write_text('{"type": "doc", "content": []}')
    assert _load_body_json(str(p)) == {"type": "doc", "content": []}


def test_load_body_json_rejects_non_doc():
    with pytest.raises(ValueError):
        _load_body_json('{"type": "paragraph"}')


def test_load_body_json_rejects_garbage():
    with pytest.raises(ValueError):
        _load_body_json("not json at all")


# ---------------------------------------------------------------------------
# _normalize_comment_id
# ---------------------------------------------------------------------------

def test_normalize_comment_id_plain_number():
    assert _normalize_comment_id("12345") == 12345


def test_normalize_comment_id_strips_c_prefix():
    assert _normalize_comment_id("c-12345") == 12345
    assert _normalize_comment_id("C-12345") == 12345


def test_normalize_comment_id_rejects_garbage():
    with pytest.raises(ValueError):
        _normalize_comment_id("p-999")  # posts are not notes
    with pytest.raises(ValueError):
        _normalize_comment_id("abc")


# ---------------------------------------------------------------------------
# create_note
# ---------------------------------------------------------------------------

@respx.mock
def test_create_note_posts_to_comment_feed_on_host_a(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    route = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 555})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = create_note(client, text="Hello Notes")
    assert route.called
    assert result == {"id": 555}


@respx.mock
def test_create_note_sends_bodyjson_as_object_not_string(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    """Regression guard vs drafts: a Note's bodyJson is a nested OBJECT, not
    a stringified JSON document like draft_body."""
    route = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    create_note(client, text="Hello")
    sent = json.loads(route.calls[0].request.content)
    assert isinstance(sent["bodyJson"], dict)  # NOT a str
    assert sent["bodyJson"]["type"] == "doc"
    assert sent["replyMinimumRole"] == "everyone"


@respx.mock
def test_create_note_reply_min_role_override(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    route = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    create_note(client, text="Paid only", reply_minimum_role="paid_subscriber")
    sent = json.loads(route.calls[0].request.content)
    assert sent["replyMinimumRole"] == "paid_subscriber"


@respx.mock
def test_create_note_body_json_takes_precedence_over_text(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    route = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    raw = '{"type": "doc", "content": [{"type": "paragraph", "content": []}]}'
    create_note(client, text="ignored", body_json=raw)
    sent = json.loads(route.calls[0].request.content)
    assert sent["bodyJson"] == json.loads(raw)


def test_create_note_requires_write_gate(
    isolated_config, authed_env, fake_cookies, fake_publication_url
):
    """No respx route registered — the write gate must block before any HTTP."""
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises((SubstackApiError, ValueError)):
        create_note(client, text="Hello")


def test_create_note_no_content_raises(
    isolated_config, fake_cookies, fake_publication_url, write_enabled_env
):
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(ValueError):
        create_note(client)


# ---------------------------------------------------------------------------
# list_notes
# ---------------------------------------------------------------------------

@respx.mock
def test_list_notes_default_hits_reader_feed_host_a(fake_cookies, fake_publication_url):
    route = respx.get(f"{SUBSTACK_COM}/api/v1/reader/feed").mock(
        return_value=httpx.Response(200, json={"items": [{"entity_key": "c-1"}]})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = list_notes(client, limit=10)
    assert route.called
    assert result["items"][0]["entity_key"] == "c-1"


@respx.mock
def test_list_notes_with_user_id_hits_profile_feed(fake_cookies, fake_publication_url):
    route = respx.get(f"{SUBSTACK_COM}/api/v1/reader/feed/profile/42").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    list_notes(client, user_id=42)
    assert route.called


@respx.mock
def test_list_notes_mine_resolves_self_then_profile_feed(
    fake_cookies, fake_publication_url, no_sleep
):
    profile = respx.get(f"{SUBSTACK_COM}/api/v1/user/profile/self").mock(
        return_value=httpx.Response(200, json={"id": 777, "name": "Me"})
    )
    feed = respx.get(f"{SUBSTACK_COM}/api/v1/reader/feed/profile/777").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    list_notes(client, mine=True)
    assert profile.called
    assert feed.called


# ---------------------------------------------------------------------------
# get_note
# ---------------------------------------------------------------------------

@respx.mock
def test_get_note_hits_reader_feed_entity_key(fake_cookies, fake_publication_url):
    route = respx.get(f"{SUBSTACK_COM}/api/v1/reader/feed/c-12345").mock(
        return_value=httpx.Response(200, json={"item": {"id": 12345}})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_note(client, 12345)
    assert route.called
    assert result["item"]["id"] == 12345


# ---------------------------------------------------------------------------
# delete_note
# ---------------------------------------------------------------------------

@respx.mock
def test_delete_note_uses_delete_comment_on_host_a(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    route = respx.delete(f"{SUBSTACK_COM}/api/v1/comment/456").mock(
        return_value=httpx.Response(200, json={})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    delete_note(client, 456)
    assert route.called
    assert route.calls[0].request.method == "DELETE"


def test_delete_note_requires_write_gate(
    isolated_config, authed_env, fake_cookies, fake_publication_url
):
    """No respx route — the write gate must block before any HTTP call."""
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises((SubstackApiError, ValueError)):
        delete_note(client, 456)


# ---------------------------------------------------------------------------
# _make_client — Notes work without a publication URL (host A only)
# ---------------------------------------------------------------------------

def test_make_client_falls_back_to_substack_com_without_pub_url(
    isolated_config, monkeypatch, fake_cookies
):
    """Notes only use host A; a publication URL is not required. When none is
    configured, _make_client falls back to substack.com instead of raising."""
    monkeypatch.setenv("SUBSTACK_COOKIES_STRING", fake_cookies)
    # No SUBSTACK_PUBLICATION_URL set (isolated_config cleared it).
    client = _make_client()
    assert isinstance(client, SubstackClient)
    assert client._publication_url == SUBSTACK_COM


# ---------------------------------------------------------------------------
# CLI command gates (via CliRunner)
# ---------------------------------------------------------------------------

def test_cli_create_refuses_without_yes(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    """Even with the write gate enabled, `notes create` needs --yes."""
    result = cli_runner.invoke(notes_app, ["create", "Hello"])
    assert result.exit_code != 0
    assert "yes" in result.output.lower()


def test_cli_create_refuses_without_write_gate(isolated_config, authed_env, cli_runner):
    result = cli_runner.invoke(notes_app, ["create", "Hello", "--yes"])
    assert result.exit_code != 0
    assert "SUBSTACK_ENABLE_WRITE" in result.output


def test_cli_delete_refuses_without_yes(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    result = cli_runner.invoke(notes_app, ["delete", "456"])
    assert result.exit_code != 0


@respx.mock
def test_cli_create_happy_path(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 999})
    )
    result = cli_runner.invoke(notes_app, ["create", "Hello Notes", "--yes"])
    assert result.exit_code == 0
    assert json.loads(result.stdout.strip())["id"] == 999
