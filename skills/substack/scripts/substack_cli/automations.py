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
from rich.text import Text

from substack_cli.app import automations_app
from substack_cli.auth import AuthError, resolve_cookies, resolve_publication_url
from substack_cli.client import SubstackApiError, SubstackClient, emit_error, output
from substack_cli.models import extract_list

_console = Console(stderr=True)

import re as _re

# Allowlist for id values put into URL path segments.
_ID_RE = _re.compile(r"[A-Za-z0-9_-]+")

# Known trigger types → short human description. Anything else is reported
# as-is (never an error) so a new Substack trigger type cannot crash a read.
_TRIGGER_LABELS = {
    "subscription": "when a reader subscribes",
    "before_renewal": "before a paid subscription renews",
    "reading_activity": "on reading activity",
}


def _validate_id(value: str, name: str) -> str:
    """Allowlist ids put into URL path segments (alphanumeric, hyphen, underscore)."""
    value = str(value)
    if not _ID_RE.fullmatch(value):
        raise ValueError(f"Invalid {name} {value!r}: must be a plain id (letters, digits, hyphens, underscores only).")
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

    def edge_key(edge: tuple) -> tuple:
        t = edge[1]
        return (
            sort_key(edge[0])
            + (str(t.get("id") or ""),)
            + (str(t.get("from_email_id") or ""),)
            + (str(t.get("delay") if t.get("delay") is not None else ""),)
        )

    ordered_ids: list = []
    seen: set = set()
    # The trigger the walk actually followed to reach each chained email, so
    # timing comes from that edge, not from whichever trigger is listed first.
    reached_by: dict = {}
    # Roots: emails whose trigger has no from_email_id. Frontier items are
    # (email_id, trigger) pairs.
    frontier = sorted(
        ((t.get("to_email_id"), t) for t in triggers
         if not t.get("from_email_id") and t.get("to_email_id") in by_id),
        key=edge_key,
    )
    while frontier:
        current, via = frontier.pop(0)
        if current in seen:
            continue
        seen.add(current)
        reached_by[current] = via
        ordered_ids.append(current)
        children = sorted(
            ((t.get("to_email_id"), t) for t in triggers
             if t.get("from_email_id") == current
             and t.get("to_email_id") in by_id
             and t.get("to_email_id") not in seen),
            key=edge_key,
        )
        frontier = children + frontier  # depth-first along the chain
    chained = set(ordered_ids)
    leftovers = sorted((i for i in by_id if i not in chained), key=sort_key)

    result: list = []
    cumulative: dict = {}
    for position, email_id in enumerate(ordered_ids + leftovers, start=1):
        email = by_id[email_id]
        trig = reached_by.get(email_id) or trigger_to.get(email_id) or {}
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
    """Project a raw campaign into a readable summary. Missing keys → None.

    `segments_by_id` / `tags_by_id` may instead be an inline error object
    (the lookup failed); a campaign that references a segment / tag then
    gets that error object as `saved_segment` / `subscriber_tag`.
    """
    segments_by_id = segments_by_id or {}
    tags_by_id = tags_by_id or {}
    seg_id = campaign.get("saved_segment_id")
    tag_id = campaign.get("subscriber_tag_id")
    seg_failed = bool(seg_id) and _is_error(segments_by_id)
    tag_failed = bool(tag_id) and _is_error(tags_by_id)
    seg = segments_by_id.get(seg_id) if seg_id and not seg_failed else None
    tag = tags_by_id.get(tag_id) if tag_id and not tag_failed else None
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
                dict(segments_by_id) if seg_failed
                else {k: seg.get(k) for k in ("id", "name", "description", "query")}
                if seg else None
            ),
            "subscriber_tag_id": tag_id,
            "subscriber_tag": (
                dict(tags_by_id) if tag_failed
                else {k: tag.get(k) for k in ("id", "name", "slug")} if tag else None
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
    """Turn the per-email API response into `{subject, text, word_count, ...}`.

    Raises ValueError when the response is not a dict or carries neither
    drip_campaign_body nor drip_campaign_title, to avoid silently returning
    an empty payload for an unrecognized response shape.
    """
    if not isinstance(raw, dict) or (
        "drip_campaign_body" not in raw and "drip_campaign_title" not in raw
    ):
        raise ValueError(
            f"Unrecognized email response for {campaign_id}/{email_id}: {type(raw).__name__!r}"
        )
    text = prosemirror_to_text(raw.get("drip_campaign_body"))
    return {
        "campaign_id": campaign_id,
        "email_id": email_id,
        "subject": raw.get("drip_campaign_title"),
        "word_count": len(text.split()),
        "text": text,
    }


# Sentinel key added to every _soft error object so _is_error can test
# object identity rather than the value of "error" (which could collide with
# a real response field).  The key is stripped before JSON output.
_SOFT_ERROR_SENTINEL = "__soft_error__"


def _soft(fn, *args) -> Any:
    """Call a read and return its result, or an inline error object.

    Used for secondary lookups (stats, estimate, segments, tags) so one
    failing endpoint does not hide the rest of a campaign's details.
    The returned error object carries a private sentinel key so _is_error
    can distinguish it from a real response dict that happens to have
    ``"error": true``.
    """
    try:
        return fn(*args)
    except (SubstackApiError, ValueError) as exc:
        return {
            _SOFT_ERROR_SENTINEL: True,
            "error": True,
            "message": str(exc),
            "status_code": getattr(exc, "status_code", None),
        }


def _is_error(value: Any) -> bool:
    """True for an inline error object as returned by `_soft`."""
    return isinstance(value, dict) and value.get(_SOFT_ERROR_SENTINEL) is True


def _strip_sentinels(value: Any) -> Any:
    """Recursively remove the private sentinel key from all nested dicts.

    Call this on the full data structure just before JSON serialisation so
    the sentinel never appears in command output.  The in-memory dicts
    retain the sentinel so _is_error keeps working through the pipeline.
    """
    if isinstance(value, dict):
        return {k: _strip_sentinels(v) for k, v in value.items()
                if k != _SOFT_ERROR_SENTINEL}
    if isinstance(value, list):
        return [_strip_sentinels(i) for i in value]
    return value


def _index_by_id(items: Any) -> dict:
    if not isinstance(items, list):
        return {}
    return {i.get("id"): i for i in items if isinstance(i, dict) and i.get("id") is not None}


def _lookup_tables(client: SubstackClient, campaigns: list) -> tuple:
    """Fetch segments/tags only if some campaign references them.

    Each table is an id → item index, or the inline error object when that
    lookup failed (so the failure is reported, not hidden as "not found").
    Non-dict campaign items are ignored.
    """
    campaigns = [c for c in campaigns if isinstance(c, dict)]
    segments: Any = {}
    tags: Any = {}
    if any(c.get("saved_segment_id") for c in campaigns):
        got = _soft(list_segments, client)
        segments = got if _is_error(got) else _index_by_id(got)
    if any(c.get("subscriber_tag_id") for c in campaigns):
        got = _soft(list_subscriber_tags, client)
        tags = got if _is_error(got) else _index_by_id(got)
    return segments, tags


def _stats_payload(raw: Any) -> dict:
    if _is_error(raw):
        return raw  # sentinel stripped at output time
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


def _failed_cell(err: dict) -> str:
    status = err.get("status_code")
    if status:
        return f"lookup failed (HTTP {status})"
    msg = err.get("message") or ""
    suffix = f": {msg[:40]}" if msg else ""
    return f"lookup failed{suffix}"


def _estimate_cell(est: Any) -> Text:
    """Short, markup-safe table cell for an audience-estimate payload."""
    if _is_error(est):
        return Text(_failed_cell(est))
    if not isinstance(est, dict):
        return Text("-")
    size = est.get("estimatedAudienceSize")
    cell = f"{size:,}" if isinstance(size, int) else "unknown"
    if est.get("windowDays") is not None:
        cell += f" ({est['windowDays']}d window)"
    return Text(cell)


def _stats_cell(stats: Any) -> Text:
    """Short, markup-safe table cell for a `_stats_payload` result."""
    if _is_error(stats):
        return Text(_failed_cell(stats))
    if not isinstance(stats, dict):
        return Text("-")
    if not stats.get("available"):
        return Text("not run yet")
    value = stats.get("stats")
    if isinstance(value, dict):
        cell = ", ".join(f"{k}={v}" for k, v in value.items())
    else:
        cell = json.dumps(value, ensure_ascii=False)
    return Text(cell if len(cell) <= 80 else cell[:77] + "...")


def _print_campaign_table(summaries: list, details: bool = False) -> None:
    table = Table(title="Automations")
    columns = ["title", "state", "trigger", "audience", "emails"]
    if details:
        columns += ["estimate", "stats"]
    for col in columns:
        table.add_column(col, overflow="fold")
    for s in summaries:
        aud = s["audience"]
        who = aud.get("subscription_type") or "-"
        if _is_error(aud.get("saved_segment")):
            who += (f" · segment {aud['saved_segment_id']}"
                    f" ({_failed_cell(aud['saved_segment'])})")
        elif aud.get("saved_segment"):
            who += f" · segment: {aud['saved_segment'].get('name')}"
        elif aud.get("saved_segment_id"):
            who += f" · segment {aud['saved_segment_id']}"
        if _is_error(aud.get("subscriber_tag")):
            who += (f" · tag {aud['subscriber_tag_id']}"
                    f" ({_failed_cell(aud['subscriber_tag'])})")
        elif aud.get("subscriber_tag"):
            who += f" · tag: {aud['subscriber_tag'].get('name')}"
        elif aud.get("subscriber_tag_id"):
            who += f" · tag {aud['subscriber_tag_id']}"
        def _email_line(e: dict) -> str:
            dh = e.get("delay_hours")
            delay = f"+{dh}h" if dh is not None else "not in chain"
            return f"{e['position']}. {e.get('subject')!s} ({delay})"

        emails = "\n".join(_email_line(e) for e in s["emails"])
        row = [Text(str(s.get("title"))), Text(str(s.get("state"))),
               Text(s["trigger"]["description"]), Text(who), Text(emails or "-")]
        if details:
            row += [_estimate_cell(s.get("audience_estimate")),
                    _stats_cell(s.get("stats"))]
        table.add_row(*row)
    _console.print(table)


@automations_app.command("list")
def automations_list_cmd(
    details: bool = typer.Option(
        False, "--details",
        help="Also fetch each campaign's audience estimate and stats (2 extra GETs per campaign).",
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit",
        help="Maximum number of automations to show (applied after sorting). "
             "Useful with --details to bound the 2 extra GETs per campaign.",
    ),
    raw: bool = typer.Option(False, "--raw", help="Print the API's campaign objects unmodified."),
    pretty: bool = False,
):
    """List automations (email sequences): state, trigger, audience, and emails in send order.

    READ-ONLY. GET /api/v1/drip-campaigns, plus saved segments / subscriber
    tags when a campaign references one (to show the segment name + query).
    With --details, 2 extra GETs are made per automation shown.
    """
    try:
        client = _make_client()
        campaigns = list_campaigns(client)
        if raw:
            output(campaigns[:limit] if limit is not None else campaigns, pretty=pretty)
            return
        segments, tags = _lookup_tables(client, campaigns)
        summaries = [summarize_campaign(c, segments, tags) for c in campaigns
                     if isinstance(c, dict)]
        if limit is not None:
            summaries = summaries[:limit]
        if details:
            total = len(summaries)
            for i, s in enumerate(summaries, start=1):
                if s.get("id"):
                    if pretty:
                        _console.print(f"[dim]Fetching details {i}/{total}…[/dim]")
                    s["audience_estimate"] = _soft(get_audience_estimate, client, s["id"])
                    s["stats"] = _stats_payload(_soft(get_campaign_stats, client, s["id"]))
        if pretty:
            _print_campaign_table(summaries, details=details)
        else:
            output(_strip_sentinels(summaries), pretty=False)
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
                if _is_error(got):
                    email["text_error"] = got
                else:
                    payload = email_text_payload(got, campaign_id, email["id"])
                    email["text"] = payload["text"]
                    email["word_count"] = payload["word_count"]
        output(_strip_sentinels(summary), pretty=pretty)
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
        payload = email_text_payload(got, campaign_id, email_id)  # raises on bad shape
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
