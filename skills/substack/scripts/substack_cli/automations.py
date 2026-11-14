"""Substack CLI — Automations (read-only).

Substack "Automations" are publication email sequences. The API calls them
"drip campaigns". This module also reads the saved subscriber segments and
subscriber tags that automations target. Every command here issues GET
requests only, by design:

    LIST CAMPAIGNS     GET /api/v1/drip-campaigns
    ONE CAMPAIGN       GET /api/v1/drip-campaigns/custom/{campaign_id}
    ONE EMAIL'S TEXT   GET /api/v1/drip-campaigns/{campaign_id}/email/{email_id}
    CAMPAIGN STATS     GET /api/v1/drip-campaigns/{campaign_id}/stats
    AUDIENCE ESTIMATE  GET /api/v1/drip-campaigns/{campaign_id}/audience-estimate
    SAVED SEGMENTS     GET /api/v1/subscriber-stats/saved-segments
    ONE SEGMENT        GET /api/v1/subscriber-stats/saved-segments/{segment_id}
    SUBSCRIBER TAGS    GET /api/v1/subscriber-tags

All are on host "P" (the publication subdomain) and need the session cookie.
All were live-verified (2026-11) with GET requests only. Segment and tag ids
are integers; campaign and email ids are UUID strings.

SCOPE: strictly READ-ONLY. The same family has endpoints that edit or start
a campaign, add/remove emails or change their timing, create/duplicate/
delete campaigns, SEND a preview email, and create/edit/delete segments and
subscriber tags. None of those are implemented here: a wrong call would
change, start, or send an email sequence to real subscribers. See
`references/substack-api.md` § Automations for the list.

EMAIL ORDER: the API returns `emails` and `triggers` unordered. Each trigger
links `from_email_id` (null for the first email) to `to_email_id` and carries
a `delay` in hours. Emails are ordered by walking that chain; emails the chain
does not reach are appended afterwards, flagged `in_chain: false`.

EMAIL TEXT: the campaign list only carries an `s3://` pointer in `body`. The
text comes from the per-email endpoint, whose `drip_campaign_body` is a
ProseMirror document serialized as a JSON string. `prosemirror_to_text`
renders it as readable plain text with links kept as `[text](url)`.
"""
import json
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from substack_cli.app import automations_app
from substack_cli.auth import AuthError, resolve_cookies, resolve_publication_url
from substack_cli.client import SubstackApiError, SubstackClient, emit_error, output
from substack_cli.models import extract_list

_console = Console(stderr=True)

_UNSAFE_ID_CHARS = set("/\\?#")

# Known trigger types → short human description. Anything else is reported
# as-is (never an error) so a new Substack trigger type cannot crash a read.
_TRIGGER_LABELS = {
    "subscription": "when a reader subscribes",
    "before_renewal": "before a paid subscription renews",
    "reading_activity": "on reading activity",
}


def _validate_id(value: str, name: str) -> str:
    """Reject ids that could escape the URL path segment they are put in."""
    value = str(value)
    if not value or ".." in value or _UNSAFE_ID_CHARS.intersection(value):
        raise ValueError(f"Invalid {name} {value!r}: must be a plain id.")
    return value


# ---------------------------------------------------------------------------
# Core operations (pure functions — take a client, return API data). GET only.
# ---------------------------------------------------------------------------


def list_campaigns(client: SubstackClient) -> list:
    """GET /api/v1/drip-campaigns → the `campaigns` list."""
    return extract_list(client.get("/api/v1/drip-campaigns"), "campaigns")


def get_campaign(client: SubstackClient, campaign_id: str) -> dict:
    """GET /api/v1/drip-campaigns/custom/{campaign_id} → one campaign dict.

    Tolerates a `{"campaign": {...}}` envelope in case the shape changes.
    """
    _validate_id(campaign_id, "campaign_id")
    data = client.get(f"/api/v1/drip-campaigns/custom/{campaign_id}")
    if isinstance(data, dict) and isinstance(data.get("campaign"), dict):
        return data["campaign"]
    return data


