"""Tests for substack_cli.automations — READ-ONLY Automations commands.

All HTTP is mocked via respx. Fixtures are synthetic: they mirror the live
response shapes (drip campaigns, per-email bodies, stats, audience estimate,
saved segments, subscriber tags) with made-up ids, titles, and text.
"""
import json

import httpx
import pytest
import respx

from substack_cli import automations
from substack_cli.app import app, automations_app
from substack_cli.automations import (
    describe_trigger,
    email_text_payload,
    get_audience_estimate,
    get_campaign,
    get_campaign_email,
    get_campaign_stats,
    get_segment,
    list_campaigns,
    list_segments,
    list_subscriber_tags,
    order_emails,
    prosemirror_to_text,
    summarize_campaign,
)
from substack_cli.client import SubstackApiError, SubstackClient

PUB = "https://example-pub.substack.com"  # synthetic; set by the authed_env override below

CAMP_A = "aaaaaaaa-0000-4000-8000-000000000001"
CAMP_B = "bbbbbbbb-0000-4000-8000-000000000002"
E1 = "eeeeeeee-0000-4000-8000-000000000001"
E2 = "eeeeeeee-0000-4000-8000-000000000002"
E3 = "eeeeeeee-0000-4000-8000-000000000003"
E9 = "eeeeeeee-0000-4000-8000-000000000009"
SEG = 101  # segment and tag ids are integers in the live API
TAG = 201


def _email(eid, campaign, subject, created):
    return {
        "id": eid,
        "drip_campaign_id": campaign,
        "subject": subject,
        "subtitle": None,
        "body": f"s3://example-bucket/drip/{eid}.json",
        "created_at": created,
        "updated_at": created,
    }


def _trigger(tid, campaign, frm, to, delay):
    return {
        "id": tid,
        "drip_campaign_id": campaign,
        "from_email_id": frm,
        "to_email_id": to,
        "delay": delay,
        "condition": None,
        "created_at": "2026-01-01T00:00:00.000Z",
        "updated_at": "2026-01-01T00:00:00.000Z",
    }


def _campaign_a():
    """Three-email welcome sequence; arrays deliberately out of send order."""
    return {
        "id": CAMP_A,
        "title": "Example welcome series",
        "publication_id": 1000,
        "state": "started",
        "first_enabled_at": "2026-01-02T00:00:00.000Z",
        "stopped_at": None,
        "created_at": "2026-01-01T00:00:00.000Z",
        "updated_at": "2026-01-03T00:00:00.000Z",
        "condition_subscription_type": "free",
        "condition_section_id": None,
        "trigger_type": "subscription",
        "trigger_config": None,
        "saved_segment_id": None,
        "subscriber_tag_id": None,
        # created_at order (E3 first) differs from chain order (E1, E2, E3)
        "emails": [
            _email(E2, CAMP_A, "Second email", "2026-01-01T00:02:00.000Z"),
            _email(E3, CAMP_A, "Third email", "2026-01-01T00:00:30.000Z"),
            _email(E1, CAMP_A, "First email", "2026-01-01T00:01:00.000Z"),
        ],
        "triggers": [
            _trigger("t3", CAMP_A, E2, E3, 120),
            _trigger("t1", CAMP_A, None, E1, 0),
            _trigger("t2", CAMP_A, E1, E2, 72),
        ],
    }


def _campaign_b():
    """Segment + tag targeted, stopped, with a non-null trigger_config."""
    return {
        "id": CAMP_B,
        "title": "Example re-engagement",
        "publication_id": 1000,
        "state": "stopped",
        "first_enabled_at": None,
        "stopped_at": None,
        "created_at": "2026-02-01T00:00:00.000Z",
        "updated_at": "2026-02-01T00:00:00.000Z",
        "condition_subscription_type": "free",
        "condition_section_id": None,
        "trigger_type": "reading_activity",
        "trigger_config": {"threshold": 3, "window_days": 14},
        "saved_segment_id": SEG,
        "subscriber_tag_id": TAG,
        "emails": [_email(E9, CAMP_B, "Only email", "2026-02-01T00:00:00.000Z")],
        "triggers": [_trigger("t9", CAMP_B, None, E9, 0)],
    }


