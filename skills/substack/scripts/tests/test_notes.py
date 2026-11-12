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
    _parse_schedule,
    _strip_unsupported_marks_from_doc,
    _strip_unsupported_note_marks,
    _text_to_note_doc,
    _validate_reply_minimum_role,
    _validate_url,
    create_note,
    create_note_image_attachment,
    create_note_link_attachment,
    delete_note,
    get_note,
    list_draft_notes,
    list_notes,
    reply_to_note,
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


def test_text_to_note_doc_strips_bold_and_italic_keeps_link(capsys):
    """bold/italic marks are stripped (Notes API 500s on them); link marks survive."""
    doc = _text_to_note_doc("**bold** and [link](https://x.com)")
    nodes = doc["content"][0]["content"]
    # bold text is kept as plain text — no strong mark
    bold_node = next(n for n in nodes if n.get("text") == "bold")
    assert "marks" not in bold_node or not any(
        m["type"] == "strong" for m in bold_node.get("marks", [])
    )
    # link mark is preserved
    link = next(n for n in nodes if n.get("marks") and n["marks"][0]["type"] == "link")
    assert link["marks"][0]["attrs"]["href"] == "https://x.com"
    # warning emitted to stderr
    captured = capsys.readouterr()
    assert "strong" in captured.err
    assert "not supported" in captured.err


def test_text_to_note_doc_strips_italic(capsys):
    """*italic* marks are stripped; text is kept."""
    doc = _text_to_note_doc("*hello*")
    nodes = doc["content"][0]["content"]
    assert nodes[0]["text"] == "hello"
    assert "marks" not in nodes[0] or not any(
        m["type"] == "em" for m in nodes[0].get("marks", [])
    )
    captured = capsys.readouterr()
    assert "em" in captured.err


def test_text_to_note_doc_empty_raises():
    with pytest.raises(ValueError):
        _text_to_note_doc("   \n\n  ")


# ---------------------------------------------------------------------------
# _strip_unsupported_note_marks
# ---------------------------------------------------------------------------

def test_strip_unsupported_note_marks_removes_strong(capsys):
    nodes = [{"type": "text", "text": "bold", "marks": [{"type": "strong"}]}]
    result = _strip_unsupported_note_marks(nodes)
    assert result == [{"type": "text", "text": "bold"}]
    assert "strong" in capsys.readouterr().err


def test_strip_unsupported_note_marks_removes_em(capsys):
    nodes = [{"type": "text", "text": "italic", "marks": [{"type": "em"}]}]
    result = _strip_unsupported_note_marks(nodes)
    assert result == [{"type": "text", "text": "italic"}]
    assert "em" in capsys.readouterr().err


def test_strip_unsupported_note_marks_preserves_link():
    mark = {"type": "link", "attrs": {"href": "https://example.com"}}
    nodes = [{"type": "text", "text": "click", "marks": [mark]}]
    result = _strip_unsupported_note_marks(nodes)
    assert result[0]["marks"] == [mark]


def test_strip_unsupported_note_marks_plain_text_unchanged():
    nodes = [{"type": "text", "text": "plain"}]
    result = _strip_unsupported_note_marks(nodes)
    assert result == [{"type": "text", "text": "plain"}]


def test_strip_unsupported_note_marks_warns_once_per_type(capsys):
    """Each mark type should only produce one warning even if it appears many times."""
    nodes = [
        {"type": "text", "text": "a", "marks": [{"type": "strong"}]},
        {"type": "text", "text": "b", "marks": [{"type": "strong"}]},
    ]
    _strip_unsupported_note_marks(nodes)
    err = capsys.readouterr().err
    assert err.count("'strong'") == 1


# ---------------------------------------------------------------------------
# _validate_reply_minimum_role
# ---------------------------------------------------------------------------

def test_validate_reply_minimum_role_accepts_everyone():
    assert _validate_reply_minimum_role("everyone") == "everyone"


def test_validate_reply_minimum_role_accepts_paid_subscriber():
    assert _validate_reply_minimum_role("paid_subscriber") == "paid_subscriber"


