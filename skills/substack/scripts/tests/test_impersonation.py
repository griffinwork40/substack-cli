"""Unit tests for impersonation detection rules.

Covers:
  POSITIVE cases — must be flagged (name_reasons non-empty):
    - 'Charlie Garcia' (non-real user_id) → charlie_garcia_name
    - 'Charlie P. Garcia' → charlie_garcia_name
    - 'charliepgarcia' handle-style → charlie_garcia_name
    - Mathematical bold 𝐂𝐡𝐚𝐫𝐥𝐢𝐞 Garcia → charlie_confusable_name
    - Cyrillic Ϲharlie → charlie_confusable_name
    - Fullwidth Ｃｈａｒｌｉｅ → charlie_confusable_name
    - Dotted C.h.a.r.l.i.e Garcia → charlie_garcia_name (punct stripped)
    - 'Charlie WhatsApp +1 672 906 6441' → charlie_with_lure_name + contacts
    - WhatsApp keyword in name → whatsapp_contact
    - Telegram handle in name → telegram_contact
    - Phone number in name → phone_number
    - t.me/ link in name → telegram_contact

  NEGATIVE (name) — must NOT produce name_reasons:
    - 'Charlie Smith' (plain ASCII, no lure, no Garcia) → no match
    - 'Charlie Brown' → no match
    - 'Charlotte Garcia' → no match (normalize: charlottegarcia, no charliegarcia)
    - 'Charles Garcia' → no match (normalize: charlesgarcia, not charliegarcia)
    - 'Charlie' alone → no match
    - Ordinary reader 'Sarah Johnson' → no match
    - 'Marchello' (contains 'arch' but not 'charlie') → no match
    - Real Charlie (user_id 27965159) 'Charlie Garcia' → allowlisted, no name_reason

  BODY — body-only contact lures → body_reasons only (uncertain, never candidate):
    - 'WhatsApp me at +1 672 906 6441' → body_contact_lure
    - 'Telegram me (@CharlieGarcia1)' → body_contact_lure
    - 'wa.me/16729066441' → body_contact_lure
    - t.me/ link → body_contact_lure

  BODY — innocent mentions must NOT match:
    - "I got a WhatsApp from a fake Charlie" → no body lure
    - "Sorry you have to deal with these Telegram scammers" → no body lure
    - "they impersonate via WhatsApp" → no body lure

  RECORD_CANDIDATE routing:
    - name_reasons only → candidate
    - body_reasons only → uncertain (body_only_review), never candidate, cap not consumed
    - both → candidate (name-driven)
    - daily cap shunts name-triggered to uncertain/cap_exceeded
    - dedupe skips already-acted user
"""
import os
import pytest
import unittest.mock as mock

from substack_cli.impersonation_detect import (
    ALLOWLISTED_USER_IDS,
    REAL_CHARLIE_USER_ID,
    _detect,
    _normalize_for_detection,
    _has_non_ascii_confusable,
    _body_has_contact_lure,
)
from substack_cli.impersonation_state import (
    _walk_comments,
    _record_candidate,
    _load_seen,
)


# ---------------------------------------------------------------------------
# _normalize_for_detection
# ---------------------------------------------------------------------------

