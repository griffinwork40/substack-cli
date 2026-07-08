"""Substack CLI — permissive response models and shape-normalization helpers.

Substack's API is undocumented, versionless, and known to change response
envelopes without notice (e.g. the 2026-05 change that moved /drafts from a
bare array to {posts, hasMore, nextCursor}). These helpers accept multiple
known shapes instead of enforcing one strict schema, so the CLI degrades
to a clear error instead of a silent wrong answer when Substack changes
shape again.
"""
from typing import Any, TypedDict, Optional


class Post(TypedDict, total=False):
    id: int
    title: str
    subtitle: Optional[str]
    slug: str
    post_date: str
    audience: str
    canonical_url: str
    type: str
    wordcount: Optional[int]


class Draft(TypedDict, total=False):
    id: int
    draft_title: str
    draft_subtitle: Optional[str]
    draft_body: str
    type: str


class Comment(TypedDict, total=False):
    id: int
    body: str
    name: str
    date: str
    user_id: int


def extract_list(data: Any, *candidate_keys: str) -> list:
    """1. bare list -> returned as-is.
    2. dict containing one of candidate_keys whose value is a list -> that list.
    3. otherwise -> raise ValueError naming the actual top-level keys seen."""
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in candidate_keys:
            value = data.get(key)
            if isinstance(value, list):
                return value
        raise ValueError(
            f"Unrecognized response shape: expected a bare list or a dict "
            f"containing one of {list(candidate_keys)!r} mapped to a list, "
            f"but top-level keys seen were {list(data.keys())!r}"
        )

    raise ValueError(
        f"Unrecognized response shape: expected a bare list or a dict "
        f"containing one of {list(candidate_keys)!r}, got {type(data).__name__}"
    )


def extract_pagination_meta(data: Any) -> dict:
    """Best-effort pull of hasMore/nextCursor/total/isCapped from a dict.
    {} if data is a bare list or has none of these keys. Never raises."""
    if not isinstance(data, dict):
        return {}

    known_fields = ("hasMore", "nextCursor", "total", "isCapped")
    return {field: data[field] for field in known_fields if field in data}