def test_validate_reply_minimum_role_rejects_subscriber():
    with pytest.raises(ValueError, match="subscriber"):
        _validate_reply_minimum_role("subscriber")


def test_validate_reply_minimum_role_rejects_founding():
    with pytest.raises(ValueError, match="founding"):
        _validate_reply_minimum_role("founding")


def test_validate_reply_minimum_role_rejects_unknown():
    with pytest.raises(ValueError, match="not a verified"):
        _validate_reply_minimum_role("admin")


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
def test_create_note_rejects_subscriber_role(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    """'subscriber' must be rejected client-side before any HTTP call."""
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(ValueError, match="subscriber"):
        create_note(client, text="Hello", reply_minimum_role="subscriber")


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
# reply_to_note
# ---------------------------------------------------------------------------

@respx.mock
def test_reply_to_note_posts_to_comment_feed_with_parent_id(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    route = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 555})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = reply_to_note(client, 100, text="hi")
    assert route.called
    assert result == {"id": 555}
    sent = json.loads(route.calls[0].request.content)
    assert sent["parent_id"] == 100
    assert isinstance(sent["bodyJson"], dict)  # NOT a str
    assert sent["bodyJson"]["type"] == "doc"
    assert sent["replyMinimumRole"] == "everyone"
    assert sent["tabId"] == "for-you"
    assert sent["surface"] == "feed"


@respx.mock
def test_reply_to_note_body_json_takes_precedence_over_text(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    route = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    raw = '{"type": "doc", "content": [{"type": "paragraph", "content": []}]}'
    reply_to_note(client, 100, text="ignored", body_json=raw)
    sent = json.loads(route.calls[0].request.content)
    assert sent["bodyJson"] == json.loads(raw)


@respx.mock
def test_reply_to_note_reply_min_role_override(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    route = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    reply_to_note(client, 100, text="Paid only", reply_minimum_role="paid_subscriber")
    sent = json.loads(route.calls[0].request.content)
    assert sent["replyMinimumRole"] == "paid_subscriber"


def test_reply_to_note_requires_write_gate(
    isolated_config, authed_env, fake_cookies, fake_publication_url
):
    """No respx route registered — the write gate must block before any HTTP."""
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises((SubstackApiError, ValueError)):
        reply_to_note(client, 100, text="hi")


def test_reply_to_note_no_content_raises(
    isolated_config, fake_cookies, fake_publication_url, write_enabled_env
):
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(ValueError):
        reply_to_note(client, 100)


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
def test_get_note_hits_reader_comment_endpoint(fake_cookies, fake_publication_url):
    route = respx.get(f"{SUBSTACK_COM}/api/v1/reader/comment/12345").mock(
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
# Image attachments (--image)
# ---------------------------------------------------------------------------

CDN_URL = "https://substack-post-media.s3.amazonaws.com/public/images/fake.png"


@pytest.fixture
def note_image(tmp_path):
    path = tmp_path / "pic.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return str(path)


def _mock_image_routes(attachment_ids=("att-1",)):
    """Mock upload + attachment on host A; returns (upload, attach) routes."""
    upload = respx.post(f"{SUBSTACK_COM}/api/v1/image").mock(
        return_value=httpx.Response(200, json={"url": CDN_URL, "imageWidth": 10})
    )
    attach = respx.post(f"{SUBSTACK_COM}/api/v1/comment/attachment").mock(
        side_effect=[httpx.Response(200, json={"id": i}) for i in attachment_ids]
    )
    return upload, attach


@respx.mock
def test_create_note_image_attachment_uploads_then_registers_on_host_a(
    fake_cookies, fake_publication_url, note_image
):
    upload, attach = _mock_image_routes()
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    assert create_note_image_attachment(client, note_image) == "att-1"
    assert "data:image/png;base64," in json.loads(upload.calls[0].request.content)["image"]
    assert json.loads(attach.calls[0].request.content) == {"url": CDN_URL, "type": "image"}


@respx.mock
def test_create_note_with_images_sends_attachment_ids_in_order(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env, note_image
):
    upload, attach = _mock_image_routes(("att-1", "att-2"))
    feed = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 77})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = create_note(client, text="With pics", image_paths=[note_image, note_image])
    assert result == {"id": 77}
    assert upload.call_count == 2 and attach.call_count == 2
    sent = json.loads(feed.calls[0].request.content)
    assert sent["attachmentIds"] == ["att-1", "att-2"]
    assert sent["bodyJson"]["type"] == "doc"


@respx.mock
def test_create_note_without_images_omits_attachment_ids(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    feed = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    create_note(client, text="Plain")
    assert "attachmentIds" not in json.loads(feed.calls[0].request.content)


@respx.mock
def test_create_note_missing_image_fails_before_any_http(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env, tmp_path
):
    feed = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(ValueError, match="Image file not found"):
        create_note(client, text="x", image_paths=[str(tmp_path / "nope.png")])
    assert not feed.called


@respx.mock
def test_create_note_failed_upload_never_publishes(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env, note_image
):
    respx.post(f"{SUBSTACK_COM}/api/v1/image").mock(
        return_value=httpx.Response(400, json={"error": "bad image"})
    )
    feed = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError):
        create_note(client, text="x", image_paths=[note_image])
    assert not feed.called


@respx.mock
def test_create_note_attachment_without_id_raises_and_never_publishes(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env, note_image
):
    respx.post(f"{SUBSTACK_COM}/api/v1/image").mock(
        return_value=httpx.Response(200, json={"url": CDN_URL})
    )
    respx.post(f"{SUBSTACK_COM}/api/v1/comment/attachment").mock(
        return_value=httpx.Response(200, json={})
    )
    feed = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError):
        create_note(client, text="x", image_paths=[note_image])
    assert not feed.called


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