class TestNormalizeForDetection:
    def test_plain_ascii(self):
        assert _normalize_for_detection("Charlie") == "charlie"

    def test_mathematical_bold(self):
        # 𝐂𝐡𝐚𝐫𝐥𝐢𝐞 — U+1D402 U+1D421 U+1D41A U+1D42B U+1D425 U+1D422 U+1D41E
        bold = "\U0001D402\U0001D421\U0001D41A\U0001D42B\U0001D425\U0001D422\U0001D41E"
        assert _normalize_for_detection(bold) == "charlie"

    def test_cyrillic_c(self):
        assert _normalize_for_detection("\u0421harlie") == "charlie"

    def test_dots_between_letters(self):
        assert _normalize_for_detection("C.h.a.r.l.i.e") == "charlie"

    def test_mixed_case(self):
        assert _normalize_for_detection("CHARLIE") == "charlie"

    def test_nfkd_diacritics(self):
        # Ċ (U+010A) — C with dot above — NFKD strips the combining dot
        assert _normalize_for_detection("\u010Aharlie") == "charlie"

    def test_fullwidth(self):
        fw = "\uFF23\uFF48\uFF41\uFF52\uFF4C\uFF49\uFF45"
        assert _normalize_for_detection(fw) == "charlie"

    def test_charlie_garcia_strips_space(self):
        assert _normalize_for_detection("Charlie Garcia") == "charliegarcia"

    def test_charles_garcia_not_charliegarcia(self):
        assert _normalize_for_detection("Charles Garcia") == "charlesgarcia"

    def test_charlotte_garcia_not_charliegarcia(self):
        assert _normalize_for_detection("Charlotte Garcia") == "charlottegarcia"


# ---------------------------------------------------------------------------
# _has_non_ascii_confusable
# ---------------------------------------------------------------------------

class TestHasNonAsciiConfusable:
    def test_plain_ascii_false(self):
        assert not _has_non_ascii_confusable("Charlie Garcia")

    def test_cyrillic_c_true(self):
        assert _has_non_ascii_confusable("\u0421harlie")

    def test_math_bold_true(self):
        bold = "\U0001D402\U0001D421\U0001D41A\U0001D42B\U0001D425\U0001D422\U0001D41E"
        assert _has_non_ascii_confusable(bold)

    def test_fullwidth_true(self):
        fw = "\uFF23\uFF48\uFF41\uFF52\uFF4C\uFF49\uFF45"
        assert _has_non_ascii_confusable(fw)


# ---------------------------------------------------------------------------
# _body_has_contact_lure
# ---------------------------------------------------------------------------

class TestBodyHasContactLure:
    # Positive — direct invitations
    def test_whatsapp_me_phone(self):
        assert _body_has_contact_lure("WhatsApp me at +1 672 906 6441")

    def test_telegram_me_handle(self):
        assert _body_has_contact_lure("Telegram me (@CharlieGarcia1)")

    def test_message_me_on_whatsapp(self):
        assert _body_has_contact_lure("message me on WhatsApp for details")

    def test_wame_link(self):
        assert _body_has_contact_lure("Join via wa.me/16729066441")

    def test_tme_link(self):
        assert _body_has_contact_lure("reach out at t.me/charliegarcia_official")

    def test_contact_me_telegram(self):
        assert _body_has_contact_lure("contact me on Telegram @CharlieG123")

    def test_whatsapp_with_phone(self):
        assert _body_has_contact_lure("WhatsApp +16729066441 for investment tips")

    # Negative — innocent mentions
    def test_victim_reporting_whatsapp(self):
        assert not _body_has_contact_lure(
            "I got a WhatsApp from a fake Charlie account yesterday"
        )

    def test_discussing_telegram_scam(self):
        assert not _body_has_contact_lure(
            "Sorry you have to deal with these Telegram scammers"
        )

    def test_warning_about_whatsapp(self):
        assert not _body_has_contact_lure(
            "They impersonate Charlie via WhatsApp — watch out everyone"
        )

    def test_no_platform_keyword(self):
        assert not _body_has_contact_lure("Call me at +16729066441")

    def test_empty_body(self):
        assert not _body_has_contact_lure("")


# ---------------------------------------------------------------------------
# _detect — POSITIVE name cases (name_reasons non-empty)
# ---------------------------------------------------------------------------