def get_campaign_email(client: SubstackClient, campaign_id: str, email_id: str) -> Any:
    """GET /api/v1/drip-campaigns/{campaign_id}/email/{email_id}.

    Returns `{"drip_campaign_title": ..., "drip_campaign_body": <JSON string>}`.
    """
    _validate_id(campaign_id, "campaign_id")
    _validate_id(email_id, "email_id")
    return client.get(f"/api/v1/drip-campaigns/{campaign_id}/email/{email_id}")


def get_campaign_stats(client: SubstackClient, campaign_id: str) -> Any:
    """GET /api/v1/drip-campaigns/{campaign_id}/stats.

    `{"stats": null}` for a campaign that has never run; the shape for a
    running campaign has not been observed, so it is passed through as-is.
    """
    _validate_id(campaign_id, "campaign_id")
    return client.get(f"/api/v1/drip-campaigns/{campaign_id}/stats")


def get_audience_estimate(client: SubstackClient, campaign_id: str) -> Any:
    """GET /api/v1/drip-campaigns/{campaign_id}/audience-estimate.

    `{"estimatedAudienceSize": int|null, "windowDays": int|null,
    "hasSegment": bool, "updatedAt": iso}`.
    """
    _validate_id(campaign_id, "campaign_id")
    return client.get(f"/api/v1/drip-campaigns/{campaign_id}/audience-estimate")


def list_segments(client: SubstackClient) -> list:
    """GET /api/v1/subscriber-stats/saved-segments → the `segments` list."""
    return extract_list(client.get("/api/v1/subscriber-stats/saved-segments"), "segments")


def get_segment(client: SubstackClient, segment_id: str) -> Any:
    """GET /api/v1/subscriber-stats/saved-segments/{segment_id}.

    Live response: `{"segment": {...}, "count": int}`. Returned as-is; the
    meaning of `count` (presumably subscribers currently matching the
    segment) is not documented by Substack.
    """
    _validate_id(segment_id, "segment_id")
    return client.get(f"/api/v1/subscriber-stats/saved-segments/{segment_id}")


def list_subscriber_tags(client: SubstackClient) -> list:
    """GET /api/v1/subscriber-tags → the `tags` list."""
    return extract_list(client.get("/api/v1/subscriber-tags"), "tags")


# ---------------------------------------------------------------------------
# Pure helpers (no I/O)
# ---------------------------------------------------------------------------


def order_emails(campaign: dict) -> list:
    """Return the campaign's emails in send order, following the trigger chain.

    Each returned item is a dict with the email's own fields (minus the
    `body` s3 pointer) plus: `position` (1-based), `in_chain`, `trigger_id`,
    `after` ("trigger" | "previous_email" | None), `from_email_id`,
    `delay_hours`, `condition`, and `cumulative_delay_hours` (hours after the
    automation's trigger, or None when any delay on the path is unknown).
    """
    emails = [e for e in (campaign.get("emails") or []) if isinstance(e, dict)]
    triggers = [t for t in (campaign.get("triggers") or []) if isinstance(t, dict)]
    by_id = {e.get("id"): e for e in emails}
    trigger_to = {}
    for t in triggers:
        trigger_to.setdefault(t.get("to_email_id"), t)

    def sort_key(email_id: Any) -> tuple:
        e = by_id.get(email_id) or {}
        return (str(e.get("created_at") or ""), str(email_id))

    ordered_ids: list = []
    seen: set = set()
    # Roots: emails whose trigger has no from_email_id.
    frontier = sorted(
        (t.get("to_email_id") for t in triggers
         if not t.get("from_email_id") and t.get("to_email_id") in by_id),
        key=sort_key,
    )
    while frontier:
        current = frontier.pop(0)
        if current in seen:
            continue
        seen.add(current)
        ordered_ids.append(current)
        children = sorted(
            (t.get("to_email_id") for t in triggers
             if t.get("from_email_id") == current
             and t.get("to_email_id") in by_id
             and t.get("to_email_id") not in seen),
            key=sort_key,
        )
        frontier = children + frontier  # depth-first along the chain
    chained = set(ordered_ids)
    leftovers = sorted((i for i in by_id if i not in chained), key=sort_key)

    result: list = []
    cumulative: dict = {}
    for position, email_id in enumerate(ordered_ids + leftovers, start=1):
        email = by_id[email_id]
        trig = trigger_to.get(email_id) or {}
        from_id = trig.get("from_email_id")
        delay = trig.get("delay")
        in_chain = email_id in chained
        cum: Optional[float] = None
        if in_chain and isinstance(delay, (int, float)):
            if not from_id:
                cum = delay
            elif cumulative.get(from_id) is not None:
                cum = cumulative[from_id] + delay
        cumulative[email_id] = cum
        item = {k: v for k, v in email.items() if k != "body"}
        item.update({
            "position": position,
            "in_chain": in_chain,
            "trigger_id": trig.get("id"),
            "after": ("previous_email" if from_id else "trigger") if trig else None,
            "from_email_id": from_id,
            "delay_hours": delay,
            "cumulative_delay_hours": cum,
            "condition": trig.get("condition"),
        })
        result.append(item)
    return result


