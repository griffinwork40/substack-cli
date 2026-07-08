"""Tests for substack_cli.models — shape-normalization helpers.

No network calls; pure data transforms only.
"""
import pytest

from substack_cli.models import Draft, Post, extract_list, extract_pagination_meta


def test_extract_list_bare_array_returned_as_is():
    data = [1, 2, 3]
    result = extract_list(data)
    assert result == [1, 2, 3]
    assert result is data


def test_extract_list_posts_envelope():
    posts = [{"id": 1}, {"id": 2}]
    data = {"posts": posts, "hasMore": True, "nextCursor": "x"}
    result = extract_list(data, "posts")
    assert result == posts
    assert result is posts


def test_extract_list_comments_envelope():
    comments = [{"id": 1, "body": "hi"}]
    data = {"comments": comments}
    result = extract_list(data, "comments")
    assert result == comments


def test_extract_list_items_envelope():
    items = [{"id": 1}, {"id": 2}, {"id": 3}]
    data = {"items": items}
    result = extract_list(data, "items")
    assert result == items


def test_extract_list_unrecognized_shape_raises_value_error_naming_keys_seen():
    data = {"foo": "bar"}
    with pytest.raises(ValueError) as exc_info:
        extract_list(data, "posts")
    assert "foo" in str(exc_info.value)


def test_extract_pagination_meta_bare_array_returns_empty_dict():
    assert extract_pagination_meta([1, 2]) == {}


def test_extract_pagination_meta_extracts_has_more_and_next_cursor():
    data = {"hasMore": True, "nextCursor": "abc"}
    assert extract_pagination_meta(data) == {"hasMore": True, "nextCursor": "abc"}


def test_extract_pagination_meta_no_known_fields_returns_empty_dict():
    assert extract_pagination_meta({"foo": "bar"}) == {}


def test_post_typeddict_tolerates_extra_unknown_fields():
    post: Post = {
        "id": 1,
        "title": "Hello World",
        "slug": "hello-world",
        "totally_unknown_field": "surprise",
        "another_surprise_field": 42,
    }
    assert post["id"] == 1
    assert post["totally_unknown_field"] == "surprise"


def test_draft_typeddict_tolerates_missing_optional_fields():
    draft: Draft = {"id": 99, "draft_title": "Untitled Draft"}
    assert draft["id"] == 99
    assert draft["draft_title"] == "Untitled Draft"
    assert "draft_body" not in draft