class TestDetectPositiveName:
    def test_charlie_garcia_non_real(self):
        """'Charlie Garcia' with non-real user_id → charlie_garcia_name"""
        name_r, body_r = _detect("Charlie Garcia", "", user_id=99999)
        assert "charlie_garcia_name" in name_r

    def test_charlie_p_garcia(self):
        """'Charlie P. Garcia' → charlie_garcia_name (charliepgarcia after norm)"""
        name_r, _ = _detect("Charlie P. Garcia", "", user_id=99999)
        assert "charlie_garcia_name" in name_r

    def test_charliepgarcia_handle_style(self):
        name_r, _ = _detect("charliepgarcia", "", user_id=99999)
        assert "charlie_garcia_name" in name_r

    def test_dotted_charlie_garcia(self):
        """C.h.a.r.l.i.e Garcia → charlie_garcia_name"""
        name_r, _ = _detect("C.h.a.r.l.i.e Garcia", "", user_id=99999)
        assert "charlie_garcia_name" in name_r

    def test_unicode_bold_charlie_garcia(self):
        """Math-bold 𝐂𝐡𝐚𝐫𝐥𝐢𝐞 Garcia → both confusable + garcia rules"""
        bold_charlie = (
            "\U0001D402\U0001D421\U0001D41A\U0001D42B\U0001D425\U0001D422\U0001D41E"
            " Garcia"
        )
        name_r, _ = _detect(bold_charlie, "", user_id=99999)
        assert "charlie_confusable_name" in name_r

    def test_cyrillic_charlie_confusable(self):
        """\u0421harlie (Cyrillic С) → charlie_confusable_name"""
        name_r, _ = _detect("\u0421harlie Garcia", "", user_id=99999)
        assert "charlie_confusable_name" in name_r

    def test_fullwidth_charlie_confusable(self):
        fw = "\uFF23\uFF48\uFF41\uFF52\uFF4C\uFF49\uFF45 Garcia"
        name_r, _ = _detect(fw, "", user_id=99999)
        assert "charlie_confusable_name" in name_r

    def test_charlie_whatsapp_phone_in_name(self):
        """'Charlie WhatsApp +1 672 906 6441' → charlie_with_lure_name + contacts"""
        name_r, _ = _detect("Charlie WhatsApp +1 672 906 6441", "", user_id=99999)
        assert "whatsapp_contact" in name_r
        assert "phone_number" in name_r
        assert "charlie_with_lure_name" in name_r

    def test_whatsapp_keyword_in_name(self):
        name_r, _ = _detect("Charlie WhatsApp Investor", "", user_id=99999)
        assert "whatsapp_contact" in name_r

    def test_telegram_handle_with_tme_link_in_name(self):
        """@handle + t.me/ link → telegram_contact flagged (Telegram context present)"""
        name_r, _ = _detect("Charlie Garcia t.me/charlie @CharlieOfficial", "", user_id=99999)
        assert "telegram_contact" in name_r

    def test_telegram_handle_alone_in_name_not_flagged(self):
        """@handle alone (no t.me/ or 'telegram' keyword) → telegram_contact NOT flagged"""
        name_r, _ = _detect("Charlie Garcia @CharlieOfficial_tg", "", user_id=99999)
        assert "telegram_contact" not in name_r

    def test_phone_in_name(self):
        name_r, _ = _detect("Charlie +16729066441", "", user_id=99999)
        assert "phone_number" in name_r

    def test_tme_link_in_name(self):
        name_r, _ = _detect("Some Guy t.me/charliegarcia_official", "", user_id=99999)
        assert "telegram_contact" in name_r

    def test_whatsapp_api_url_in_name(self):
        name_r, _ = _detect("Some Guy api.whatsapp.com/send", "", user_id=99999)
        assert "whatsapp_contact" in name_r


# ---------------------------------------------------------------------------
# _detect — NEGATIVE name cases (name_reasons must be empty)
# ---------------------------------------------------------------------------