def test_cli_create_rejects_invalid_reply_min_role(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    """'subscriber' was rejected by the live API (issue #31); CLI must catch it first."""
    result = cli_runner.invoke(
        notes_app, ["create", "Hello", "--yes", "--reply-min-role", "subscriber"]
    )
    assert result.exit_code != 0
    assert "subscriber" in result.output.lower()


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


def test_cli_reply_refuses_without_yes(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    """Even with the write gate enabled, `notes reply` needs --yes."""
    result = cli_runner.invoke(notes_app, ["reply", "100", "Hello"])
    assert result.exit_code != 0
    assert "yes" in result.output.lower()


def test_cli_reply_refuses_without_write_gate(isolated_config, authed_env, cli_runner):
    result = cli_runner.invoke(notes_app, ["reply", "100", "Hello", "--yes"])
    assert result.exit_code != 0
    assert "SUBSTACK_ENABLE_WRITE" in result.output


@respx.mock
def test_cli_reply_happy_path(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    route = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 999})
    )
    result = cli_runner.invoke(notes_app, ["reply", "100", "Hi there", "--yes"])
    assert result.exit_code == 0
    assert json.loads(result.stdout.strip())["id"] == 999
    sent = json.loads(route.calls[0].request.content)
    assert sent["parent_id"] == 100


@respx.mock
def test_cli_reply_accepts_c_prefixed_parent_id(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    route = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 999})
    )
    result = cli_runner.invoke(notes_app, ["reply", "c-100", "Hi", "--yes"])
    assert result.exit_code == 0
    sent = json.loads(route.calls[0].request.content)
    assert sent["parent_id"] == 100  # the `c-` prefix is stripped