def describe_trigger(trigger_type: Any, trigger_config: Any) -> str:
    """One-line description of a campaign trigger; never raises."""
    label = _TRIGGER_LABELS.get(trigger_type)
    if label is None:
        label = f"unknown trigger type {trigger_type!r}" if trigger_type else "no trigger type"
    if isinstance(trigger_config, dict) and trigger_config:
        parts = ", ".join(f"{k}={v}" for k, v in trigger_config.items())
        return f"{label} ({parts})"
    if trigger_config not in (None, {}, ""):
        return f"{label} (config: {trigger_config!r})"
    return label


def summarize_campaign(
    campaign: dict,
    segments_by_id: Optional[dict] = None,
    tags_by_id: Optional[dict] = None,
) -> dict:
    """Project a raw campaign into a readable summary. Missing keys → None."""
    segments_by_id = segments_by_id or {}
    tags_by_id = tags_by_id or {}
    seg_id = campaign.get("saved_segment_id")
    tag_id = campaign.get("subscriber_tag_id")
    seg = segments_by_id.get(seg_id) if seg_id else None
    tag = tags_by_id.get(tag_id) if tag_id else None
    emails = order_emails(campaign)
    return {
        "id": campaign.get("id"),
        "title": campaign.get("title"),
        "state": campaign.get("state"),
        "running": campaign.get("state") == "started",
        "first_enabled_at": campaign.get("first_enabled_at"),
        "stopped_at": campaign.get("stopped_at"),
        "created_at": campaign.get("created_at"),
        "updated_at": campaign.get("updated_at"),
        "trigger": {
            "type": campaign.get("trigger_type"),
            "config": campaign.get("trigger_config"),
            "description": describe_trigger(
                campaign.get("trigger_type"), campaign.get("trigger_config")
            ),
        },
        "audience": {
            "subscription_type": campaign.get("condition_subscription_type"),
            "section_id": campaign.get("condition_section_id"),
            "saved_segment_id": seg_id,
            "saved_segment": (
                {k: seg.get(k) for k in ("id", "name", "description", "query")}
                if seg else None
            ),
            "subscriber_tag_id": tag_id,
            "subscriber_tag": (
                {k: tag.get(k) for k in ("id", "name", "slug")} if tag else None
            ),
        },
        "email_count": len(emails),
        "emails": [
            {k: e.get(k) for k in (
                "position", "id", "subject", "subtitle", "after", "delay_hours",
                "cumulative_delay_hours", "condition", "in_chain", "updated_at",
            )}
            for e in emails
        ],
    }


# -- ProseMirror → text ------------------------------------------------------

_BLOCK_TYPES = {
    "paragraph", "heading", "blockquote", "list_item", "bullet_list",
    "ordered_list", "captionedImage", "horizontal_rule", "button",
    "code_block", "doc",
}


def _inline_text(node: dict) -> str:
    text = node.get("text") or ""
    for mark in node.get("marks") or []:
        if isinstance(mark, dict) and mark.get("type") == "link":
            href = (mark.get("attrs") or {}).get("href")
            if href:
                return f"[{text}]({href})"
    return text