class TestDetectNegativeName:
    def test_charlie_smith_no_match(self):
        """'Charlie Smith' — plain ASCII, no lure, no Garcia → no name match"""
        name_r, _ = _detect("Charlie Smith", "", user_id=99999)
        assert "charlie_garcia_name" not in name_r
        assert "charlie_confusable_name" not in name_r
        assert "charlie_with_lure_name" not in name_r

    def test_charlie_brown_no_match(self):
        name_r, _ = _detect("Charlie Brown", "", user_id=99999)
        assert not any(r in name_r for r in [
            "charlie_garcia_name", "charlie_confusable_name", "charlie_with_lure_name"
        ])

    def test_charlotte_garcia_no_match(self):
        """'Charlotte Garcia' normalizes to 'charlottegarcia' — not charliegarcia"""
        name_r, _ = _detect("Charlotte Garcia", "", user_id=99999)
        assert "charlie_garcia_name" not in name_r
        assert "charlie_confusable_name" not in name_r

    def test_charles_garcia_no_match(self):
        """'Charles Garcia' normalizes to 'charlesgarcia' — not charliegarcia.
        Charles Garcia alone is NOT auto-reported; at most flagged for human
        review if a body lure appears, but the name alone is not a match."""
        name_r, _ = _detect("Charles Garcia", "", user_id=99999)
        assert "charlie_garcia_name" not in name_r
        assert "charlie_confusable_name" not in name_r

    def test_charlie_alone_no_match(self):
        """Plain 'Charlie' alone — innocent first name, no match."""
        name_r, _ = _detect("Charlie", "", user_id=99999)
        assert not name_r

    def test_ordinary_reader_no_match(self):
        name_r, body_r = _detect("Sarah Johnson", "Looking forward to the next issue.", user_id=99999)
        assert name_r == []
        assert body_r == []

    def test_arch_in_name_not_charlie(self):
        name_r, _ = _detect("Marchello", "", user_id=99999)
        assert name_r == []

    def test_real_charlie_user_id_no_garcia_rule(self):
        """Real Charlie (user_id 27965159) with name 'Charlie Garcia' → no charlie_garcia_name
        (Rule A explicitly skips when user_id == REAL_CHARLIE_USER_ID)."""
        name_r, _ = _detect("Charlie Garcia", "", user_id=REAL_CHARLIE_USER_ID)
        assert "charlie_garcia_name" not in name_r

    def test_body_alone_no_name_reasons(self):
        """WhatsApp in body with no name lure → name_reasons empty"""
        name_r, _ = _detect("Ellen", "I've gotten countless WhatsApp messages from fake Charlie accounts")
        assert name_r == []

    def test_phone_only_in_body_no_name_reason(self):
        name_r, _ = _detect("Random Name", "call me at +16729066441 for details")
        assert "phone_number" not in name_r

    def test_tme_link_in_body_no_name_reason(self):
        name_r, _ = _detect("Some Guy", "Join at t.me/charliegarcia_official")
        assert "telegram_contact" not in name_r

    def test_whatsapp_api_url_in_body_no_name_reason(self):
        name_r, _ = _detect("Some Guy", "api.whatsapp.com/send?phone=16729066441")
        assert "whatsapp_contact" not in name_r


# ---------------------------------------------------------------------------
# _detect — BODY positive cases (body_reasons non-empty, name_reasons empty)
# ---------------------------------------------------------------------------

class TestDetectBodyPositive:
    def test_whatsapp_me_body(self):
        """'WhatsApp me at +1 672 906 6441' in body → body_contact_lure"""
        name_r, body_r = _detect("Ellen", "WhatsApp me at +1 672 906 6441")
        assert name_r == []
        assert "body_contact_lure" in body_r

    def test_telegram_me_handle_body(self):
        name_r, body_r = _detect("Tom H", "Telegram me (@CharlieGarcia1)")
        assert name_r == []
        assert "body_contact_lure" in body_r

    def test_wame_link_body(self):
        name_r, body_r = _detect("User", "wa.me/16729066441 for investment tips")
        assert name_r == []
        assert "body_contact_lure" in body_r

    def test_innocent_victim_report_no_body_lure(self):
        """Victim reporting the scam mentions WhatsApp — NOT a lure"""
        _, body_r = _detect("Ellen", "I've gotten countless WhatsApp messages from fake Charlie accounts")
        assert "body_contact_lure" not in body_r

    def test_scam_discussion_no_body_lure(self):
        _, body_r = _detect("Tom H", "Sorry you have to deal with these Telegram scammers")
        assert "body_contact_lure" not in body_r


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

