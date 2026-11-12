"""Tests for substack_cli.reactions — GET /api/v1/comment/{id}/reactors."""
import httpx
import pytest
import respx

from substack_cli.client import SubstackClient
from substack_cli.reactions import _project_reactor, get_comment_reactors

# ---------------------------------------------------------------------------
# Fixture data (sanitised — no real user ids, names, handles, or photo URLs)
# ---------------------------------------------------------------------------

REACTOR_FIXTURE = [
    {
        "id": 10000001,
        "name": "Alice Example",
        "photo_url": "https://substack-post-media.s3.amazonaws.com/public/images/fake-alice.jpeg",
        "primary_publication": {
            "id": 20000001,
            "subdomain": "aliceexample",
            "custom_domain_optional": False,
            "name": "Alice's Newsletter",
            "logo_url": None,
            "author_id": 10000001,
            "primary_user_id": 10000001,
            "user_id": 10000001,
            "handles_enabled": False,
            "explicit": False,
            "is_personal_mode": False,
            "payments_state": "disabled",
            "pledges_enabled": False,
            "ios_app_payments_enabled": False,
            "has_reply_rules": False,
        },
        "bestseller_tier": None,
        "status": {
            "bestsellerTier": None,
            "subscriberTier": 1,
            "leaderboard": None,
            "vip": False,
            "badge": {"type": "subscriber", "tier": 1, "accent_colors": None},
            "subscriber": None,
        },
        "is_subscribed": False,
        "is_following": False,
        "writes": "Alice's Newsletter",
    },
    {
        "id": 10000002,
        "name": "Bob Example",
        "photo_url": "https://substack-post-media.s3.amazonaws.com/public/images/fake-bob.jpeg",
        "primary_publication": {
            "id": 20000002,
            "subdomain": "bobexample",
            "custom_domain_optional": False,
            "name": "Bob's Weekly",
            "logo_url": None,
            "author_id": 10000002,
            "primary_user_id": 10000002,
            "user_id": 10000002,
            "handles_enabled": False,
            "explicit": False,
            "is_personal_mode": False,
            "payments_state": "enabled",
            "pledges_enabled": False,
            "ios_app_payments_enabled": False,
            "has_reply_rules": False,
        },
        "bestseller_tier": None,
        "status": {
            "bestsellerTier": None,
            "subscriberTier": 5,
            "leaderboard": None,
            "vip": False,
            "badge": {"type": "subscriber", "tier": 5, "accent_colors": None},
            "subscriber": {"publicationId": 99999, "label": "Paid subscriber"},
        },
        "is_subscribed": True,
        "is_following": True,
        "writes": "Bob's Weekly",
    },
]


# ---------------------------------------------------------------------------
# Core function tests
# ---------------------------------------------------------------------------