def _render(node: Any, list_prefix: str = "") -> str:
    """Render one ProseMirror node to text (blocks end with a blank line)."""
    if isinstance(node, list):
        return "".join(_render(n) for n in node)
    if not isinstance(node, dict):
        return ""
    ntype = node.get("type")
    attrs = node.get("attrs") or {}
    content = node.get("content") or []

    if ntype == "text":
        return _inline_text(node)
    if ntype == "hard_break":
        return "\n"
    if ntype == "horizontal_rule":
        return "---\n\n"
    if ntype == "image2" or ntype == "image":
        alt = attrs.get("alt") or attrs.get("title") or "image"
        src = attrs.get("src")
        return f"[image: {alt}]({src})" if src else f"[image: {alt}]"
    if ntype == "button":
        label = attrs.get("text") or "button"
        url = attrs.get("url")
        return (f"[{label}]({url})" if url else f"[{label}]") + "\n\n"
    if ntype == "heading":
        level = attrs.get("level") if isinstance(attrs.get("level"), int) else 1
        inner = "".join(_render(c) for c in content).strip()
        return f"{'#' * max(1, min(level, 6))} {inner}\n\n"
    if ntype == "paragraph":
        return "".join(_render(c) for c in content).strip() + "\n\n"
    if ntype in ("bullet_list", "ordered_list"):
        start = attrs.get("start") if isinstance(attrs.get("start"), int) else 1
        lines = []
        for i, item in enumerate(c for c in content if isinstance(c, dict)):
            marker = f"{start + i}." if ntype == "ordered_list" else "-"
            body = "".join(_render(c) for c in (item.get("content") or [])).strip()
            body = body.replace("\n\n", "\n").replace("\n", "\n   ")
            lines.append(f"{marker} {body}")
        return "\n".join(lines) + "\n\n"
    if ntype == "blockquote":
        inner = "".join(_render(c) for c in content).strip()
        return "\n".join(f"> {line}" if line else ">" for line in inner.split("\n")) + "\n\n"
    if ntype == "captionedImage":
        inner = "".join(_render(c) for c in content).strip()
        return (inner + "\n\n") if inner else ""
    # doc, list_item, code_block and unknown node types: render children.
    inner = "".join(_render(c) for c in content)
    if ntype in _BLOCK_TYPES and ntype != "doc" and inner and not inner.endswith("\n\n"):
        inner = inner.rstrip("\n") + "\n\n"
    return inner


def prosemirror_to_text(body: Any) -> str:
    """Render a ProseMirror document (dict, or JSON string) as plain text.

    Links are kept as `[text](url)`, headings as `#`, lists as `-`/`1.`,
    images as `[image: alt](src)`, buttons as `[label](url)`. A string that
    is not JSON is returned unchanged; None returns "".
    """
    if body is None:
        return ""
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            return body
    return _render(body).strip() + "\n" if body else ""


def email_text_payload(raw: Any, campaign_id: str, email_id: str) -> dict:
    """Turn the per-email API response into `{subject, text, word_count, ...}`."""
    raw = raw if isinstance(raw, dict) else {}
    text = prosemirror_to_text(raw.get("drip_campaign_body"))
    return {
        "campaign_id": campaign_id,
        "email_id": email_id,
        "subject": raw.get("drip_campaign_title"),
        "word_count": len(text.split()),
        "text": text,
    }


def _soft(fn, *args) -> Any:
    """Call a read and return its result, or an inline error object.

    Used for secondary lookups (stats, estimate, segments, tags) so one
    failing endpoint does not hide the rest of a campaign's details.
    """
    try:
        return fn(*args)
    except (SubstackApiError, ValueError) as exc:
        return {"error": True, "message": str(exc),
                "status_code": getattr(exc, "status_code", None)}


def _index_by_id(items: Any) -> dict:
    if not isinstance(items, list):
        return {}
    return {i.get("id"): i for i in items if isinstance(i, dict) and i.get("id") is not None}


def _lookup_tables(client: SubstackClient, campaigns: list) -> tuple:
    """Fetch segments/tags only if some campaign references them."""
    segments: Any = {}
    tags: Any = {}
    if any(c.get("saved_segment_id") for c in campaigns):
        segments = _index_by_id(_soft(list_segments, client))
    if any(c.get("subscriber_tag_id") for c in campaigns):
        tags = _index_by_id(_soft(list_subscriber_tags, client))
    return segments, tags


