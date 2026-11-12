"""Tests for notes replies + notes list --all.

All Substack API calls are mocked with respx. No real network requests.
"""
import json
from datetime import datetime, timezone

import httpx
import pytest
import respx

from substack_cli.app import notes_app
from substack_cli.client import SUBSTACK_COM, SubstackClient
from substack_cli.notes import (
    _parse_since,
    get_note_replies,
    list_notes,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPLIES_URL_TEMPLATE = f"{SUBSTACK_COM}/api/v1/reader/comment/{{id}}/replies"
NOTES_URL = f"{SUBSTACK_COM}/api/v1/notes"
PROFILE_URL_TEMPLATE = f"{SUBSTACK_COM}/api/v1/reader/feed/profile/{{uid}}"
SELF_URL = f"{SUBSTACK_COM}/api/v1/user/profile/self"


def _branch(cid: int, children_count: int = 0, descendants=None) -> dict:
    return {
        "comment": {"id": cid, "body": f"reply {cid}", "children_count": children_count},
        "descendantComments": descendants or [],
    }


def _page(branches, automod=None, next_cursor=None):
    return {
        "rootComment": {"id": 1, "body": "root"},
        "commentBranches": branches,
        "automodHiddenBranches": automod or [],
        "moreBranches": 1 if next_cursor else 0,
        "nextCursor": next_cursor,
    }


# ---------------------------------------------------------------------------
# _parse_since
# ---------------------------------------------------------------------------

def test_parse_since_date_only():
    dt = _parse_since("2026-10-01")
    assert dt == datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_parse_since_z_suffix():
    dt = _parse_since("2026-10-01T12:00:00Z")
    assert dt == datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def test_parse_since_offset():
    dt = _parse_since("2026-10-01T08:00:00-04:00")
    assert dt == datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def test_parse_since_naive_treated_as_utc():
    dt = _parse_since("2026-10-01T12:00:00")
    assert dt == datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def test_parse_since_bad_value_raises():
    with pytest.raises(ValueError, match="Invalid --since"):
        _parse_since("not-a-date")


# ---------------------------------------------------------------------------
# get_note_replies — single page (default)
# ---------------------------------------------------------------------------

@respx.mock
def test_replies_single_page_returns_raw(fake_cookies, fake_publication_url):
    page = _page([_branch(10)], next_cursor="tok1")
    respx.get(REPLIES_URL_TEMPLATE.format(id=99)).mock(
        return_value=httpx.Response(200, json=page)
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_note_replies(client, 99)
    assert result["nextCursor"] == "tok1"
    assert result["commentBranches"][0]["comment"]["id"] == 10
    assert "pages" not in result  # raw envelope, no pages key


@respx.mock
def test_replies_single_page_passes_cursor_param(fake_cookies, fake_publication_url):
    route = respx.get(REPLIES_URL_TEMPLATE.format(id=99)).mock(
        return_value=httpx.Response(200, json=_page([]))
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    get_note_replies(client, 99, cursor="mytoken")
    assert route.calls[0].request.url.params["cursor"] == "mytoken"


@respx.mock
def test_replies_single_page_no_cursor_param_when_none(fake_cookies, fake_publication_url):
    route = respx.get(REPLIES_URL_TEMPLATE.format(id=99)).mock(
        return_value=httpx.Response(200, json=_page([]))
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    get_note_replies(client, 99)
    assert "cursor" not in route.calls[0].request.url.params


# ---------------------------------------------------------------------------
# get_note_replies — --all pagination
# ---------------------------------------------------------------------------

@respx.mock
def test_replies_all_follows_cursor_until_null(fake_cookies, fake_publication_url):
    route = respx.get(REPLIES_URL_TEMPLATE.format(id=5)).mock(
        side_effect=[
            httpx.Response(200, json=_page([_branch(10), _branch(11)], next_cursor="tok2")),
            httpx.Response(200, json=_page([_branch(12)], next_cursor=None)),
        ]
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_note_replies(client, 5, fetch_all=True)
    assert route.call_count == 2
    assert len(result["commentBranches"]) == 3
    assert result["nextCursor"] is None
    assert result["moreBranches"] == 0
    assert result["pages"] == 2
    ids = [b["comment"]["id"] for b in result["commentBranches"]]
    assert ids == [10, 11, 12]


@respx.mock
def test_replies_all_single_page_no_cursor(fake_cookies, fake_publication_url):
    respx.get(REPLIES_URL_TEMPLATE.format(id=5)).mock(
        return_value=httpx.Response(200, json=_page([_branch(10)]))
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_note_replies(client, 5, fetch_all=True)
    assert result["pages"] == 1
    assert result["nextCursor"] is None


@respx.mock
def test_replies_all_merges_automod_branches(fake_cookies, fake_publication_url):
    respx.get(REPLIES_URL_TEMPLATE.format(id=7)).mock(
        side_effect=[
            httpx.Response(200, json={
                "rootComment": {"id": 7},
                "commentBranches": [_branch(20)],
                "automodHiddenBranches": [_branch(21)],
                "moreBranches": 1,
                "nextCursor": "next",
            }),
            httpx.Response(200, json={
                "rootComment": {"id": 7},
                "commentBranches": [_branch(22)],
                "automodHiddenBranches": [_branch(23)],
                "moreBranches": 0,
                "nextCursor": None,
            }),
        ]
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_note_replies(client, 7, fetch_all=True)
    assert len(result["commentBranches"]) == 2
    assert len(result["automodHiddenBranches"]) == 2
    cb_ids = [b["comment"]["id"] for b in result["commentBranches"]]
    ah_ids = [b["comment"]["id"] for b in result["automodHiddenBranches"]]
    assert cb_ids == [20, 22]
    assert ah_ids == [21, 23]


# ---------------------------------------------------------------------------
# get_note_replies — --descendants recursion
# ---------------------------------------------------------------------------

@respx.mock
def test_replies_descendants_expands_branch_with_children(fake_cookies, fake_publication_url):
    # Root note 5 has branch 10 with children_count=1 but no descendantComments
    # Reply 10 itself has one reply: branch 30
    respx.get(REPLIES_URL_TEMPLATE.format(id=5)).mock(
        return_value=httpx.Response(200, json=_page([_branch(10, children_count=1)]))
    )
    respx.get(REPLIES_URL_TEMPLATE.format(id=10)).mock(
        return_value=httpx.Response(200, json=_page([_branch(30, children_count=0)]))
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_note_replies(client, 5, fetch_descendants=True)
    assert result["pages"] == 1
    branch_10 = result["commentBranches"][0]
    assert branch_10["comment"]["id"] == 10
    # descendantComments should now have comment 30
    descs = branch_10["descendantComments"]
    assert len(descs) == 1
    assert descs[0]["id"] == 30


@respx.mock
def test_replies_descendants_no_expansion_when_count_satisfied(fake_cookies, fake_publication_url):
    # children_count == len(descendantComments) → no extra request
    route_5 = respx.get(REPLIES_URL_TEMPLATE.format(id=5)).mock(
        return_value=httpx.Response(200, json=_page([
            _branch(10, children_count=1, descendants=[{"id": 99}])
        ]))
    )
    route_10 = respx.get(REPLIES_URL_TEMPLATE.format(id=10)).mock(
        return_value=httpx.Response(200, json=_page([]))
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    get_note_replies(client, 5, fetch_descendants=True)
    assert route_5.call_count == 1
    assert not route_10.called  # no extra fetch needed


@respx.mock
def test_replies_descendants_expands_automod_branches(fake_cookies, fake_publication_url):
    respx.get(REPLIES_URL_TEMPLATE.format(id=5)).mock(
        return_value=httpx.Response(200, json={
            "rootComment": {"id": 5},
            "commentBranches": [],
            "automodHiddenBranches": [_branch(50, children_count=1)],
            "moreBranches": 0,
            "nextCursor": None,
        })
    )
    respx.get(REPLIES_URL_TEMPLATE.format(id=50)).mock(
        return_value=httpx.Response(200, json=_page([_branch(51)]))
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_note_replies(client, 5, fetch_descendants=True)
    ah = result["automodHiddenBranches"]
    assert ah[0]["comment"]["id"] == 50
    assert ah[0]["descendantComments"][0]["id"] == 51


@respx.mock
def test_replies_descendants_request_cap_sets_truncated(fake_cookies, fake_publication_url, monkeypatch):
    import substack_cli.notes as notes_mod
    monkeypatch.setattr(notes_mod, "_DESCENDANTS_MAX_REQUESTS", 1)
    # First fetch of id=5 costs 1 request (hits the cap immediately for children)
    branches = [_branch(10, children_count=1), _branch(11, children_count=1)]
    respx.get(REPLIES_URL_TEMPLATE.format(id=5)).mock(
        return_value=httpx.Response(200, json=_page(branches))
    )
    # id=10 fetch would be request #2 but cap is 1, so it should be skipped
    route_10 = respx.get(REPLIES_URL_TEMPLATE.format(id=10)).mock(
        return_value=httpx.Response(200, json=_page([]))
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_note_replies(client, 5, fetch_descendants=True)
    assert result.get("truncated") is True
    assert not route_10.called


@respx.mock
def test_replies_descendants_depth_cap_sets_truncated(fake_cookies, fake_publication_url, monkeypatch):
    import substack_cli.notes as notes_mod
    monkeypatch.setattr(notes_mod, "_DESCENDANTS_MAX_DEPTH", 0)
    respx.get(REPLIES_URL_TEMPLATE.format(id=5)).mock(
        return_value=httpx.Response(200, json=_page([_branch(10, children_count=1)]))
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_note_replies(client, 5, fetch_descendants=True)
    assert result.get("truncated") is True


@respx.mock
def test_replies_descendants_deep_flatten(fake_cookies, fake_publication_url):
    """Three levels deep: 5 -> 10 -> 20. Flat list should be [comment20]."""
    respx.get(REPLIES_URL_TEMPLATE.format(id=5)).mock(
        return_value=httpx.Response(200, json=_page([_branch(10, children_count=1)]))
    )
    respx.get(REPLIES_URL_TEMPLATE.format(id=10)).mock(
        return_value=httpx.Response(200, json=_page([_branch(20, children_count=0)]))
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_note_replies(client, 5, fetch_descendants=True)
    descs = result["commentBranches"][0]["descendantComments"]
    assert [d["id"] for d in descs] == [20]


# ---------------------------------------------------------------------------
# CLI: notes replies command
# ---------------------------------------------------------------------------

@respx.mock
def test_cli_replies_single_page(isolated_config, authed_env, cli_runner):
    page = _page([_branch(10)], next_cursor="tok")
    respx.get(REPLIES_URL_TEMPLATE.format(id=42)).mock(
        return_value=httpx.Response(200, json=page)
    )
    result = cli_runner.invoke(notes_app, ["replies", "42"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout.strip())
    assert data["nextCursor"] == "tok"
    assert data["commentBranches"][0]["comment"]["id"] == 10


@respx.mock
def test_cli_replies_c_prefix_stripped(isolated_config, authed_env, cli_runner):
    respx.get(REPLIES_URL_TEMPLATE.format(id=42)).mock(
        return_value=httpx.Response(200, json=_page([]))
    )
    result = cli_runner.invoke(notes_app, ["replies", "c-42"])
    assert result.exit_code == 0, result.output


@respx.mock
def test_cli_replies_all(isolated_config, authed_env, cli_runner):
    respx.get(REPLIES_URL_TEMPLATE.format(id=5)).mock(
        side_effect=[
            httpx.Response(200, json=_page([_branch(10)], next_cursor="tok")),
            httpx.Response(200, json=_page([_branch(11)])),
        ]
    )
    result = cli_runner.invoke(notes_app, ["replies", "5", "--all"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout.strip())
    assert data["pages"] == 2
    assert data["nextCursor"] is None
    assert len(data["commentBranches"]) == 2


@respx.mock
def test_cli_replies_descendants(isolated_config, authed_env, cli_runner):
    respx.get(REPLIES_URL_TEMPLATE.format(id=5)).mock(
        return_value=httpx.Response(200, json=_page([_branch(10, children_count=1)]))
    )
    respx.get(REPLIES_URL_TEMPLATE.format(id=10)).mock(
        return_value=httpx.Response(200, json=_page([_branch(20)]))
    )
    result = cli_runner.invoke(notes_app, ["replies", "5", "--descendants"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout.strip())
    assert data["commentBranches"][0]["descendantComments"][0]["id"] == 20


def test_cli_replies_bad_id(isolated_config, authed_env, cli_runner):
    result = cli_runner.invoke(notes_app, ["replies", "p-999"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# list_notes -- --all pagination
# ---------------------------------------------------------------------------

def _note_item(cid: int, date: str) -> dict:
    return {"comment": {"id": cid, "date": date, "body": f"note {cid}"}}


@respx.mock
def test_list_notes_all_paginates_notes_endpoint(fake_cookies, fake_publication_url):
    respx.get(PROFILE_URL_TEMPLATE.format(uid=42)).mock(
        side_effect=[
            httpx.Response(200, json={
                "items": [_note_item(1, "2026-10-10T10:00:00Z")],
                "nextCursor": "cur2",
            }),
            httpx.Response(200, json={
                "items": [_note_item(2, "2026-10-09T10:00:00Z")],
                "nextCursor": None,
            }),
        ]
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = list_notes(client, user_id=42, fetch_all=True)
    assert result["pages"] == 2
    assert result["nextCursor"] is None
    assert len(result["items"]) == 2
    assert result["items"][0]["comment"]["id"] == 1


@respx.mock
def test_list_notes_all_sends_cursor_on_second_page(fake_cookies, fake_publication_url):
    route = respx.get(PROFILE_URL_TEMPLATE.format(uid=99)).mock(
        side_effect=[
            httpx.Response(200, json={"items": [_note_item(1, "2026-10-10T10:00:00Z")], "nextCursor": "cur2"}),
            httpx.Response(200, json={"items": [], "nextCursor": None}),
        ]
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    list_notes(client, user_id=99, fetch_all=True)
    first_params = route.calls[0].request.url.params
    assert "cursor" not in first_params
    second_params = route.calls[1].request.url.params
    assert second_params["cursor"] == "cur2"


@respx.mock
def test_list_notes_all_stops_at_since(fake_cookies, fake_publication_url):
    since = datetime(2026, 10, 9, tzinfo=timezone.utc)
    respx.get(PROFILE_URL_TEMPLATE.format(uid=42)).mock(
        side_effect=[
            httpx.Response(200, json={
                "items": [
                    _note_item(1, "2026-10-10T10:00:00Z"),  # newer -> keep
                    _note_item(2, "2026-10-08T10:00:00Z"),  # older -> drop + stop
                ],
                "nextCursor": "would-not-follow",
            }),
        ]
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = list_notes(client, user_id=42, fetch_all=True, since=since)
    assert len(result["items"]) == 1
    assert result["items"][0]["comment"]["id"] == 1
    assert result["nextCursor"] is None  # stopped early


@respx.mock
def test_list_notes_all_stops_at_max_pages(fake_cookies, fake_publication_url):
    respx.get(PROFILE_URL_TEMPLATE.format(uid=42)).mock(
        return_value=httpx.Response(200, json={
            "items": [_note_item(1, "2026-10-10T10:00:00Z")],
            "nextCursor": "always-more",
        })
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = list_notes(client, user_id=42, fetch_all=True, max_pages=3)
    assert result["pages"] == 3
    assert result["nextCursor"] == "always-more"


@respx.mock
def test_list_notes_all_resolves_mine_via_self(fake_cookies, fake_publication_url, no_sleep):
    respx.get(SELF_URL).mock(
        return_value=httpx.Response(200, json={"id": 77, "name": "Me"})
    )
    respx.get(PROFILE_URL_TEMPLATE.format(uid=77)).mock(
        return_value=httpx.Response(200, json={"items": [], "nextCursor": None})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = list_notes(client, mine=True, fetch_all=True)
    assert result["pages"] == 1


def test_list_notes_all_requires_mine_or_user_id(fake_cookies, fake_publication_url):
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(ValueError, match="--all requires"):
        list_notes(client, fetch_all=True)


# ---------------------------------------------------------------------------
# list_notes — unchanged default behaviour
# ---------------------------------------------------------------------------

@respx.mock
def test_list_notes_default_unchanged(fake_cookies, fake_publication_url):
    route = respx.get(f"{SUBSTACK_COM}/api/v1/reader/feed").mock(
        return_value=httpx.Response(200, json={"items": [{"entity_key": "c-1"}]})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = list_notes(client, limit=10)
    assert route.called
    assert result["items"][0]["entity_key"] == "c-1"
    assert "pages" not in result  # no --all envelope


@respx.mock
def test_list_notes_user_id_unchanged(fake_cookies, fake_publication_url):
    route = respx.get(PROFILE_URL_TEMPLATE.format(uid=42)).mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    list_notes(client, user_id=42)
    assert route.called


# ---------------------------------------------------------------------------
# CLI: notes list --all
# ---------------------------------------------------------------------------

@respx.mock
def test_cli_notes_list_all(isolated_config, authed_env, cli_runner, no_sleep):
    respx.get(SELF_URL).mock(
        return_value=httpx.Response(200, json={"id": 42})
    )
    respx.get(PROFILE_URL_TEMPLATE.format(uid=42)).mock(
        side_effect=[
            httpx.Response(200, json={"items": [_note_item(1, "2026-10-10T10:00:00Z")], "nextCursor": "c2"}),
            httpx.Response(200, json={"items": [_note_item(2, "2026-10-09T10:00:00Z")], "nextCursor": None}),
        ]
    )
    result = cli_runner.invoke(notes_app, ["list", "--mine", "--all"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout.strip())
    assert data["pages"] == 2
    assert len(data["items"]) == 2


@respx.mock
def test_cli_notes_list_all_with_since(isolated_config, authed_env, cli_runner, no_sleep):
    respx.get(SELF_URL).mock(
        return_value=httpx.Response(200, json={"id": 42})
    )
    respx.get(PROFILE_URL_TEMPLATE.format(uid=42)).mock(
        return_value=httpx.Response(200, json={
            "items": [
                _note_item(1, "2026-10-10T10:00:00Z"),
                _note_item(2, "2026-10-08T10:00:00Z"),
            ],
            "nextCursor": "would-not-follow",
        })
    )
    result = cli_runner.invoke(notes_app, ["list", "--mine", "--all", "--since", "2026-10-09"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout.strip())
    assert len(data["items"]) == 1
    assert data["items"][0]["comment"]["id"] == 1


@respx.mock
def test_cli_notes_list_all_max_pages(isolated_config, authed_env, cli_runner, no_sleep):
    respx.get(SELF_URL).mock(
        return_value=httpx.Response(200, json={"id": 42})
    )
    respx.get(PROFILE_URL_TEMPLATE.format(uid=42)).mock(
        return_value=httpx.Response(200, json={
            "items": [_note_item(1, "2026-10-10T10:00:00Z")],
            "nextCursor": "always",
        })
    )
    result = cli_runner.invoke(notes_app, ["list", "--mine", "--all", "--max-pages", "2"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout.strip())
    assert data["pages"] == 2


@respx.mock
def test_cli_notes_list_default_unchanged(isolated_config, authed_env, cli_runner):
    """Without --all, notes list must use the old profile-feed endpoint."""
    respx.get(SELF_URL).mock(
        return_value=httpx.Response(200, json={"id": 42})
    )
    profile_route = respx.get(PROFILE_URL_TEMPLATE.format(uid=42)).mock(
        return_value=httpx.Response(200, json={"items": [{"entity_key": "c-1"}]})
    )
    notes_route = respx.get(NOTES_URL).mock(
        return_value=httpx.Response(200, json={"items": [], "nextCursor": None})
    )
    result = cli_runner.invoke(notes_app, ["list", "--mine"])
    assert result.exit_code == 0, result.output
    assert profile_route.called
    assert not notes_route.called


def test_cli_notes_list_all_requires_mine_or_user_id(isolated_config, authed_env, cli_runner):
    result = cli_runner.invoke(notes_app, ["list", "--all"])
    assert result.exit_code != 0
    assert "--all requires" in result.output or "requires" in result.output


def test_cli_notes_list_bad_since(isolated_config, authed_env, cli_runner):
    result = cli_runner.invoke(notes_app, ["list", "--mine", "--all", "--since", "not-a-date"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# NEW: wrapped descendantComments normalisation (bug fix tests)
# ---------------------------------------------------------------------------

def _wrapped(cid: int, **extra) -> dict:
    """Simulate the real API shape: {type:'comment', comment:{...}}."""
    inner = {"id": cid, "body": f"reply {cid}", "children_count": 0}
    inner.update(extra)
    return {"type": "comment", "comment": inner}


def _bare(cid: int, **extra) -> dict:
    inner = {"id": cid, "body": f"reply {cid}", "children_count": 0}
    inner.update(extra)
    return inner


@respx.mock
def test_descendants_wrapped_inline_gets_unwrapped(fake_cookies, fake_publication_url):
    """Inline descendantComments that are {type,comment} wrapped must be normalised to bare."""
    page = _page([{
        "comment": {"id": 10, "body": "root branch", "children_count": 0},
        # Inline entries from real API come wrapped
        "descendantComments": [_wrapped(20), _wrapped(21)],
    }])
    respx.get(REPLIES_URL_TEMPLATE.format(id=5)).mock(
        return_value=httpx.Response(200, json=page)
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_note_replies(client, 5, fetch_descendants=True)
    descs = result["commentBranches"][0]["descendantComments"]
    assert len(descs) == 2
    # Every entry must be a bare dict (has id at top level, no 'type'/'comment' wrapper)
    for d in descs:
        assert "id" in d, f"entry missing top-level id: {d}"
        assert d.get("type") != "comment" or "comment" not in d, f"entry still wrapped: {d}"
    assert [d["id"] for d in descs] == [20, 21]


@respx.mock
def test_descendants_mixed_inline_and_fetched_yields_bare(fake_cookies, fake_publication_url):
    """Mixed inline (wrapped) + recursively fetched entries both come out as bare dicts."""
    # Branch 10 has children_count=2 but only 1 inline (wrapped) descendant → triggers fetch
    page = _page([{
        "comment": {"id": 10, "body": "branch", "children_count": 2},
        "descendantComments": [_wrapped(20)],  # only 1, children_count=2 → recurse
    }])
    respx.get(REPLIES_URL_TEMPLATE.format(id=5)).mock(
        return_value=httpx.Response(200, json=page)
    )
    # Recursive fetch for comment 10 returns branch 30 with wrapped inline descendant 31
    respx.get(REPLIES_URL_TEMPLATE.format(id=10)).mock(
        return_value=httpx.Response(200, json=_page([{
            "comment": {"id": 30, "body": "deep branch", "children_count": 0},
            "descendantComments": [_wrapped(31)],
        }]))
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_note_replies(client, 5, fetch_descendants=True)
    descs = result["commentBranches"][0]["descendantComments"]
    # Should contain bare dicts for 30 and 31 (depth-first: sub_branch comment then its descs)
    assert all("id" in d for d in descs), f"not all bare: {descs}"
    assert all(not ("type" in d and "comment" in d and "id" not in d) for d in descs), \
        f"some still wrapped: {descs}"
    ids = [d["id"] for d in descs]
    assert ids == [30, 31], f"expected [30, 31], got {ids}"


@respx.mock
def test_descendants_deduplication_preserves_order(fake_cookies, fake_publication_url):
    """Duplicate ids (same entry from inline + fetch) are deduped, first-seen wins."""
    # children_count=2 but 2 inline wrapped entries → len(existing)==2 == children_count → no fetch
    page = _page([{
        "comment": {"id": 10, "body": "branch", "children_count": 0},
        "descendantComments": [_wrapped(20), _wrapped(20), _wrapped(21)],  # 20 duplicated
    }])
    respx.get(REPLIES_URL_TEMPLATE.format(id=5)).mock(
        return_value=httpx.Response(200, json=page)
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_note_replies(client, 5, fetch_descendants=True)
    descs = result["commentBranches"][0]["descendantComments"]
    assert [d["id"] for d in descs] == [20, 21], f"expected deduped [20,21], got {descs}"


@respx.mock
def test_no_descendants_flag_leaves_raw_untouched(fake_cookies, fake_publication_url):
    """Without --descendants, descendantComments must be returned exactly as the API gave them."""
    wrapped_entry = _wrapped(20)
    page = _page([{
        "comment": {"id": 10, "body": "branch", "children_count": 1},
        "descendantComments": [wrapped_entry],
    }])
    respx.get(REPLIES_URL_TEMPLATE.format(id=5)).mock(
        return_value=httpx.Response(200, json=page)
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    # Default: no fetch_descendants
    result = get_note_replies(client, 5)
    descs = result["commentBranches"][0]["descendantComments"]
    # Raw API shape preserved — entry is still wrapped
    assert descs[0] == wrapped_entry, f"raw entry was mutated: {descs[0]}"


@respx.mock
def test_descendants_already_bare_inline_not_double_unwrapped(fake_cookies, fake_publication_url):
    """Already-bare inline entries must survive _normalize_descendants unchanged."""
    page = _page([{
        "comment": {"id": 10, "body": "branch", "children_count": 0},
        "descendantComments": [_bare(20), _bare(21)],
    }])
    respx.get(REPLIES_URL_TEMPLATE.format(id=5)).mock(
        return_value=httpx.Response(200, json=page)
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_note_replies(client, 5, fetch_descendants=True)
    descs = result["commentBranches"][0]["descendantComments"]
    assert [d["id"] for d in descs] == [20, 21]
    assert all("id" in d for d in descs)