class TestAllowlist:
    def test_allowlist_contains_real_charlie(self):
        assert REAL_CHARLIE_USER_ID in ALLOWLISTED_USER_IDS
        assert REAL_CHARLIE_USER_ID == 27965159


# ---------------------------------------------------------------------------
# _walk_comments — tree flattening
# ---------------------------------------------------------------------------

class TestWalkComments:
    def test_flat_list(self):
        comments = [{"id": 1}, {"id": 2}]
        assert _walk_comments(comments) == [{"id": 1}, {"id": 2}]

    def test_nested_children(self):
        comments = [
            {"id": 1, "children": [{"id": 2, "children": [{"id": 3}]}]},
        ]
        flat = _walk_comments(comments)
        assert [c["id"] for c in flat] == [1, 2, 3]

    def test_empty(self):
        assert _walk_comments([]) == []

    def test_non_dict_entries_skipped(self):
        assert _walk_comments([None, "bad", 42]) == []

    def test_sibling_order_preserved(self):
        """Multiple siblings at each level must appear left-to-right."""
        comments = [
            {"id": 1, "children": [{"id": 2}, {"id": 3}]},
            {"id": 4, "children": [{"id": 5}]},
        ]
        ids = [c["id"] for c in _walk_comments(comments)]
        assert ids == [1, 2, 3, 4, 5]

    def test_comments_key_as_children(self):
        """Nodes using 'comments' key (not 'children') are also flattened."""
        comments = [{"id": 1, "comments": [{"id": 2}]}]
        assert [c["id"] for c in _walk_comments(comments)] == [1, 2]

    def test_deep_tree_no_recursion_error(self):
        """A chain 2000 nodes deep must not raise RecursionError.

        Previously the recursive implementation would exceed Python's default
        recursion limit (~1000) on such a tree.
        """
        # Build a chain: node 0 → node 1 → … → node 1999
        depth = 2000
        # Construct from the bottom up
        node = {"id": depth - 1}
        for i in range(depth - 2, -1, -1):
            node = {"id": i, "children": [node]}
        flat = _walk_comments([node])
        assert len(flat) == depth
        assert [c["id"] for c in flat] == list(range(depth))


# ---------------------------------------------------------------------------
# _record_candidate — routing, daily cap, dedupe
# (today_count param removed from signature — Fix E)
# ---------------------------------------------------------------------------