@respx.mock
def test_get_comment_reactors_publication_surface(fake_cookies, fake_publication_url):
    """Uses publication host for surface='publication'."""
    respx.get(f"{fake_publication_url}/api/v1/comment/111/reactors").mock(
        return_value=httpx.Response(200, json=REACTOR_FIXTURE)
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_comment_reactors(client, 111, surface="publication")
    assert len(result) == 2
    assert result[0]["id"] == 10000001
    assert result[1]["id"] == 10000002


@respx.mock
def test_get_comment_reactors_notes_surface_uses_host_a(fake_cookies, fake_publication_url):
    """Uses substack.com (host A) for surface='notes'."""
    respx.get("https://substack.com/api/v1/comment/222/reactors").mock(
        return_value=httpx.Response(200, json=REACTOR_FIXTURE[:1])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_comment_reactors(client, 222, surface="notes")
    assert len(result) == 1
    assert result[0]["id"] == 10000001


@respx.mock
def test_get_comment_reactors_returns_empty_list_on_no_reactions(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/api/v1/comment/333/reactors").mock(
        return_value=httpx.Response(200, json=[])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_comment_reactors(client, 333)
    assert result == []


@respx.mock
def test_get_comment_reactors_tolerates_envelope_shape(fake_cookies, fake_publication_url):
    """Tolerates a future {reactors: [...]} envelope."""
    respx.get(f"{fake_publication_url}/api/v1/comment/444/reactors").mock(
        return_value=httpx.Response(200, json={"reactors": REACTOR_FIXTURE})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_comment_reactors(client, 444)
    assert len(result) == 2


def test_get_comment_reactors_raises_on_invalid_surface(fake_cookies, fake_publication_url):
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(ValueError, match="Invalid surface"):
        get_comment_reactors(client, 555, surface="invalid")


# ---------------------------------------------------------------------------
# Projection tests
# ---------------------------------------------------------------------------


def test_project_reactor_extracts_expected_fields():
    projected = _project_reactor(REACTOR_FIXTURE[0])
    assert projected["user_id"] == 10000001
    assert projected["name"] == "Alice Example"
    assert projected["handle"] == "aliceexample"
    assert "fake-alice" in projected["photo_url"]
    assert projected["writes"] == "Alice's Newsletter"


def test_project_reactor_handles_missing_primary_publication():
    reactor = {"id": 99, "name": "No Pub", "photo_url": None, "writes": "No Pub"}
    projected = _project_reactor(reactor)
    assert projected["user_id"] == 99
    assert projected["handle"] is None


def test_project_reactor_handles_non_dict_gracefully():
    assert _project_reactor("not a dict") == {}
    assert _project_reactor(None) == {}
    assert _project_reactor(42) == {}


# ---------------------------------------------------------------------------
# CLI command tests
# ---------------------------------------------------------------------------


@respx.mock
def test_comments_reactions_cmd_outputs_projected_json(
    authed_env, fake_publication_url, cli_runner
):
    import json
    from substack_cli.app import app

    respx.get(f"{fake_publication_url}/api/v1/comment/111/reactors").mock(
        return_value=httpx.Response(200, json=REACTOR_FIXTURE)
    )
    result = cli_runner.invoke(app, ["comments", "reactions", "111"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["user_id"] == 10000001
    assert data[0]["handle"] == "aliceexample"
    assert "name" in data[0]
    assert "photo_url" in data[0]


@respx.mock
def test_comments_reactions_cmd_full_flag_returns_raw(
    authed_env, fake_publication_url, cli_runner
):
    import json
    from substack_cli.app import app

    respx.get(f"{fake_publication_url}/api/v1/comment/111/reactors").mock(
        return_value=httpx.Response(200, json=REACTOR_FIXTURE)
    )
    result = cli_runner.invoke(app, ["comments", "reactions", "111", "--full"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    # Raw objects have primary_publication key (not in projected)
    assert "primary_publication" in data[0]


@respx.mock
def test_comments_reactions_cmd_notes_surface(
    authed_env, fake_publication_url, cli_runner
):
    import json
    from substack_cli.app import app

    respx.get("https://substack.com/api/v1/comment/222/reactors").mock(
        return_value=httpx.Response(200, json=REACTOR_FIXTURE[:1])
    )
    result = cli_runner.invoke(app, ["comments", "reactions", "222", "--surface", "notes"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert len(data) == 1


@respx.mock
def test_comments_reactions_cmd_invalid_surface_exits_1(
    authed_env, fake_publication_url, cli_runner
):
    from substack_cli.app import app

    result = cli_runner.invoke(app, ["comments", "reactions", "111", "--surface", "bad"])
    assert result.exit_code == 1


@respx.mock
def test_comments_reactions_cmd_empty_list(
    authed_env, fake_publication_url, cli_runner
):
    import json
    from substack_cli.app import app

    respx.get(f"{fake_publication_url}/api/v1/comment/999/reactors").mock(
        return_value=httpx.Response(200, json=[])
    )
    result = cli_runner.invoke(app, ["comments", "reactions", "999"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data == []