SEGMENTS = {
    "segments": [
        {
            "id": SEG,
            "name": "Example engaged readers",
            "description": None,
            "query": {"subscription_type": "free", "activity_rating_gte": 3},
            "publication_id": 1000,
            "created_by_user_id": 2000,
            "created_at": "2026-01-01T00:00:00.000Z",
            "updated_at": "2026-01-01T00:00:00.000Z",
        }
    ]
}
TAGS = {"tags": [{"id": TAG, "name": "Example tag", "slug": "example-tag",
                  "publication_id": 1000, "created_by_user_id": 2000,
                  "created_at": "2026-01-01T00:00:00.000Z",
                  "updated_at": "2026-01-01T00:00:00.000Z"}]}

DOC = {
    "type": "doc",
    "content": [
        {"type": "heading", "attrs": {"level": 2, "textAlign": None},
         "content": [{"type": "text", "text": "Hello there"}]},
        {"type": "paragraph", "attrs": {"textAlign": None}, "content": [
            {"type": "text", "text": "Read "},
            {"type": "text", "text": "the guide",
             "marks": [{"type": "link", "attrs": {"href": "https://example.com/guide",
                                                  "target": "_blank", "rel": None, "class": None}}]},
            {"type": "text", "text": " today.", "marks": [{"type": "strong"}]},
        ]},
        {"type": "ordered_list", "attrs": {"start": 1}, "content": [
            {"type": "list_item", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Step one"}]}]},
            {"type": "list_item", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Step two"}]}]},
        ]},
        {"type": "blockquote", "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "A quote"}]}]},
        {"type": "captionedImage", "content": [
            {"type": "image2", "attrs": {"src": "https://example.com/i.png", "alt": "chart"}}]},
        {"type": "horizontal_rule"},
        {"type": "button", "attrs": {"url": "https://example.com/sub", "text": "Subscribe",
                                     "action": None, "class": None}},
        {"type": "mystery_node", "content": [{"type": "text", "text": "kept anyway"}]},
    ],
}
EMAIL_RESPONSE = {"drip_campaign_title": "First email", "drip_campaign_body": json.dumps(DOC)}


@pytest.fixture
def authed_env(monkeypatch, fake_cookies):
    """Override conftest's authed_env to point at a synthetic publication URL."""
    monkeypatch.setenv("SUBSTACK_COOKIES_STRING", fake_cookies)
    monkeypatch.setenv("SUBSTACK_PUBLICATION_URL", PUB)


@pytest.fixture(autouse=True)
def _fast(no_sleep):
    """Throttle sleeps are irrelevant under respx."""


@pytest.fixture
def wide_console(monkeypatch):
    """Render --pretty tables wide enough that cells do not wrap mid-word."""
    from rich.console import Console
    monkeypatch.setattr(automations, "_console", Console(stderr=True, width=400))


@pytest.fixture
def client(fake_cookies):
    return SubstackClient(cookies=fake_cookies, publication_url=PUB, min_interval=0)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_order_emails_follows_trigger_chain_not_array_order():
    ordered = order_emails(_campaign_a())
    assert [e["id"] for e in ordered] == [E1, E2, E3]
    assert [e["position"] for e in ordered] == [1, 2, 3]
    assert [e["after"] for e in ordered] == ["trigger", "previous_email", "previous_email"]
    assert [e["delay_hours"] for e in ordered] == [0, 72, 120]
    assert [e["cumulative_delay_hours"] for e in ordered] == [0, 72, 192]
    assert all(e["in_chain"] for e in ordered)
    assert all("body" not in e for e in ordered)  # s3 pointer dropped


def test_order_emails_unchained_email_appended_and_flagged():
    camp = _campaign_a()
    camp["emails"].append(_email("orphan", CAMP_A, "Orphan", "2025-01-01T00:00:00.000Z"))
    ordered = order_emails(camp)
    assert [e["id"] for e in ordered][-1] == "orphan"
    orphan = ordered[-1]
    assert orphan["in_chain"] is False and orphan["after"] is None
    assert orphan["delay_hours"] is None and orphan["cumulative_delay_hours"] is None


def test_order_emails_tolerates_cycles_and_missing_keys():
    camp = {"emails": [{"id": "x"}, {"id": "y"}],
            "triggers": [{"from_email_id": None, "to_email_id": "x", "delay": 1},
                         {"from_email_id": "x", "to_email_id": "y"},
                         {"from_email_id": "y", "to_email_id": "x", "delay": 5}]}
    ordered = order_emails(camp)
    assert [e["id"] for e in ordered] == ["x", "y"]
    assert ordered[1]["cumulative_delay_hours"] is None  # delay missing


def _repro_triggers():
    # Non-root trigger y->x listed first: x must still take its timing from
    # the root trigger the chain walk followed, not the first one listed.
    return [{"from_email_id": "y", "to_email_id": "x", "delay": 5},
            {"from_email_id": None, "to_email_id": "x", "delay": 1},
            {"from_email_id": "x", "to_email_id": "y", "delay": 2}]


@pytest.mark.parametrize("order", [[0, 1, 2], [1, 2, 0], [2, 0, 1], [2, 1, 0]])
def test_order_emails_uses_trigger_the_walk_followed(order):
    triggers = [_repro_triggers()[i] for i in order]
    ordered = order_emails({"emails": [{"id": "x"}, {"id": "y"}], "triggers": triggers})
    x, y = ordered
    assert (x["id"], y["id"]) == ("x", "y")
    assert x["after"] == "trigger" and x["from_email_id"] is None
    assert x["delay_hours"] == 1 and x["cumulative_delay_hours"] == 1
    assert y["after"] == "previous_email" and y["from_email_id"] == "x"
    assert y["delay_hours"] == 2 and y["cumulative_delay_hours"] == 3


def test_order_emails_empty_campaign():
    assert order_emails({}) == []
    assert order_emails({"emails": None, "triggers": None}) == []


def test_describe_trigger_known_unknown_and_config():
    assert describe_trigger("subscription", None) == "when a reader subscribes"
    assert "delay_days=30" in describe_trigger("before_renewal", {"delay_days": 30})
    assert "unknown trigger type 'new_kind'" in describe_trigger("new_kind", {"x": 1})
    assert describe_trigger(None, None) == "no trigger type"
    assert "config" in describe_trigger("subscription", [1, 2])


def test_summarize_campaign_resolves_segment_and_tag():
    seg_index = {s["id"]: s for s in SEGMENTS["segments"]}
    tag_index = {t["id"]: t for t in TAGS["tags"]}
    s = summarize_campaign(_campaign_b(), seg_index, tag_index)
    assert s["running"] is False and s["state"] == "stopped"
    assert s["audience"]["saved_segment"]["query"] == {"subscription_type": "free",
                                                       "activity_rating_gte": 3}
    assert s["audience"]["subscriber_tag"]["slug"] == "example-tag"
    assert s["trigger"]["config"] == {"threshold": 3, "window_days": 14}
    assert s["email_count"] == 1


def test_summarize_campaign_missing_fields_do_not_crash():
    s = summarize_campaign({"id": "z"})
    assert s["title"] is None and s["emails"] == [] and s["audience"]["saved_segment"] is None


def test_summarize_campaign_unresolved_segment_keeps_id():
    s = summarize_campaign(_campaign_b(), {}, {})
    assert s["audience"]["saved_segment_id"] == SEG
    assert s["audience"]["saved_segment"] is None


def test_prosemirror_to_text_renders_blocks_and_keeps_links():
    text = prosemirror_to_text(json.dumps(DOC))
    assert "## Hello there" in text
    assert "Read [the guide](https://example.com/guide) today." in text
    assert "1. Step one\n2. Step two" in text
    assert "> A quote" in text
    assert "[image: chart](https://example.com/i.png)" in text
    assert "---" in text
    assert "[Subscribe](https://example.com/sub)" in text
    assert "kept anyway" in text


def test_prosemirror_to_text_edge_inputs():
    assert prosemirror_to_text(None) == ""
    assert prosemirror_to_text("not json") == "not json"
    assert prosemirror_to_text({}) == ""
    assert prosemirror_to_text({"type": "doc", "content": None}) == "\n"


def test_email_text_payload_counts_words_and_tolerates_bad_input():
    p = email_text_payload(EMAIL_RESPONSE, CAMP_A, E1)
    assert p["subject"] == "First email" and p["word_count"] > 5
    empty = email_text_payload(None, CAMP_A, E1)
    assert empty["text"] == "" and empty["subject"] is None and empty["word_count"] == 0


@pytest.mark.parametrize("bad", ["a/b", "../x", "a?b", "a#b", "a\\b", ""])
def test_ids_are_validated_before_any_request(client, bad):
    with pytest.raises(ValueError):
        get_campaign(client, bad)


# ---------------------------------------------------------------------------
# Core operations — paths, host, GET only
# ---------------------------------------------------------------------------


@respx.mock
def test_core_reads_hit_expected_paths(client):
    routes = {
        "list": respx.get(f"{PUB}/api/v1/drip-campaigns").mock(
            return_value=httpx.Response(200, json={"campaigns": [_campaign_a()]})),
        "one": respx.get(f"{PUB}/api/v1/drip-campaigns/custom/{CAMP_A}").mock(
            return_value=httpx.Response(200, json=_campaign_a())),
        "email": respx.get(f"{PUB}/api/v1/drip-campaigns/{CAMP_A}/email/{E1}").mock(
            return_value=httpx.Response(200, json=EMAIL_RESPONSE)),
        "stats": respx.get(f"{PUB}/api/v1/drip-campaigns/{CAMP_A}/stats").mock(
            return_value=httpx.Response(200, json={"stats": None})),
        "est": respx.get(f"{PUB}/api/v1/drip-campaigns/{CAMP_A}/audience-estimate").mock(
            return_value=httpx.Response(200, json={"estimatedAudienceSize": 12})),
        "segs": respx.get(f"{PUB}/api/v1/subscriber-stats/saved-segments").mock(
            return_value=httpx.Response(200, json=SEGMENTS)),
        "seg": respx.get(f"{PUB}/api/v1/subscriber-stats/saved-segments/{SEG}").mock(
            return_value=httpx.Response(200, json={"segment": SEGMENTS["segments"][0], "count": 5})),
        "tags": respx.get(f"{PUB}/api/v1/subscriber-tags").mock(
            return_value=httpx.Response(200, json=TAGS)),
    }
    assert list_campaigns(client)[0]["id"] == CAMP_A
    assert get_campaign(client, CAMP_A)["id"] == CAMP_A
    assert get_campaign_email(client, CAMP_A, E1)["drip_campaign_title"] == "First email"
    assert get_campaign_stats(client, CAMP_A) == {"stats": None}
    assert get_audience_estimate(client, CAMP_A)["estimatedAudienceSize"] == 12
    assert list_segments(client)[0]["id"] == SEG
    assert get_segment(client, str(SEG)) == {"segment": SEGMENTS["segments"][0], "count": 5}
    assert list_subscriber_tags(client)[0]["slug"] == "example-tag"
    assert all(r.call_count == 1 for r in routes.values())
    assert all(c.request.method == "GET" for c in respx.calls)


@respx.mock
def test_get_campaign_unwraps_campaign_envelope(client):
    respx.get(f"{PUB}/api/v1/drip-campaigns/custom/{CAMP_A}").mock(
        return_value=httpx.Response(200, json={"campaign": _campaign_a()}))
    assert get_campaign(client, CAMP_A)["id"] == CAMP_A


@respx.mock
def test_list_campaigns_unexpected_shape_raises_value_error(client):
    respx.get(f"{PUB}/api/v1/drip-campaigns").mock(
        return_value=httpx.Response(200, json={"something_else": []}))
    with pytest.raises(ValueError):
        list_campaigns(client)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_root_help_lists_automations(cli_runner):
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0 and "automations" in result.output


def test_module_registers_no_write_verbs():
    """Guard: the module must never call client.post/put/delete/patch."""
    import inspect
    src = inspect.getsource(automations)
    for verb in (".post(", ".put(", ".delete(", ".patch(", "_request("):
        assert verb not in src


@respx.mock
def test_cli_list_orders_emails_and_resolves_segment(isolated_config, authed_env, cli_runner):
    respx.get(f"{PUB}/api/v1/drip-campaigns").mock(
        return_value=httpx.Response(200, json={"campaigns": [_campaign_a(), _campaign_b()]}))
    respx.get(f"{PUB}/api/v1/subscriber-stats/saved-segments").mock(
        return_value=httpx.Response(200, json=SEGMENTS))
    respx.get(f"{PUB}/api/v1/subscriber-tags").mock(
        return_value=httpx.Response(200, json=TAGS))
    result = cli_runner.invoke(automations_app, ["list"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert [e["subject"] for e in data[0]["emails"]] == ["First email", "Second email", "Third email"]
    assert data[0]["running"] is True
    assert data[1]["audience"]["saved_segment"]["name"] == "Example engaged readers"
    assert data[1]["audience"]["subscriber_tag"]["name"] == "Example tag"
    assert all(c.request.method == "GET" for c in respx.calls)


@respx.mock
def test_cli_list_skips_lookups_when_not_referenced(isolated_config, authed_env, cli_runner):
    respx.get(f"{PUB}/api/v1/drip-campaigns").mock(
        return_value=httpx.Response(200, json={"campaigns": [_campaign_a()]}))
    result = cli_runner.invoke(automations_app, ["list"])
    assert result.exit_code == 0 and len(respx.calls) == 1


@respx.mock
def test_cli_list_segment_lookup_failure_is_soft(isolated_config, authed_env, cli_runner):
    respx.get(f"{PUB}/api/v1/drip-campaigns").mock(
        return_value=httpx.Response(200, json={"campaigns": [_campaign_b()]}))
    respx.get(f"{PUB}/api/v1/subscriber-stats/saved-segments").mock(
        return_value=httpx.Response(403, json={"error": "forbidden"}))
    respx.get(f"{PUB}/api/v1/subscriber-tags").mock(
        return_value=httpx.Response(200, json=TAGS))
    result = cli_runner.invoke(automations_app, ["list"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data[0]["audience"]["saved_segment_id"] == SEG
    seg = data[0]["audience"]["saved_segment"]
    assert seg["error"] is True and seg["status_code"] == 403 and seg["message"]
    # the failing segments lookup does not disturb the tags lookup
    assert data[0]["audience"]["subscriber_tag"]["name"] == "Example tag"


@respx.mock
def test_cli_list_tag_lookup_failure_is_inline(isolated_config, authed_env, cli_runner):
    respx.get(f"{PUB}/api/v1/drip-campaigns").mock(
        return_value=httpx.Response(200, json={"campaigns": [_campaign_b(), _campaign_a()]}))
    respx.get(f"{PUB}/api/v1/subscriber-stats/saved-segments").mock(
        return_value=httpx.Response(200, json=SEGMENTS))
    respx.get(f"{PUB}/api/v1/subscriber-tags").mock(
        return_value=httpx.Response(403, json={"error": "forbidden"}))
    result = cli_runner.invoke(automations_app, ["list"])
    assert result.exit_code == 0
    b, a = json.loads(result.stdout)
    assert b["audience"]["subscriber_tag_id"] == TAG
    tag = b["audience"]["subscriber_tag"]
    assert tag["error"] is True and tag["status_code"] == 403
    assert b["audience"]["saved_segment"]["name"] == "Example engaged readers"
    # a campaign that references no tag/segment is unaffected by the failure
    assert a["audience"]["subscriber_tag"] is None and a["audience"]["saved_segment"] is None


@respx.mock
def test_cli_get_segment_and_tag_lookup_failures_are_inline(isolated_config, authed_env,
                                                             cli_runner):
    respx.get(f"{PUB}/api/v1/drip-campaigns/custom/{CAMP_B}").mock(
        return_value=httpx.Response(200, json=_campaign_b()))
    respx.get(f"{PUB}/api/v1/subscriber-stats/saved-segments").mock(
        return_value=httpx.Response(403, json={"error": "forbidden"}))
    respx.get(f"{PUB}/api/v1/subscriber-tags").mock(
        return_value=httpx.Response(500, json={"error": "boom"}))
    respx.get(f"{PUB}/api/v1/drip-campaigns/{CAMP_B}/stats").mock(
        return_value=httpx.Response(200, json={"stats": None}))
    respx.get(f"{PUB}/api/v1/drip-campaigns/{CAMP_B}/audience-estimate").mock(
        return_value=httpx.Response(200, json={"estimatedAudienceSize": 3}))
    result = cli_runner.invoke(automations_app, ["get", CAMP_B])
    assert result.exit_code == 0, result.output
    aud = json.loads(result.stdout)["audience"]
    assert aud["saved_segment_id"] == SEG and aud["subscriber_tag_id"] == TAG
    assert aud["saved_segment"]["error"] is True and aud["saved_segment"]["status_code"] == 403
    assert aud["subscriber_tag"]["error"] is True and aud["subscriber_tag"]["status_code"] == 500


@respx.mock
def test_cli_get_lookup_success_without_id_match_is_none(isolated_config, authed_env,
                                                         cli_runner):
    respx.get(f"{PUB}/api/v1/drip-campaigns/custom/{CAMP_B}").mock(
        return_value=httpx.Response(200, json=_campaign_b()))
    respx.get(f"{PUB}/api/v1/subscriber-stats/saved-segments").mock(
        return_value=httpx.Response(200, json={"segments": []}))
    respx.get(f"{PUB}/api/v1/subscriber-tags").mock(
        return_value=httpx.Response(200, json={"tags": []}))
    respx.get(f"{PUB}/api/v1/drip-campaigns/{CAMP_B}/stats").mock(
        return_value=httpx.Response(200, json={"stats": None}))
    respx.get(f"{PUB}/api/v1/drip-campaigns/{CAMP_B}/audience-estimate").mock(
        return_value=httpx.Response(200, json={}))
    aud = json.loads(cli_runner.invoke(automations_app, ["get", CAMP_B]).stdout)["audience"]
    assert aud["saved_segment"] is None and aud["subscriber_tag"] is None


@respx.mock
def test_cli_list_pretty_segment_failure_shows_id_not_none(isolated_config, authed_env,
                                                           cli_runner, wide_console):
    respx.get(f"{PUB}/api/v1/drip-campaigns").mock(
        return_value=httpx.Response(200, json={"campaigns": [_campaign_b()]}))
    respx.get(f"{PUB}/api/v1/subscriber-stats/saved-segments").mock(
        return_value=httpx.Response(403, json={"error": "forbidden"}))
    respx.get(f"{PUB}/api/v1/subscriber-tags").mock(
        return_value=httpx.Response(200, json=TAGS))
    result = cli_runner.invoke(automations_app, ["list", "--pretty"])
    assert result.exit_code == 0
    out = " ".join(result.output.split())
    assert "segment: None" not in out
    assert f"segment {SEG}" in out and "lookup failed" in out


@respx.mock
def test_cli_list_details_and_raw(isolated_config, authed_env, cli_runner):
    respx.get(f"{PUB}/api/v1/drip-campaigns").mock(
        return_value=httpx.Response(200, json={"campaigns": [_campaign_a()]}))
    respx.get(f"{PUB}/api/v1/drip-campaigns/{CAMP_A}/stats").mock(
        return_value=httpx.Response(200, json={"stats": None}))
    respx.get(f"{PUB}/api/v1/drip-campaigns/{CAMP_A}/audience-estimate").mock(
        return_value=httpx.Response(200, json={"estimatedAudienceSize": None, "windowDays": None,
                                               "hasSegment": False, "updatedAt": "x"}))
    result = cli_runner.invoke(automations_app, ["list", "--details"])
    data = json.loads(result.stdout)
    assert data[0]["stats"] == {"available": False, "stats": None}
    assert data[0]["audience_estimate"]["hasSegment"] is False
    raw = json.loads(cli_runner.invoke(automations_app, ["list", "--raw"]).stdout)
    assert raw[0]["emails"][0]["body"].startswith("s3://")


@respx.mock
def test_cli_list_skips_non_dict_campaign_items(isolated_config, authed_env, cli_runner):
    respx.get(f"{PUB}/api/v1/drip-campaigns").mock(
        return_value=httpx.Response(200, json={"campaigns": [None, _campaign_b()]}))
    respx.get(f"{PUB}/api/v1/subscriber-stats/saved-segments").mock(
        return_value=httpx.Response(200, json=SEGMENTS))
    respx.get(f"{PUB}/api/v1/subscriber-tags").mock(
        return_value=httpx.Response(200, json=TAGS))
    result = cli_runner.invoke(automations_app, ["list"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert len(data) == 1 and data[0]["id"] == CAMP_B
    assert data[0]["audience"]["saved_segment"]["name"] == "Example engaged readers"
    raw = json.loads(cli_runner.invoke(automations_app, ["list", "--raw"]).stdout)
    assert raw[0] is None and raw[1]["id"] == CAMP_B


@respx.mock
def test_cli_list_pretty_renders_table(isolated_config, authed_env, cli_runner, wide_console):
    respx.get(f"{PUB}/api/v1/drip-campaigns").mock(
        return_value=httpx.Response(200, json={"campaigns": [_campaign_a()]}))
    result = cli_runner.invoke(automations_app, ["list", "--pretty"])
    assert result.exit_code == 0
    # Rich table goes to stderr; CliRunner's result.output carries it.
    assert "First email" in result.output
    assert "estimate" not in result.output and "stats" not in result.output
    assert len(respx.calls) == 1  # no --details: no estimate/stats GETs


@respx.mock
def test_cli_list_details_pretty_shows_estimate_and_stats(isolated_config, authed_env,
                                                          cli_runner, wide_console):
    camp_c = _campaign_a()
    camp_c["id"] = "cccccccc-0000-4000-8000-000000000003"
    camp_c["title"] = "Third automation"
    respx.get(f"{PUB}/api/v1/drip-campaigns").mock(
        return_value=httpx.Response(200, json={"campaigns": [_campaign_a(), camp_c]}))
    respx.get(f"{PUB}/api/v1/drip-campaigns/{CAMP_A}/audience-estimate").mock(
        return_value=httpx.Response(200, json={"estimatedAudienceSize": 4321, "windowDays": 30,
                                               "hasSegment": False, "updatedAt": "x"}))
    respx.get(f"{PUB}/api/v1/drip-campaigns/{CAMP_A}/stats").mock(
        return_value=httpx.Response(200, json={"stats": None}))
    respx.get(f"{PUB}/api/v1/drip-campaigns/{camp_c['id']}/audience-estimate").mock(
        return_value=httpx.Response(200, json={"estimatedAudienceSize": None, "windowDays": None,
                                               "hasSegment": False, "updatedAt": "x"}))
    respx.get(f"{PUB}/api/v1/drip-campaigns/{camp_c['id']}/stats").mock(
        return_value=httpx.Response(503, json={"error": "[bold]down[/bold]"}))
    camp_d = _campaign_a()
    camp_d["id"] = "dddddddd-0000-4000-8000-000000000004"
    respx.get(f"{PUB}/api/v1/drip-campaigns").mock(
        return_value=httpx.Response(200, json={"campaigns": [_campaign_a(), camp_c, camp_d]}))
    respx.get(f"{PUB}/api/v1/drip-campaigns/{camp_d['id']}/audience-estimate").mock(
        return_value=httpx.Response(500, json={"error": "boom"}))
    respx.get(f"{PUB}/api/v1/drip-campaigns/{camp_d['id']}/stats").mock(
        return_value=httpx.Response(200, json={"stats": {"sent": 10, "note": "[red]x[/red]"}}))
    result = cli_runner.invoke(automations_app, ["list", "--details", "--pretty"])
    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())
    assert "lookup failed (HTTP 500)" in out  # erroring estimate → error cell
    assert "sent=10" in out and "note=[red]x[/red]" in out  # server text is not markup
    assert "estimate" in out and "stats" in out
    assert "4,321" in out and "not run yet" in out
    assert "unknown" in out  # null estimate renders readably
    assert "lookup failed (HTTP 503)" in out  # erroring stats → error cell
    assert "None" not in out and "'error'" not in out


@respx.mock
def test_cli_get_with_bodies(isolated_config, authed_env, cli_runner):
    respx.get(f"{PUB}/api/v1/drip-campaigns/custom/{CAMP_A}").mock(
        return_value=httpx.Response(200, json=_campaign_a()))
    respx.get(f"{PUB}/api/v1/drip-campaigns/{CAMP_A}/stats").mock(
        return_value=httpx.Response(200, json={"stats": {"sent": 10}}))
    respx.get(f"{PUB}/api/v1/drip-campaigns/{CAMP_A}/audience-estimate").mock(
        return_value=httpx.Response(200, json={"estimatedAudienceSize": 42, "windowDays": 30,
                                               "hasSegment": False, "updatedAt": "x"}))
    respx.get(f"{PUB}/api/v1/drip-campaigns/{CAMP_A}/email/{E1}").mock(
        return_value=httpx.Response(200, json=EMAIL_RESPONSE))
    respx.get(f"{PUB}/api/v1/drip-campaigns/{CAMP_A}/email/{E2}").mock(
        return_value=httpx.Response(200, json={"drip_campaign_title": "Second email",
                                               "drip_campaign_body": None}))
    respx.get(f"{PUB}/api/v1/drip-campaigns/{CAMP_A}/email/{E3}").mock(
        return_value=httpx.Response(500, json={"error": "boom"}))
    result = cli_runner.invoke(automations_app, ["get", CAMP_A, "--bodies"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["stats"] == {"available": True, "stats": {"sent": 10}}
    assert data["audience_estimate"]["estimatedAudienceSize"] == 42
    e1, e2, e3 = data["emails"]
    assert "[the guide](https://example.com/guide)" in e1["text"]
    assert e2["text"] == "" and e2["word_count"] == 0
    assert e3["text_error"]["status_code"] == 500


@respx.mock
def test_cli_get_stats_error_is_inline_not_fatal(isolated_config, authed_env, cli_runner):
    respx.get(f"{PUB}/api/v1/drip-campaigns/custom/{CAMP_A}").mock(
        return_value=httpx.Response(200, json=_campaign_a()))
    respx.get(f"{PUB}/api/v1/drip-campaigns/{CAMP_A}/stats").mock(
        return_value=httpx.Response(404, json={"error": "nope"}))
    respx.get(f"{PUB}/api/v1/drip-campaigns/{CAMP_A}/audience-estimate").mock(
        return_value=httpx.Response(200, json={}))
    result = cli_runner.invoke(automations_app, ["get", CAMP_A])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["stats"]["status_code"] == 404


@respx.mock
def test_cli_get_404_exits_nonzero_with_json_error(isolated_config, authed_env, cli_runner):
    respx.get(f"{PUB}/api/v1/drip-campaigns/custom/{CAMP_A}").mock(
        return_value=httpx.Response(404, json={"error": "not found"}))
    result = cli_runner.invoke(automations_app, ["get", CAMP_A])
    assert result.exit_code == 1
    err = json.loads(result.stderr.strip().splitlines()[0])
    assert err["error"] is True and err["status_code"] == 404


@respx.mock
def test_cli_email_formats(isolated_config, authed_env, cli_runner):
    respx.get(f"{PUB}/api/v1/drip-campaigns/{CAMP_A}/email/{E1}").mock(
        return_value=httpx.Response(200, json=EMAIL_RESPONSE))
    as_json = json.loads(cli_runner.invoke(automations_app, ["email", CAMP_A, E1]).stdout)
    assert as_json["subject"] == "First email" and "## Hello there" in as_json["text"]
    as_text = cli_runner.invoke(automations_app, ["email", CAMP_A, E1, "--format", "text"]).stdout
    assert as_text.startswith("Subject: First email\n\n## Hello there")
    as_raw = json.loads(cli_runner.invoke(automations_app, ["email", CAMP_A, E1, "--format", "raw"]).stdout)
    assert as_raw == EMAIL_RESPONSE


def test_cli_email_bad_format_errors_before_request(isolated_config, authed_env, cli_runner):
    with respx.mock(assert_all_called=False) as mock:
        result = cli_runner.invoke(automations_app, ["email", CAMP_A, E1, "--format", "html"])
        assert result.exit_code == 1 and len(mock.calls) == 0


@respx.mock
def test_cli_stats_null_and_audience(isolated_config, authed_env, cli_runner):
    respx.get(f"{PUB}/api/v1/drip-campaigns/{CAMP_A}/stats").mock(
        return_value=httpx.Response(200, json={"stats": None}))
    respx.get(f"{PUB}/api/v1/drip-campaigns/{CAMP_A}/audience-estimate").mock(
        return_value=httpx.Response(200, json={"estimatedAudienceSize": 7, "windowDays": 7,
                                               "hasSegment": True, "updatedAt": "x"}))
    stats = json.loads(cli_runner.invoke(automations_app, ["stats", CAMP_A]).stdout)
    assert stats == {"available": False, "stats": None}
    est = json.loads(cli_runner.invoke(automations_app, ["audience", CAMP_A]).stdout)
    assert est["estimatedAudienceSize"] == 7


@respx.mock
def test_cli_segments_segment_and_tags(isolated_config, authed_env, cli_runner):
    respx.get(f"{PUB}/api/v1/subscriber-stats/saved-segments").mock(
        return_value=httpx.Response(200, json=SEGMENTS))
    respx.get(f"{PUB}/api/v1/subscriber-stats/saved-segments/{SEG}").mock(
        return_value=httpx.Response(200, json={"segment": SEGMENTS["segments"][0], "count": 5}))
    respx.get(f"{PUB}/api/v1/subscriber-tags").mock(
        return_value=httpx.Response(200, json=TAGS))
    segs = json.loads(cli_runner.invoke(automations_app, ["segments"]).stdout)
    assert segs[0]["query"]["activity_rating_gte"] == 3
    seg = json.loads(cli_runner.invoke(automations_app, ["segment", str(SEG)]).stdout)
    assert seg["segment"]["id"] == SEG and seg["count"] == 5
    tags = json.loads(cli_runner.invoke(automations_app, ["subscriber-tags"]).stdout)
    assert tags[0]["slug"] == "example-tag"


@respx.mock
def test_cli_auth_error_401_includes_cookie_hint(isolated_config, authed_env, cli_runner):
    respx.get(f"{PUB}/api/v1/drip-campaigns").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"}))
    result = cli_runner.invoke(automations_app, ["list"])
    assert result.exit_code == 1
    err = json.loads(result.stderr.strip().splitlines()[0])
    assert err["status_code"] == 401 and "cookie" in err["message"].lower()


def test_cli_missing_cookies_is_json_error_with_no_request(isolated_config, cli_runner):
    with respx.mock(assert_all_called=False) as mock:
        result = cli_runner.invoke(automations_app, ["list"])
        assert result.exit_code == 1 and len(mock.calls) == 0
        assert json.loads(result.stderr.strip().splitlines()[0])["error"] is True


def test_cli_invalid_id_is_json_error_with_no_request(isolated_config, authed_env, cli_runner):
    with respx.mock(assert_all_called=False) as mock:
        result = cli_runner.invoke(automations_app, ["stats", "../etc"])
        assert result.exit_code == 1 and len(mock.calls) == 0