class TestRecordCandidate:
    def _make_state(self):
        return {"acted_user_ids": [], "daily_counts": {}}

    def test_name_reasons_go_to_candidates(self):
        candidates, uncertain = [], []
        state = self._make_state()
        acted = set()
        _record_candidate(
            candidates, uncertain, acted, state,
            user_id=999, name="Test User",
            name_reasons=["whatsapp_contact"], body_reasons=[],
            surface="comment", ref_id=1, post_id=1,
            daily_cap=20, dry_run=True,
        )
        assert len(candidates) == 1
        assert candidates[0]["status"] == "would_report"
        assert "dry_run_call" in candidates[0]
        assert uncertain == []

    def test_body_only_goes_to_uncertain_not_candidate(self):
        """body_reasons only → uncertain, status body_only_review, never candidate"""
        candidates, uncertain = [], []
        state = self._make_state()
        acted = set()
        _record_candidate(
            candidates, uncertain, acted, state,
            user_id=777, name="Ellen",
            name_reasons=[], body_reasons=["body_contact_lure"],
            surface="comment", ref_id=2, post_id=2,
            daily_cap=20, dry_run=True,
        )
        assert candidates == []
        assert len(uncertain) == 1
        assert uncertain[0]["status"] == "body_only_review"
        assert "dry_run_call" not in uncertain[0]

    def test_body_only_does_not_consume_cap(self):
        """body_only match must NOT increment the daily count"""
        candidates, uncertain = [], []
        state = self._make_state()
        acted = set()
        # fill cap with a real name match first
        _record_candidate(
            candidates, uncertain, acted, state,
            user_id=111, name="Charlie Garcia",
            name_reasons=["charlie_garcia_name"], body_reasons=[],
            surface="comment", ref_id=10, post_id=10,
            daily_cap=1, dry_run=True,
        )
        assert len(candidates) == 1
        # now a body-only should still go to uncertain, not cap_exceeded
        _record_candidate(
            candidates, uncertain, acted, state,
            user_id=222, name="Ellen",
            name_reasons=[], body_reasons=["body_contact_lure"],
            surface="comment", ref_id=11, post_id=10,
            daily_cap=1, dry_run=True,
        )
        assert len(candidates) == 1  # no new candidate
        assert uncertain[0]["status"] == "body_only_review"  # not cap_exceeded

    def test_name_and_body_goes_to_candidates(self):
        """Both name + body reasons → candidate (name-driven)"""
        candidates, uncertain = [], []
        state = self._make_state()
        acted = set()
        _record_candidate(
            candidates, uncertain, acted, state,
            user_id=333, name="Charlie Garcia",
            name_reasons=["charlie_garcia_name"], body_reasons=["body_contact_lure"],
            surface="comment", ref_id=3, post_id=3,
            daily_cap=20, dry_run=True,
        )
        assert len(candidates) == 1
        assert candidates[0]["status"] == "would_report"
        assert "body_contact_lure" in candidates[0]["body_reasons"]

    def test_dedupe_skips_acted(self):
        candidates, uncertain = [], []
        state = self._make_state()
        acted = {999}
        _record_candidate(
            candidates, uncertain, acted, state,
            user_id=999, name="Test User",
            name_reasons=["whatsapp_contact"], body_reasons=[],
            surface="comment", ref_id=1, post_id=1,
            daily_cap=20, dry_run=True,
        )
        assert candidates == []
        assert uncertain == []

    def test_daily_cap_shunts_name_to_uncertain(self):
        candidates, uncertain = [], []
        state = {"acted_user_ids": [], "daily_counts": {"2026-10-15": 20}}
        acted = set()
        with mock.patch("substack_cli.impersonation_state._today", return_value="2026-10-15"):
            _record_candidate(
                candidates, uncertain, acted, state,
                user_id=888, name="Cap Test",
                name_reasons=["phone_number"], body_reasons=[],
                surface="comment", ref_id=2, post_id=2,
                daily_cap=20, dry_run=True,
            )
        assert candidates == []
        assert len(uncertain) == 1
        assert uncertain[0]["status"] == "cap_exceeded"
        assert "dry_run_call" not in uncertain[0]

    def test_no_reasons_is_noop(self):
        candidates, uncertain = [], []
        state = self._make_state()
        acted = set()
        _record_candidate(
            candidates, uncertain, acted, state,
            user_id=555, name="Nobody",
            name_reasons=[], body_reasons=[],
            surface="comment", ref_id=5, post_id=5,
            daily_cap=20, dry_run=True,
        )
        assert candidates == []
        assert uncertain == []


# ---------------------------------------------------------------------------
# Fix A — Telegram false-positive: require Telegram context
# ---------------------------------------------------------------------------