def _stats_payload(raw: Any) -> dict:
    if isinstance(raw, dict) and raw.get("error") is True:
        return raw
    stats = raw.get("stats") if isinstance(raw, dict) and "stats" in raw else raw
    return {"available": stats is not None, "stats": stats}


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def _make_client() -> SubstackClient:
    """Resolve auth + publication URL. All endpoints here are host "P"."""
    return SubstackClient(cookies=resolve_cookies(), publication_url=resolve_publication_url())


def _fail(exc: Exception, pretty: bool) -> None:
    emit_error(str(exc), status_code=getattr(exc, "status_code", None), pretty=pretty)


def _print_campaign_table(summaries: list) -> None:
    table = Table(title="Automations")
    for col in ("title", "state", "trigger", "audience", "emails"):
        table.add_column(col, overflow="fold")
    for s in summaries:
        aud = s["audience"]
        who = aud.get("subscription_type") or "-"
        if aud.get("saved_segment"):
            who += f" · segment: {aud['saved_segment'].get('name')}"
        elif aud.get("saved_segment_id"):
            who += f" · segment {aud['saved_segment_id']}"
        if aud.get("subscriber_tag"):
            who += f" · tag: {aud['subscriber_tag'].get('name')}"
        elif aud.get("subscriber_tag_id"):
            who += f" · tag {aud['subscriber_tag_id']}"
        emails = "\n".join(
            f"{e['position']}. {e.get('subject')!s} (+{e.get('delay_hours')}h)"
            for e in s["emails"]
        )
        table.add_row(str(s.get("title")), str(s.get("state")),
                      s["trigger"]["description"], who, emails or "-")
    _console.print(table)


