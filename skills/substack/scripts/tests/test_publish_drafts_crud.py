"""Tests for substack_cli.publish — draft CRUD + markdown->ProseMirror conversion.

No test in this suite performs a real network call; every Substack API
interaction is mocked with respx.
"""
import json

import httpx
import pytest
import respx

from substack_cli.client import SubstackApiError, SubstackClient, SUBSTACK_COM
from substack_cli.publish import (
    _markdown_to_prosemirror_doc,
    _prosemirror_doc_to_body_string,
    create_draft,
    delete_draft,
    get_draft,
    list_drafts,
    update_draft,
)


# ---------------------------------------------------------------------------
# list_drafts
# ---------------------------------------------------------------------------

@respx.mock
def test_list_drafts_tolerates_bare_array_shape(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/api/v1/drafts").mock(
        return_value=httpx.Response(200, json=[{"id": 1}])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)

    result = list_drafts(client)

    assert result == [{"id": 1}]


@respx.mock
def test_list_drafts_tolerates_paginated_envelope_shape(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/api/v1/drafts").mock(
        return_value=httpx.Response(200, json={"posts": [{"id": 1}], "hasMore": False})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)

    result = list_drafts(client)

    assert result == [{"id": 1}]


# ---------------------------------------------------------------------------
# get_draft
# ---------------------------------------------------------------------------

@respx.mock
def test_get_draft_by_id(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/api/v1/drafts/123").mock(
        return_value=httpx.Response(200, json={"id": 123, "draft_title": "Hi"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)

    result = get_draft(client, 123)

    assert result == {"id": 123, "draft_title": "Hi"}


# ---------------------------------------------------------------------------
# create_draft
# ---------------------------------------------------------------------------

@respx.mock
def test_create_draft_stringifies_body_not_nested_object(fake_cookies, fake_publication_url):
    """The most critical test: draft_body must be a JSON STRING, never a
    nested dict. Submitting a nested dict renders literal
    `{type:"doc",...}` text in the published post."""
    route = respx.post(f"{fake_publication_url}/api/v1/drafts").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)

    create_draft(
        client,
        title="Hello",
        body_markdown="Hello world",
        byline_ids=[{"id": 1, "publicationUserId": 2}],
    )

    sent_body = json.loads(route.calls[0].request.content)
    assert isinstance(sent_body["draft_body"], str)
    parsed_doc = json.loads(sent_body["draft_body"])
    assert parsed_doc["type"] == "doc"


@respx.mock
def test_create_draft_auto_derives_byline_from_self_profile_when_not_supplied(
    fake_cookies, fake_publication_url, no_sleep
):
    respx.get(f"{SUBSTACK_COM}/api/v1/user/profile/self").mock(
        return_value=httpx.Response(200, json={"id": 999, "publicationUserId": 888})
    )
    route = respx.post(f"{fake_publication_url}/api/v1/drafts").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)

    create_draft(client, title="Hello", body_markdown="Hello world")

    sent_body = json.loads(route.calls[0].request.content)
    # New contract: key is `draft_bylines`, value is [{id, is_guest}], byline
    # id derived from the self-profile id (999). No `byline_ids`, no
    # `publicationUserId`.
    assert "byline_ids" not in sent_body
    assert sent_body["draft_bylines"] == [{"id": 999, "is_guest": False}]


@respx.mock
def test_create_draft_uses_explicit_byline_ids_when_supplied(fake_cookies, fake_publication_url):
    profile_route = respx.get(f"{SUBSTACK_COM}/api/v1/user/profile/self").mock(
        return_value=httpx.Response(200, json={"id": 999, "publicationUserId": 888})
    )
    route = respx.post(f"{fake_publication_url}/api/v1/drafts").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    explicit_bylines = [{"id": 42, "is_guest": False}]

    create_draft(
        client,
        title="Hello",
        body_markdown="Hello world",
        byline_ids=explicit_bylines,
    )

    sent_body = json.loads(route.calls[0].request.content)
    # New contract: explicit bylines are sent verbatim under `draft_bylines`;
    # the self-profile is NOT fetched when bylines are supplied.
    assert "byline_ids" not in sent_body
    assert sent_body["draft_bylines"] == explicit_bylines
    assert not profile_route.called


# ---------------------------------------------------------------------------
# update_draft
# ---------------------------------------------------------------------------

@respx.mock
def test_update_draft_uses_put(fake_cookies, fake_publication_url):
    route = respx.put(f"{fake_publication_url}/api/v1/drafts/123").mock(
        return_value=httpx.Response(200, json={"id": 123, "draft_title": "Updated"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)

    result = update_draft(client, 123, draft_title="Updated")

    assert route.called
    assert route.calls[0].request.method == "PUT"
    assert result["draft_title"] == "Updated"


# ---------------------------------------------------------------------------
# delete_draft
# ---------------------------------------------------------------------------

def test_delete_draft_requires_write_gate(isolated_config, authed_env, fake_cookies, fake_publication_url):
    """No respx mock registered on purpose — the write gate must block
    delete_draft before any HTTP call is attempted."""
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)

    with pytest.raises((SubstackApiError, ValueError)):
        delete_draft(client, 123)


# ---------------------------------------------------------------------------
# _markdown_to_prosemirror_doc
# ---------------------------------------------------------------------------

def test_markdown_single_paragraph():
    doc = _markdown_to_prosemirror_doc("Hello world")

    assert doc["type"] == "doc"
    assert len(doc["content"]) == 1
    paragraph = doc["content"][0]
    assert paragraph["type"] == "paragraph"
    assert paragraph["content"][0]["type"] == "text"
    assert paragraph["content"][0]["text"] == "Hello world"


def test_markdown_heading_levels_1_to_3():
    for level, prefix in [(1, "#"), (2, "##"), (3, "###")]:
        doc = _markdown_to_prosemirror_doc(f"{prefix} Heading {level}")
        heading = doc["content"][0]
        assert heading["type"] == "heading"
        assert heading["attrs"]["level"] == level
        assert heading["content"][0]["text"] == f"Heading {level}"


def test_markdown_bold_and_italic_marks():
    bold_doc = _markdown_to_prosemirror_doc("**bold**")
    bold_text_node = bold_doc["content"][0]["content"][0]
    assert bold_text_node["text"] == "bold"
    assert any(mark["type"] == "strong" for mark in bold_text_node["marks"])

    italic_doc = _markdown_to_prosemirror_doc("*italic*")
    italic_text_node = italic_doc["content"][0]["content"][0]
    assert italic_text_node["text"] == "italic"
    assert any(mark["type"] == "em" for mark in italic_text_node["marks"])


def test_markdown_link_mark_with_href():
    doc = _markdown_to_prosemirror_doc("[text](url)")
    text_node = doc["content"][0]["content"][0]

    assert text_node["text"] == "text"
    link_mark = next(mark for mark in text_node["marks"] if mark["type"] == "link")
    assert link_mark["attrs"]["href"] == "url"


def test_markdown_unsupported_list_syntax_raises_not_implemented_with_guidance():
    with pytest.raises(NotImplementedError) as exc_info:
        _markdown_to_prosemirror_doc("- item")

    assert "--body-json" in str(exc_info.value)


# ---------------------------------------------------------------------------
# _prosemirror_doc_to_body_string
# ---------------------------------------------------------------------------

def test_prosemirror_doc_to_body_string_is_valid_json_string_not_dict():
    doc = {"type": "doc", "content": []}

    result = _prosemirror_doc_to_body_string(doc)

    assert isinstance(result, str)
    parsed = json.loads(result)
    assert isinstance(parsed, dict)
    assert parsed == doc