class TestDetectTelegramContextRequired:
    def test_bare_handle_no_telegram_context_not_flagged(self):
        """@CharlieFan2024 alone — no t.me/ or 'telegram' → telegram_contact NOT flagged"""
        name_r, _ = _detect("@CharlieFan2024", "", user_id=99999)
        assert "telegram_contact" not in name_r

    def test_tme_link_with_handle_flagged(self):
        """t.me/charlie @CharlieOfficial — has t.me/ context → telegram_contact flagged"""
        name_r, _ = _detect("t.me/charlie @CharlieOfficial", "", user_id=99999)
        assert "telegram_contact" in name_r

    def test_telegram_keyword_with_handle_flagged(self):
        """'Charlie telegram @charlie_official' — has 'telegram' keyword → flagged"""
        name_r, _ = _detect("Charlie telegram @charlie_official", "", user_id=99999)
        assert "telegram_contact" in name_r

    def test_tme_link_alone_flagged(self):
        """t.me/charliegarcia_official alone — t.me/ is always a signal"""
        name_r, _ = _detect("t.me/charliegarcia_official", "", user_id=99999)
        assert "telegram_contact" in name_r

    def test_telegram_keyword_alone_flagged(self):
        """'Charlie telegram support' — 'telegram' keyword alone is sufficient"""
        name_r, _ = _detect("Charlie telegram support", "", user_id=99999)
        assert "telegram_contact" in name_r


# ---------------------------------------------------------------------------
# Fix B — assert → RuntimeError for dry-run + SUBSTACK_ENABLE_WRITE=true
# ---------------------------------------------------------------------------

class TestSafetyAbortCheck:
    def test_dry_run_with_write_env_raises(self):
        """SUBSTACK_ENABLE_WRITE=true + dry_run=True → RuntimeError with SAFETY ABORT"""
        with mock.patch.dict(os.environ, {"SUBSTACK_ENABLE_WRITE": "true"}):
            with pytest.raises(RuntimeError, match="SAFETY ABORT"):
                from substack_cli.impersonation import run_scan
                # Use a minimal mock client; the check fires before any client calls
                run_scan(mock.MagicMock(), post_limit=0, notes_limit=0, dry_run=True)

    def test_dry_run_without_write_env_no_raise(self):
        """SUBSTACK_ENABLE_WRITE unset + dry_run=True → no RuntimeError"""
        env = {k: v for k, v in os.environ.items() if k != "SUBSTACK_ENABLE_WRITE"}
        with mock.patch.dict(os.environ, env, clear=True):
            from substack_cli.impersonation import run_scan
            client = mock.MagicMock()
            # archive returns empty list → scan completes without network
            client.get.return_value = []
            # Should not raise
            result = run_scan(client, post_limit=0, notes_limit=0, dry_run=True)
            assert "posts_scanned" in result


# ---------------------------------------------------------------------------
# Fix C — removeprefix vs lstrip correctness
# ---------------------------------------------------------------------------

class TestRemovePrefix:
    def test_entity_key_with_c_dash_prefix(self):
        assert "c-12345".removeprefix("c-") == "12345"

    def test_entity_key_without_prefix_unchanged(self):
        assert "12345".removeprefix("c-") == "12345"

    def test_entity_key_c_alone_unchanged(self):
        """'c12345' has no dash after c — removeprefix leaves it intact"""
        assert "c12345".removeprefix("c-") == "c12345"

    def test_lstrip_would_overconsume_but_removeprefix_does_not(self):
        """lstrip('c-') would strip all leading c and - chars;
        removeprefix('c-') only strips the exact prefix 'c-' once."""
        assert "cc-12345".removeprefix("c-") == "cc-12345"
        # Contrast: "cc-12345".lstrip("c-") would return "12345" (wrong)
        assert "cc-12345".lstrip("c-") == "12345"


# ---------------------------------------------------------------------------
# Item 2 — entity_key format: skip + warn on unexpected prefix
# ---------------------------------------------------------------------------