@automations_app.command("list")
def automations_list_cmd(
    details: bool = typer.Option(
        False, "--details",
        help="Also fetch each campaign's audience estimate and stats (2 extra GETs per campaign).",
    ),
    raw: bool = typer.Option(False, "--raw", help="Print the API's campaign objects unmodified."),
    pretty: bool = False,
):
    """List automations (email sequences): state, trigger, audience, and emails in send order.

    READ-ONLY. GET /api/v1/drip-campaigns, plus saved segments / subscriber
    tags when a campaign references one (to show the segment name + query).
    """
    try:
        client = _make_client()
        campaigns = list_campaigns(client)
        if raw:
            output(campaigns, pretty=pretty)
            return
        segments, tags = _lookup_tables(client, campaigns)
        summaries = [summarize_campaign(c, segments, tags) for c in campaigns
                     if isinstance(c, dict)]
        if details:
            for s in summaries:
                if s.get("id"):
                    s["audience_estimate"] = _soft(get_audience_estimate, client, s["id"])
                    s["stats"] = _stats_payload(_soft(get_campaign_stats, client, s["id"]))
        if pretty:
            _print_campaign_table(summaries)
        else:
            output(summaries, pretty=False)
    except (SubstackApiError, AuthError, ValueError) as exc:
        _fail(exc, pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@automations_app.command("get")
def automations_get_cmd(
    campaign_id: str = typer.Argument(..., help="Automation (drip campaign) id, from `automations list`."),
    bodies: bool = typer.Option(
        False, "--bodies", help="Also fetch each email's text (one extra GET per email)."
    ),
    raw: bool = typer.Option(False, "--raw", help="Print the API's campaign object unmodified."),
    pretty: bool = False,
):
    """Show one automation in full: trigger, audience, ordered emails, estimate, stats.

    Audience includes the saved segment's filter query and the subscriber
    tag when set; emails are in send order with per-step and cumulative delays.

    READ-ONLY. GET /api/v1/drip-campaigns/custom/{id}, …/audience-estimate,
    …/stats, and (with --bodies) …/email/{email_id}. Stats are null until the
    automation has run.
    """
    try:
        client = _make_client()
        campaign = get_campaign(client, campaign_id)
        if raw:
            output(campaign, pretty=pretty)
            return
        if not isinstance(campaign, dict):
            raise ValueError(f"Unrecognized campaign response type: {type(campaign).__name__}")
        segments, tags = _lookup_tables(client, [campaign])
        summary = summarize_campaign(campaign, segments, tags)
        summary["audience_estimate"] = _soft(get_audience_estimate, client, campaign_id)
        summary["stats"] = _stats_payload(_soft(get_campaign_stats, client, campaign_id))
        if bodies:
            for email in summary["emails"]:
                if not email.get("id"):
                    continue
                got = _soft(get_campaign_email, client, campaign_id, email["id"])
                if isinstance(got, dict) and got.get("error") is True:
                    email["text_error"] = got
                else:
                    payload = email_text_payload(got, campaign_id, email["id"])
                    email["text"] = payload["text"]
                    email["word_count"] = payload["word_count"]
        output(summary, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        _fail(exc, pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@automations_app.command("email")
def automations_email_cmd(
    campaign_id: str = typer.Argument(..., help="Automation (drip campaign) id."),
    email_id: str = typer.Argument(..., help="Email id, from `automations get`."),
    fmt: str = typer.Option(
        "json", "--format",
        help="json: {subject, text, word_count} (default). text: plain text only. "
        "raw: the API response unmodified (body is a ProseMirror JSON string).",
    ),
    pretty: bool = False,
):
    """Show one automation email's subject and text.

    READ-ONLY. GET /api/v1/drip-campaigns/{campaign_id}/email/{email_id} — the
    only endpoint that returns an automation email's body. Text output keeps
    links as [text](url).
    """
    try:
        if fmt not in ("json", "text", "raw"):
            raise ValueError(f"--format must be json, text or raw, not {fmt!r}.")
        client = _make_client()
        got = get_campaign_email(client, campaign_id, email_id)
        if fmt == "raw":
            output(got, pretty=pretty)
            return
        payload = email_text_payload(got, campaign_id, email_id)
        if fmt == "text":
            subject = payload.get("subject")
            print((f"Subject: {subject}\n\n" if subject else "") + payload["text"], end="")
            return
        output(payload, pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        _fail(exc, pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@automations_app.command("stats")
def automations_stats_cmd(
    campaign_id: str = typer.Argument(..., help="Automation (drip campaign) id."),
    pretty: bool = False,
):
    """Show an automation's send stats ({"available": false, "stats": null} until it has run).

    READ-ONLY. GET /api/v1/drip-campaigns/{campaign_id}/stats. The stats shape
    for a running automation has not been observed; it is passed through as-is.
    """
    try:
        client = _make_client()
        output(_stats_payload(get_campaign_stats(client, campaign_id)), pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        _fail(exc, pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@automations_app.command("audience")
def automations_audience_cmd(
    campaign_id: str = typer.Argument(..., help="Automation (drip campaign) id."),
    pretty: bool = False,
):
    """Show Substack's audience-size estimate for an automation.

    READ-ONLY. GET /api/v1/drip-campaigns/{campaign_id}/audience-estimate →
    {estimatedAudienceSize, windowDays, hasSegment, updatedAt}.
    """
    try:
        client = _make_client()
        output(get_audience_estimate(client, campaign_id), pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        _fail(exc, pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@automations_app.command("segments")
def automations_segments_cmd(pretty: bool = False):
    """List saved subscriber segments (id, name, description, filter query).

    READ-ONLY. GET /api/v1/subscriber-stats/saved-segments.
    """
    try:
        client = _make_client()
        output(list_segments(client), pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        _fail(exc, pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@automations_app.command("segment")
def automations_segment_cmd(
    segment_id: str = typer.Argument(..., help="Saved segment id, from `automations segments`."),
    pretty: bool = False,
):
    """Show one saved subscriber segment and its count.

    READ-ONLY. GET /api/v1/subscriber-stats/saved-segments/{segment_id} →
    {"segment": {...}, "count": int}, passed through unchanged.
    """
    try:
        client = _make_client()
        output(get_segment(client, segment_id), pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        _fail(exc, pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)


@automations_app.command("subscriber-tags")
def automations_subscriber_tags_cmd(pretty: bool = False):
    """List subscriber tags (not post tags — see `tags list` for those).

    READ-ONLY. GET /api/v1/subscriber-tags.
    """
    try:
        client = _make_client()
        output(list_subscriber_tags(client), pretty=pretty)
    except (SubstackApiError, AuthError, ValueError) as exc:
        _fail(exc, pretty)
    except Exception as exc:
        emit_error(f"Unexpected error: {exc}", pretty=pretty)