@respx.mock
def test_cli_create_with_image_flag(
    isolated_config, authed_env, write_enabled_env, cli_runner, note_image
):
    _mock_image_routes(("att-9",))
    feed = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 999})
    )
    result = cli_runner.invoke(
        notes_app, ["create", "Look at this", "--image", note_image, "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(feed.calls[0].request.content)["attachmentIds"] == ["att-9"]


def test_cli_create_missing_image_errors_without_publishing(
    isolated_config, authed_env, write_enabled_env, cli_runner, tmp_path
):
    """No respx routes: a missing --image must fail before any HTTP call."""
    result = cli_runner.invoke(
        notes_app, ["create", "x", "--image", str(tmp_path / "nope.png"), "--yes"]
    )
    assert result.exit_code != 0
    assert "Image file not found" in result.output


# ---------------------------------------------------------------------------
# Link attachments (--link)
# ---------------------------------------------------------------------------

LINK_URL = "https://charliepgarcia.substack.com/p/some-post"
LINK_UUID = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"


def test_validate_url_accepts_http_and_https():
    _validate_url("http://example.com")
    _validate_url("https://example.com/path?q=1")


def test_validate_url_rejects_non_http():
    with pytest.raises(ValueError, match="http"):
        _validate_url("ftp://example.com")
    with pytest.raises(ValueError, match="http"):
        _validate_url("not-a-url")


@respx.mock
def test_create_note_link_attachment_posts_correct_body(
    fake_cookies, fake_publication_url
):
    """create_note_link_attachment sends {url, type:'link'} to /api/v1/comment/attachment."""
    route = respx.post(f"{SUBSTACK_COM}/api/v1/comment/attachment").mock(
        return_value=httpx.Response(200, json={"id": LINK_UUID, "type": "post", "post": {}})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = create_note_link_attachment(client, LINK_URL)
    assert result == LINK_UUID
    sent = json.loads(route.calls[0].request.content)
    assert sent == {"url": LINK_URL, "type": "link"}


@respx.mock
def test_create_note_link_attachment_raises_on_missing_id(
    fake_cookies, fake_publication_url
):
    respx.post(f"{SUBSTACK_COM}/api/v1/comment/attachment").mock(
        return_value=httpx.Response(200, json={"type": "link"})  # no id
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError, match="no 'id' field"):
        create_note_link_attachment(client, LINK_URL)


def test_create_note_link_attachment_rejects_invalid_url(
    fake_cookies, fake_publication_url
):
    """No HTTP routes needed: URL validation fires before any network call."""
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(ValueError, match="http"):
        create_note_link_attachment(client, "not-a-url")


@respx.mock
def test_create_note_with_link_sends_attachment_id(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    attach = respx.post(f"{SUBSTACK_COM}/api/v1/comment/attachment").mock(
        return_value=httpx.Response(200, json={"id": LINK_UUID, "type": "post"})
    )
    feed = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 88})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = create_note(client, text="Check this out", link_urls=[LINK_URL])
    assert result == {"id": 88}
    assert attach.call_count == 1
    sent = json.loads(feed.calls[0].request.content)
    assert sent["attachmentIds"] == [LINK_UUID]


@respx.mock
def test_create_note_link_and_image_ordering(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env, note_image
):
    """Image attachment ids come before link attachment ids in attachmentIds."""
    # upload + image-attachment route
    respx.post(f"{SUBSTACK_COM}/api/v1/image").mock(
        return_value=httpx.Response(200, json={"url": CDN_URL, "imageWidth": 10})
    )
    attach = respx.post(f"{SUBSTACK_COM}/api/v1/comment/attachment").mock(
        side_effect=[
            httpx.Response(200, json={"id": "img-id"}),   # image attachment
            httpx.Response(200, json={"id": "lnk-id"}),   # link attachment
        ]
    )
    feed = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 99})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    create_note(client, text="Both", image_paths=[note_image], link_urls=[LINK_URL])
    sent = json.loads(feed.calls[0].request.content)
    assert sent["attachmentIds"] == ["img-id", "lnk-id"]


