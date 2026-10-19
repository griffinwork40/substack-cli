"""Substack CLI — Markdown → ProseMirror converter.

Public API: parse_inline, markdown_to_doc, doc_to_body_string.

Supports: blank-line-separated paragraphs, #/##/### headings, **bold**,
*italic*, [text](url) links, --- / *** / ___ horizontal rules, and
hard line breaks (single newline within a paragraph block). Raises
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

def markdown_to_doc(markdown_text: str) -> dict:
    """Convert minimal Markdown to a ProseMirror document.

    Supports: blank-line-separated paragraphs, #/##/### headings,
    **bold**, *italic*, [text](url) links, --- / *** / ___ horizontal
    rules, and hard line breaks (single newline within a paragraph block).

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

        # Horizontal rule — flush paragraph, emit rule node
        if _is_hr(stripped):
            flush_para()
            content.append({"type": "horizontal_rule"})
            continue

        # Heading detection
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
