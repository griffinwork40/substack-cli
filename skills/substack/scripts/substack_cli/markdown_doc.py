"""Substack CLI — Markdown → ProseMirror converter.

Public API: parse_inline, markdown_to_doc, doc_to_body_string.

Supports: blank-line-separated paragraphs, #/##/### headings, **bold**,
*italic*, [text](url) links, --- / *** / ___ horizontal rules, hard line
breaks (single newline within a paragraph block), and CommonMark setext
headings (text underlined with === for h1 or --- for h2). Raises
NotImplementedError for lists, images, and other unsupported constructs.
"""
import json
import re


# ---------------------------------------------------------------------------
# Inline parser
# ---------------------------------------------------------------------------

def parse_inline(text: str) -> list:
    """Parse inline markdown (bold, italic, links) into ProseMirror text nodes."""
    nodes: list = []
    pattern = re.compile(
        r"\*\*(.+?)\*\*"       # bold
        r"|\*(.+?)\*"           # italic
        r"|\[(.+?)\]\((.+?)\)",  # link
    )
    pos = 0
    for m in pattern.finditer(text):
        if m.start() > pos:
            nodes.append({"type": "text", "text": text[pos : m.start()]})
        if m.group(1) is not None:  # bold
            nodes.append(
                {"type": "text", "text": m.group(1), "marks": [{"type": "strong"}]}
            )
        elif m.group(2) is not None:  # italic
            nodes.append(
                {"type": "text", "text": m.group(2), "marks": [{"type": "em"}]}
            )
        elif m.group(3) is not None:  # link
            nodes.append(
                {
                    "type": "text",
                    "text": m.group(3),
                    "marks": [{"type": "link", "attrs": {"href": m.group(4)}}],
                }
            )
        pos = m.end()
    if pos < len(text):
        nodes.append({"type": "text", "text": text[pos:]})
    return nodes if nodes else [{"type": "text", "text": text}]


# Matches ---, ***, ___ (3+ repeated identical chars, nothing else on the line)
_HR_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")


def _is_hr(stripped: str) -> bool:
    """Return True if the line is a thematic break (---, ***, ___)."""
    return bool(_HR_RE.match(stripped))


# ---------------------------------------------------------------------------
# Block converter
# ---------------------------------------------------------------------------


# Setext heading underline patterns (CommonMark: 1+ chars, up to 3 leading
# spaces, optional trailing whitespace)
_SETEXT_H1_RE = re.compile(r"^ {0,3}=+[ \t]*$")
_SETEXT_H2_RE = re.compile(r"^ {0,3}-+[ \t]*$")


def _flush_para_as_heading(para_lines: list, level: int) -> dict:
    """Build a heading node from accumulated paragraph lines.

    Multiple pending lines are joined with a space (simpler than hard_break
    inside a heading, and matches CommonMark rendering intent).
    """
    joined = " ".join(para_lines)
    return {
        "type": "heading",
        "attrs": {"level": level},
        "content": parse_inline(joined),
    }


def markdown_to_doc(markdown_text: str) -> dict:
    """Convert minimal Markdown to a ProseMirror document.

    Supports: blank-line-separated paragraphs, #/##/### ATX headings,
    CommonMark setext headings (text underlined with === for h1 or --- for
    h2), **bold**, *italic*, [text](url) links, --- / *** / ___ horizontal
    rules, and hard line breaks (single newline within a paragraph block).

    Setext heading rule: when para_lines are pending (no intervening blank
    line) and the current line is all = chars (level 1) or all - chars
    (level 2, minimum 1 char per CommonMark), the pending lines become the
    heading content.  A standalone --- after a blank line (or at document
    start) remains a horizontal rule; the setext rule only fires when there
    are pending paragraph lines.

    Raises NotImplementedError with a --body-json hint for lists, images,
    footnotes, paywall markers, embeds, and other unsupported constructs.
    """
    lines = markdown_text.split("\n")
    content: list = []
    para_lines: list = []

    def flush_para() -> None:
        if not para_lines:
            return
        para_content: list = []
        for i, ln in enumerate(para_lines):
            if i > 0:
                para_content.append({"type": "hard_break"})
            para_content.extend(parse_inline(ln))
        content.append({"type": "paragraph", "content": para_content})
        para_lines.clear()

    for line in lines:
        stripped = line.strip()

        # Blank line — flush current paragraph
        if not stripped:
            flush_para()
            continue

        # Setext heading underline — only when para_lines are pending
        if para_lines:
            if _SETEXT_H1_RE.match(line):
                content.append(_flush_para_as_heading(para_lines, level=1))
                para_lines.clear()
                continue
            if _SETEXT_H2_RE.match(line):
                content.append(_flush_para_as_heading(para_lines, level=2))
                para_lines.clear()
                continue

        # Horizontal rule — flush paragraph, emit rule node
        # (only reached when para_lines is empty, because setext check above
        #  intercepts --- when para_lines are pending)
        if _is_hr(stripped):
            flush_para()
            content.append({"type": "horizontal_rule"})
            continue

        # ATX heading detection
        heading_match = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading_match:
            flush_para()
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2)
            content.append(
                {
                    "type": "heading",
                    "attrs": {"level": level},
                    "content": parse_inline(heading_text),
                }
            )
            continue

        # List detection — unsupported
        if re.match(r"^[-*+]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
            raise NotImplementedError(
                "Lists are not supported by the minimal Markdown converter. "
                "Use --body-json with a full ProseMirror document for "
                "lists, images, footnotes, paywall markers, or embeds."
            )

        # Image detection — unsupported
        if stripped.startswith("!["):
            raise NotImplementedError(
                "Images are not supported by the minimal Markdown converter. "
                "Use --body-json with a full ProseMirror document, or "
                "upload the image first with `substack image-upload`."
            )

        # Regular paragraph line — accumulate for hard_break joining
        para_lines.append(stripped)

    # Flush any trailing paragraph
    flush_para()

    return {"type": "doc", "content": content}


# ---------------------------------------------------------------------------
# Serialiser
# ---------------------------------------------------------------------------

def doc_to_body_string(doc: dict) -> str:
    """Stringify a ProseMirror doc — Substack requires a JSON string, not a
    nested dict. Submitting a nested object renders literal
    `{type:"doc",...}` text in the published post."""
    return json.dumps(doc)