@respx.mock
def test_create_note_with_link_as_draft_sends_to_comment_draft(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    respx.post(f"{SUBSTACK_COM}/api/v1/comment/attachment").mock(
        return_value=httpx.Response(200, json={"id": LINK_UUID})
    )
    draft = respx.post(f"{SUBSTACK_COM}/api/v1/comment/draft").mock(
        return_value=httpx.Response(200, json={"id": 10, "status": "draft"})
    )
    feed = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = create_note(client, text="Draft with link", link_urls=[LINK_URL], draft=True)
    assert result == {"id": 10, "status": "draft"}
    assert not feed.called
    sent = json.loads(draft.calls[0].request.content)
    assert sent["attachmentIds"] == [LINK_UUID]


def test_create_note_invalid_link_fails_before_any_http(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    """No respx routes: an invalid --link URL must raise before any network call."""
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(ValueError, match="http"):
        create_note(client, text="x", link_urls=["not-a-url"])


def test_create_note_no_link_omits_attachment_ids_when_no_images(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    """Regression: no link_urls and no image_paths -> no attachmentIds key.
    (Checked without HTTP mocking; we hit the write gate before network.)"""
    # Already covered by test_create_note_without_images_omits_attachment_ids,
    # but confirm link_urls=[] is equivalent.
    import respx as _respx
    with _respx.mock:
        feed = _respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
            return_value=httpx.Response(200, json={"id": 1})
        )
        client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
        create_note(client, text="Plain", link_urls=[])
        assert "attachmentIds" not in json.loads(feed.calls[0].request.content)


# ---------------------------------------------------------------------------
# CLI --link flag tests
# ---------------------------------------------------------------------------

@respx.mock
def test_cli_create_with_link_flag(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    respx.post(f"{SUBSTACK_COM}/api/v1/comment/attachment").mock(
        return_value=httpx.Response(200, json={"id": LINK_UUID, "type": "post"})
    )
    feed = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 101})
    )
    result = cli_runner.invoke(
        notes_app, ["create", "Read this", "--link", LINK_URL, "--yes"]
    )
    assert result.exit_code == 0, result.output
    sent = json.loads(feed.calls[0].request.content)
    assert sent["attachmentIds"] == [LINK_UUID]


