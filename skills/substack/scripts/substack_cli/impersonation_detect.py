"""Substack CLI — impersonation detection rules, regexes, and confusables.

Detection logic for the Capital Mischief impersonation scan.
Scan orchestration lives in impersonation.py; state management in impersonation_state.py.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The real Charlie Garcia — never flag this account.
REAL_CHARLIE_USER_ID = 27965159
REAL_CHARLIE_HANDLE = "charliepgarcia"

# Additional team accounts to allowlist (extend as needed).
ALLOWLISTED_USER_IDS: frozenset[int] = frozenset({
    REAL_CHARLIE_USER_ID,
})

# ---------------------------------------------------------------------------
# Unicode confusables map for 'charlie'
# ---------------------------------------------------------------------------
# Maps confusable Unicode chars to their ASCII equivalents.
# Generated from the most common homoglyphs seen in impersonation attempts.
# The full Unicode confusables list (confusables.txt from unicode.org) is not
# bundled; we cover the high-frequency set that fits in a maintainable dict.

_CONFUSABLES: dict[str, str] = {
    # Latin lookalikes for C/c
    "\u0421": "c",  # Cyrillic С
    "\u03F9": "c",  # Greek Ϲ (lunate sigma)
    "\u2CA5": "c",  # Coptic small ϲ
    "\uFF23": "c",  # Fullwidth Ｃ
    "\uFF43": "c",  # Fullwidth ｃ
    # Latin lookalikes for h
    "\u04BB": "h",  # Cyrillic һ
    "\uFF48": "h",  # Fullwidth ｈ
    # Latin lookalikes for a
    "\u0430": "a",  # Cyrillic а
    "\u03B1": "a",  # Greek α
    "\uFF41": "a",  # Fullwidth ａ
    # Latin lookalikes for r
    "\u0433": "r",  # Cyrillic г (stretched)
    "\uFF52": "r",  # Fullwidth ｒ
    # Latin lookalikes for l
    "\u04C0": "l",  # Cyrillic Ӏ
    "\u0031": "l",  # digit 1 (common swap)
    "\uFF4C": "l",  # Fullwidth ｌ
    "\u007C": "l",  # pipe |
    # Latin lookalikes for i
    "\u0456": "i",  # Cyrillic і
    "\u04CF": "i",  # Cyrillic ӏ
    "\uFF49": "i",  # Fullwidth ｉ
    # Latin lookalikes for e
    "\u0435": "e",  # Cyrillic е
    "\u03B5": "e",  # Greek ε
    "\uFF45": "e",  # Fullwidth ｅ
}

# Mathematical bold/italic/script Unicode blocks map A-Z, a-z to codepoints.
# e.g. Mathematical Bold Capital C = U+1D402; Bold Small c = U+1D41C
# We handle these by NFKD normalization (which collapses compatibility chars).


def _normalize_for_detection(text: str) -> str:
    """Normalize text to ASCII-comparable form for detection purposes.

    Steps:
    1. NFKD decomposition (collapses math bold/script/italic Unicode blocks
       like 𝐂𝐡𝐚𝐫𝐥𝐢𝐞 → individual base chars + combining diacritics).
    2. Strip combining diacritical marks (so Ċ → C, etc.).
    3. Apply manual confusables map for chars that NFKD misses.
    4. Lowercase.
    5. Strip non-alphanumeric chars (spaces, dots, underscores, etc.) so
       "C.h.a.r.l.i.e" and "C_h_a_r_l_i_e" both become "charlie".
    """
    # Step 1+2: NFKD + strip combining marks
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = "".join(
        ch for ch in nfkd
        if unicodedata.category(ch) != "Mn"  # Mn = non-spacing mark
    )
    # Step 3: manual confusables
    mapped = "".join(_CONFUSABLES.get(ch, ch) for ch in stripped)
    # Step 4+5: lowercase, keep only alphanumeric
    return re.sub(r"[^a-z0-9]", "", mapped.lower())


def _has_non_ascii_confusable(text: str) -> bool:
    """Return True if *text* contains any non-ASCII character that our
    confusables map (or NFKD) converts to an ASCII letter.

    Used to distinguish 'Ϲharlie Garcia' (confusable C → disguise signal)
    from plain ASCII 'Charlie Garcia' (innocent reader).
    """
    for ch in text:
        if ord(ch) <= 127:
            continue
        # Check manual confusables map
        if ch in _CONFUSABLES:
            return True
        # Check whether NFKD decomposition yields any ASCII base letter
        nfkd_ch = unicodedata.normalize("NFKD", ch)
        base = "".join(
            c for c in nfkd_ch if unicodedata.category(c) != "Mn"
        )
        if base and all(ord(c) <= 127 for c in base) and base.isalpha():
            return True
    return False


# ---------------------------------------------------------------------------
# Detection rules — regexes
# ---------------------------------------------------------------------------

# WhatsApp/Telegram contact lure patterns
_WHATSAPP_RE = re.compile(
    r"whatsapp|whats\s*app|wa\.me|api\.whatsapp",
    re.IGNORECASE,
)
_TELEGRAM_URL_RE = re.compile(
    r"telegram|(?<![a-z_])tg\b|t\.me/",  # Telegram keyword/abbrev or t.me link (always a signal)
    re.IGNORECASE,
)
_TELEGRAM_HANDLE_RE = re.compile(
    r"@[A-Za-z0-9_]{4,}",  # bare @handle — only meaningful with Telegram context
)
_PHONE_RE = re.compile(
    # International phone: +1 672 906 6441, +16729066441, (672) 906-6441, etc.
    r"(\+?\d[\d\s\-\.\(\)]{6,}\d)",
)

# 'charlie' and 'charliegarcia' after normalization
_CHARLIE_NORM = "charlie"
_CHARLIEGARCIA_NORM = "charliegarcia"
_CHARLIEPGARCIA_NORM = "charliepgarcia"

# Surnames that, combined with 'charlie', target Charlie Garcia specifically.
# Normalization strips spaces/punctuation so "p garcia", "garcia" both match.
_GARCIA_VARIANTS: frozenset[str] = frozenset({
    "charliegarcia",
    "charliepgarcia",
})

# Body scanning: only flag bodies that contain a *direct contact invitation*
# — an imperative verb near whatsapp/telegram, or a bare link/phone together
# with whatsapp/telegram.  Readers reporting or discussing the scam say things
# like "I got a WhatsApp from fake Charlie" — those must NOT match.

# Imperative contact verbs: "WhatsApp me", "message me on Telegram", etc.
_BODY_INVITE_VERB_RE = re.compile(
    r"\b(?:whatsapp|message|text|contact|dm|reach|ping|chat)\s+me\b"
    r"|"
    r"\b(?:reach\s+out|get\s+in\s+touch)\b",
    re.IGNORECASE,
)

# wa.me / t.me links  (direct invite links)
_BODY_INVITE_LINK_RE = re.compile(
    r"(?:wa\.me|t\.me)/\S+",
    re.IGNORECASE,
)

# Phone number in body (same pattern as name)
_BODY_PHONE_RE = _PHONE_RE

# @handle in body (Telegram-style, ≥4 chars)
_BODY_HANDLE_RE = re.compile(r"@[A-Za-z0-9_]{4,}")


def _body_has_contact_lure(body: str) -> bool:
    """Return True only when the body carries a *direct contact invitation*.

    A direct contact invitation is one of:
      (a) An imperative contact verb ("WhatsApp me", "message me", etc.)
          near whatsapp or telegram keyword.
      (b) A wa.me or t.me invite link together with whatsapp/telegram keyword.
      (c) A phone number or @handle together with whatsapp/telegram keyword.

    A reader saying "I got a WhatsApp from a fake Charlie" does NOT match
    because there is no imperative verb, invite link, or bare phone/handle
    paired with the platform keyword.
    """
    has_whatsapp = bool(_WHATSAPP_RE.search(body))
    has_telegram = bool(_TELEGRAM_URL_RE.search(body) or _TELEGRAM_HANDLE_RE.search(body))
    if not (has_whatsapp or has_telegram):
        return False

    # Condition (a): imperative verb present
    if _BODY_INVITE_VERB_RE.search(body):
        return True

    # Condition (b): invite link present
    if _BODY_INVITE_LINK_RE.search(body):
        return True

    # Condition (c): phone number or @handle present alongside platform keyword
    if _BODY_PHONE_RE.search(body) or _BODY_HANDLE_RE.search(body):
        return True

    return False


def _detect(name: str, body: str, *, user_id: Optional[int] = None) -> tuple[list[str], list[str]]:
    """Return (name_reasons, body_reasons).

    name_reasons — triggers from the display name; actionable (candidates).
    body_reasons — triggers from the comment body; uncertain bucket only,
                   never auto-reported.

    Bug 1 fix — disguised_charlie_name rules (replaces the old substring-only
    check that matched any 'Charlie <Surname>'):

      Rule A — charlie_garcia_name:
        Normalized name contains 'charliegarcia' or 'charliepgarcia' AND
        user_id is not the real Charlie (27965159).  Catches impersonators
        who write "Charlie Garcia", "Charlie P. Garcia", "charliepgarcia", etc.

      Rule B — charlie_confusable_name:
        The raw name contains at least one non-ASCII confusable letter AND
        the normalized form contains 'charlie'.  A disguise signal regardless
        of surname: scammers use 𝐂𝐡𝐚𝐫𝐥𝐢𝐞, Ϲharlie, ＣＨＡＲＬＩＥ, etc.

      Rule C — charlie_with_lure_name:
        'charlie' in normalized name AND a contact lure (whatsapp/telegram/
        phone/@handle) also appears in the display name.  Catches "Charlie
        WhatsApp +1 672 906 6441" even without a Garcia surname.

    A plain ASCII 'Charlie <OtherSurname>' with no lure does NOT match any
    rule and is never flagged (e.g. 'Charlie Smith', 'Charlie Brown').

    Note on 'Charles Garcia' / 'Charlotte Garcia': normalized forms are
    'charlesgarcia' / 'charlottegarcia' — neither contains 'charliegarcia',
    and both are plain ASCII with no lure, so they do NOT match.  'Charles
    Garcia' is at most uncertain; flagging it would be a false positive.

    Bug 2 fix — body scanning:
      Body matches go to body_reasons (uncertain bucket, never auto-reported).
      Only direct contact invitations match (see _body_has_contact_lure).
    """
    name_reasons: list[str] = []
    body_reasons: list[str] = []

    # ---- Name rules (contact lures) ----
    if _WHATSAPP_RE.search(name):
        name_reasons.append("whatsapp_contact")
    _tg_url = bool(_TELEGRAM_URL_RE.search(name))
    _tg_handle = bool(_TELEGRAM_HANDLE_RE.search(name))
    # Flag telegram_contact only when Telegram context is present (URL/keyword),
    # OR when both a t.me/ link and a @handle appear together.
    # A bare @handle alone (no "telegram"/"tg"/t.me/) does NOT trigger.
    if _tg_url or (_tg_handle and "t.me/" in name.lower()):
        name_reasons.append("telegram_contact")
    # bare @handle with no Telegram context → do not flag
    if _PHONE_RE.search(name):
        name_reasons.append("phone_number")

    # ---- Name rules (disguised Charlie) ----
    norm_name = _normalize_for_detection(name)

    # Rule A: charlie garcia / charliepgarcia exact target
    if any(variant in norm_name for variant in _GARCIA_VARIANTS):
        is_real = (user_id is not None and int(user_id) == REAL_CHARLIE_USER_ID)
        if not is_real:
            name_reasons.append("charlie_garcia_name")

    # Rule B: confusable letters that normalize to 'charlie' (disguise signal)
    if _CHARLIE_NORM in norm_name and _has_non_ascii_confusable(name):
        name_reasons.append("charlie_confusable_name")

    # Rule C: 'charlie' in name AND contact lure also in name
    if _CHARLIE_NORM in norm_name and (
        _WHATSAPP_RE.search(name) or _TELEGRAM_URL_RE.search(name) or _TELEGRAM_HANDLE_RE.search(name) or _PHONE_RE.search(name)
    ):
        name_reasons.append("charlie_with_lure_name")

    # ---- Body rule (uncertain bucket only) ----
    if _body_has_contact_lure(body):
        body_reasons.append("body_contact_lure")

    return name_reasons, body_reasons