class TestEntityKeyFormat:
    """run_scan skips note items whose entity_key lacks the 'c-' prefix,
    and emits a warning to stderr for non-empty unexpected keys."""

    def _make_scan_client(self, note_items):
        """Return a mock client that yields the given note_items from the Notes feed."""
        client = mock.MagicMock()
        # archive returns empty list → no post comments to scan
        # Notes feed returns note_items
        client.get.side_effect = [[], note_items]
        return client

    def test_c_prefix_item_is_processed(self):
        """entity_key='c-42' with no comment.id → note_id extracted, item scanned."""
        from substack_cli.impersonation import run_scan
        item = {"entity_key": "c-42", "item": {"comment": {}, "children": []}}
        client = self._make_scan_client([item])
        env = {k: v for k, v in os.environ.items() if k != "SUBSTACK_ENABLE_WRITE"}
        with mock.patch.dict(os.environ, env, clear=True):
            result = run_scan(client, post_limit=1, notes_limit=1, dry_run=True)
        assert result["notes_scanned"] == 1

    def test_unexpected_key_format_is_skipped(self, capsys):
        """entity_key='note-123' (no 'c-' prefix, no comment.id) → item skipped,
        warning written to stderr, notes_scanned stays 0."""
        from substack_cli.impersonation import run_scan
        item = {"entity_key": "note-123", "item": {"comment": {}, "children": []}}
        client = self._make_scan_client([item])
        env = {k: v for k, v in os.environ.items() if k != "SUBSTACK_ENABLE_WRITE"}
        with mock.patch.dict(os.environ, env, clear=True):
            result = run_scan(client, post_limit=1, notes_limit=1, dry_run=True)
        assert result["notes_scanned"] == 0
        captured = capsys.readouterr()
        assert "note-123" in captured.err
        assert "Warning" in captured.err

    def test_empty_entity_key_no_comment_id_skipped_silently(self, capsys):
        """entity_key='' and no comment.id → skipped silently (no warning)."""
        from substack_cli.impersonation import run_scan
        item = {"entity_key": "", "item": {"comment": {}, "children": []}}
        client = self._make_scan_client([item])
        env = {k: v for k, v in os.environ.items() if k != "SUBSTACK_ENABLE_WRITE"}
        with mock.patch.dict(os.environ, env, clear=True):
            result = run_scan(client, post_limit=1, notes_limit=1, dry_run=True)
        assert result["notes_scanned"] == 0
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_comment_id_takes_precedence_over_entity_key(self):
        """comment.id is present → note_id uses it, entity_key ignored."""
        from substack_cli.impersonation import run_scan
        item = {
            "entity_key": "note-999",  # bad format — but comment.id overrides
            "item": {"comment": {"id": 77}, "children": []},
        }
        client = self._make_scan_client([item])
        env = {k: v for k, v in os.environ.items() if k != "SUBSTACK_ENABLE_WRITE"}
        with mock.patch.dict(os.environ, env, clear=True):
            result = run_scan(client, post_limit=1, notes_limit=1, dry_run=True)
        assert result["notes_scanned"] == 1

    def test_valid_key_does_not_change_json_output_schema(self):
        """Valid c- key: result dict keys match the documented schema."""
        from substack_cli.impersonation import run_scan
        item = {"entity_key": "c-55", "item": {"comment": {}, "children": []}}
        client = self._make_scan_client([item])
        env = {k: v for k, v in os.environ.items() if k != "SUBSTACK_ENABLE_WRITE"}
        with mock.patch.dict(os.environ, env, clear=True):
            result = run_scan(client, post_limit=1, notes_limit=1, dry_run=True)
        for key in ("posts_scanned", "notes_scanned", "candidates_found",
                    "uncertain_count", "would_report", "uncertain",
                    "log_path", "summary_path"):
            assert key in result, f"Missing key {key!r} in result"


# ---------------------------------------------------------------------------
# Item 2 — --no-dry-run refuses with error
# ---------------------------------------------------------------------------

class TestNoDryRunRefuses:
    def test_no_dry_run_refuses_with_error(self):
        """dry_run=False → SystemExit(1) with error message"""
        from substack_cli.impersonation import run_scan
        client = mock.MagicMock()
        with pytest.raises(SystemExit) as exc_info:
            run_scan(client, post_limit=0, notes_limit=0, dry_run=False)
        assert exc_info.value.code == 1