@respx.mock
def test_cli_create_with_link_draft(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    respx.post(f"{SUBSTACK_COM}/api/v1/comment/attachment").mock(
        return_value=httpx.Response(200, json={"id": LINK_UUID})
    )
    draft = respx.post(f"{SUBSTACK_COM}/api/v1/comment/draft").mock(
        return_value=httpx.Response(200, json={"id": 20, "status": "draft"})
    )
    result = cli_runner.invoke(
        notes_app, ["create", "Draft link", "--link", LINK_URL, "--draft"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout.strip())["id"] == 20
    sent = json.loads(draft.calls[0].request.content)
    assert sent["attachmentIds"] == [LINK_UUID]


def test_cli_create_invalid_link_errors_without_publishing(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    """No respx routes: an invalid --link URL must fail before any HTTP call."""
    result = cli_runner.invoke(
        notes_app, ["create", "x", "--link", "ftp://bad", "--yes"]
    )
    assert result.exit_code != 0
    assert "http" in result.output.lower()



# ---------------------------------------------------------------------------
# Drafts + scheduling (--draft / --schedule / notes drafts)
# ---------------------------------------------------------------------------

from datetime import datetime, timezone  # noqa: E402

FIXED_NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def test_parse_schedule_utc_z_suffix():
    assert _parse_schedule("2026-10-12T13:00Z", now=FIXED_NOW) == "2026-10-12T13:00:00.000Z"


def test_parse_schedule_explicit_offset_converts_to_utc():
    assert (
        _parse_schedule("2026-10-12T09:00-04:00", now=FIXED_NOW)
        == "2026-10-12T13:00:00.000Z"
    )


def test_parse_schedule_naive_uses_local_timezone():
    naive = datetime(2026, 10, 12, 9, 0)
    expected = naive.astimezone().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    assert _parse_schedule("2026-10-12 09:00", now=FIXED_NOW) == expected


def test_parse_schedule_rejects_past_and_garbage():
    with pytest.raises(ValueError, match="not in the future"):
        _parse_schedule("2026-10-09T09:00Z", now=FIXED_NOW)
    with pytest.raises(ValueError, match="Invalid --schedule"):
        _parse_schedule("next tuesday", now=FIXED_NOW)


@respx.mock
def test_create_note_draft_posts_to_comment_draft_not_feed(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env
):
    draft = respx.post(f"{SUBSTACK_COM}/api/v1/comment/draft").mock(
        return_value=httpx.Response(200, json={"id": 5, "status": "draft"})
    )
    feed = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    assert create_note(client, text="Later", draft=True) == {"id": 5, "status": "draft"}
    assert not feed.called
    sent = json.loads(draft.calls[0].request.content)
    assert sent["bodyJson"]["type"] == "doc"
    assert sent["tabId"] == "for-you" and sent["surface"] == "feed"
    assert "trigger_at" not in sent


@respx.mock
def test_create_note_scheduled_sends_trigger_at_and_images(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env, note_image
):
    _mock_image_routes(("att-1",))
    draft = respx.post(f"{SUBSTACK_COM}/api/v1/comment/draft").mock(
        return_value=httpx.Response(200, json={"id": 6})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    create_note(
        client, text="Tomorrow", image_paths=[note_image],
        trigger_at="2026-10-12T13:00:00.000Z",
    )
    sent = json.loads(draft.calls[0].request.content)
    assert sent["trigger_at"] == "2026-10-12T13:00:00.000Z"
    assert sent["attachmentIds"] == ["att-1"]


@respx.mock
def test_list_draft_notes_hits_feed_drafts(fake_cookies, fake_publication_url):
    route = respx.get(f"{SUBSTACK_COM}/api/v1/feed/drafts").mock(
        return_value=httpx.Response(200, json={"drafts": [{"id": 1}], "hasMore": False})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    assert list_draft_notes(client, limit=5)["drafts"] == [{"id": 1}]
    assert route.calls[0].request.url.params["limit"] == "5"


@respx.mock
def test_cli_create_draft_does_not_need_yes(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    respx.post(f"{SUBSTACK_COM}/api/v1/comment/draft").mock(
        return_value=httpx.Response(200, json={"id": 42, "status": "draft"})
    )
    feed = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    result = cli_runner.invoke(notes_app, ["create", "Save me", "--draft"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout.strip())["id"] == 42
    assert not feed.called


def test_cli_create_draft_still_requires_write_gate(isolated_config, authed_env, cli_runner):
    result = cli_runner.invoke(notes_app, ["create", "Save me", "--draft"])
    assert result.exit_code != 0
    assert "SUBSTACK_ENABLE_WRITE" in result.output


def test_cli_schedule_requires_yes(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    result = cli_runner.invoke(
        notes_app, ["create", "x", "--schedule", "2099-01-01T09:00Z"]
    )
    assert result.exit_code != 0
    assert "--yes" in result.output


def test_cli_schedule_rejects_past_time(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    result = cli_runner.invoke(
        notes_app, ["create", "x", "--schedule", "2001-01-01T09:00Z", "--yes"]
    )
    assert result.exit_code != 0
    assert "not in the future" in result.output


@respx.mock
def test_cli_schedule_happy_path(
    isolated_config, authed_env, write_enabled_env, cli_runner
):
    route = respx.post(f"{SUBSTACK_COM}/api/v1/comment/draft").mock(
        return_value=httpx.Response(200, json={"id": 7})
    )
    result = cli_runner.invoke(
        notes_app, ["create", "x", "--schedule", "2099-01-01T09:00Z", "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls[0].request.content)["trigger_at"] == "2099-01-01T09:00:00.000Z"


@respx.mock
def test_cli_drafts_lists_unwrapped(isolated_config, authed_env, cli_runner):
    respx.get(f"{SUBSTACK_COM}/api/v1/feed/drafts").mock(
        return_value=httpx.Response(200, json={"drafts": [{"id": 1}, {"id": 2}]})
    )
    result = cli_runner.invoke(notes_app, ["drafts"])
    assert result.exit_code == 0, result.output
    assert [d["id"] for d in json.loads(result.stdout.strip())] == [1, 2]


# ---------------------------------------------------------------------------
# body-json path: strong/em stripped at create_note and reply_to_note
# ---------------------------------------------------------------------------

def _doc_with_strong_top_level() -> dict:
    """ProseMirror doc with a strong mark at the top paragraph level."""
    return {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "bold", "marks": [{"type": "strong"}]},
                    {"type": "text", "text": " plain"},
                ],
            }
        ],
    }


def _doc_with_em_nested_in_list() -> dict:
    """ProseMirror doc with an em mark nested inside a list item."""
    return {
        "type": "doc",
        "content": [
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "italic",
                                        "marks": [{"type": "em"}],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }


# -- unit tests for _strip_unsupported_marks_from_doc -----------------------

def test_strip_marks_from_doc_removes_strong_top_level(capsys):
    doc = _doc_with_strong_top_level()
    result = _strip_unsupported_marks_from_doc(doc)
    para_nodes = result["content"][0]["content"]
    bold_node = para_nodes[0]
    assert "marks" not in bold_node
    assert bold_node["text"] == "bold"
    assert para_nodes[1] == {"type": "text", "text": " plain"}
    assert "strong" in capsys.readouterr().err


def test_strip_marks_from_doc_removes_em_nested_in_list(capsys):
    doc = _doc_with_em_nested_in_list()
    result = _strip_unsupported_marks_from_doc(doc)
    item_para = result["content"][0]["content"][0]["content"][0]
    text_node = item_para["content"][0]
    assert "marks" not in text_node
    assert text_node["text"] == "italic"
    assert "em" in capsys.readouterr().err


def test_strip_marks_from_doc_preserves_link_mark():
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": "click",
                        "marks": [{"type": "link", "attrs": {"href": "https://example.com"}}],
                    }
                ],
            }
        ],
    }
    result = _strip_unsupported_marks_from_doc(doc)
    node = result["content"][0]["content"][0]
    assert node["marks"][0]["type"] == "link"


# -- integration: create_note body_json path strips marks in POST body -------

@respx.mock
def test_create_note_body_json_strong_stripped_in_post(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env, capsys
):
    """create_note with body_json containing a strong mark must strip it before POST."""
    route = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    doc = _doc_with_strong_top_level()
    create_note(client, body_json=json.dumps(doc))
    sent = json.loads(route.calls[0].request.content)
    para_nodes = sent["bodyJson"]["content"][0]["content"]
    assert "marks" not in para_nodes[0], "strong mark must be stripped before POST"
    assert "strong" in capsys.readouterr().err


@respx.mock
def test_create_note_body_json_em_nested_stripped_in_post(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env, capsys
):
    """create_note with body_json containing a nested em mark must strip it before POST."""
    route = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    doc = _doc_with_em_nested_in_list()
    create_note(client, body_json=json.dumps(doc))
    sent = json.loads(route.calls[0].request.content)
    text_node = sent["bodyJson"]["content"][0]["content"][0]["content"][0]["content"][0]
    assert "marks" not in text_node, "em mark inside list item must be stripped before POST"
    assert "em" in capsys.readouterr().err


# -- integration: reply_to_note body_json path strips marks in POST body -----

@respx.mock
def test_reply_to_note_body_json_strong_stripped_in_post(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env, capsys
):
    """reply_to_note with body_json containing a strong mark must strip it before POST."""
    route = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    doc = _doc_with_strong_top_level()
    reply_to_note(client, 100, body_json=json.dumps(doc))
    sent = json.loads(route.calls[0].request.content)
    para_nodes = sent["bodyJson"]["content"][0]["content"]
    assert "marks" not in para_nodes[0], "strong mark must be stripped before POST"
    assert "strong" in capsys.readouterr().err


@respx.mock
def test_reply_to_note_body_json_em_nested_stripped_in_post(
    fake_cookies, fake_publication_url, isolated_config, write_enabled_env, capsys
):
    """reply_to_note with body_json containing a nested em mark must strip it before POST."""
    route = respx.post(f"{SUBSTACK_COM}/api/v1/comment/feed").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    doc = _doc_with_em_nested_in_list()
    reply_to_note(client, 100, body_json=json.dumps(doc))
    sent = json.loads(route.calls[0].request.content)
    text_node = sent["bodyJson"]["content"][0]["content"][0]["content"][0]["content"][0]
    assert "marks" not in text_node, "em mark inside list item must be stripped before POST"
    assert "em" in capsys.readouterr().err
