"""Tests for substack_cli.markdown_doc — Markdown → ProseMirror converter."""
import pytest

from substack_cli.markdown_doc import doc_to_body_string, markdown_to_doc, parse_inline


# ---------------------------------------------------------------------------
# parse_inline
# ---------------------------------------------------------------------------

def test_parse_inline_plain_text():
    nodes = parse_inline("hello")
    assert nodes == [{"type": "text", "text": "hello"}]


def test_parse_inline_bold():
    nodes = parse_inline("**bold**")
    assert nodes[0]["text"] == "bold"
    assert any(m["type"] == "strong" for m in nodes[0]["marks"])


def test_parse_inline_italic():
    nodes = parse_inline("*italic*")
    assert nodes[0]["text"] == "italic"
    assert any(m["type"] == "em" for m in nodes[0]["marks"])


def test_parse_inline_link():
    nodes = parse_inline("[text](url)")
    assert nodes[0]["text"] == "text"
    link = next(m for m in nodes[0]["marks"] if m["type"] == "link")
    assert link["attrs"]["href"] == "url"


# ---------------------------------------------------------------------------
# horizontal_rule — all three markers
# ---------------------------------------------------------------------------

def test_horizontal_rule_dash():
    doc = markdown_to_doc("---")
    assert doc["content"] == [{"type": "horizontal_rule"}]


def test_horizontal_rule_star():
    doc = markdown_to_doc("***")
    assert doc["content"] == [{"type": "horizontal_rule"}]


def test_horizontal_rule_underscore():
    doc = markdown_to_doc("___")
    assert doc["content"] == [{"type": "horizontal_rule"}]


def test_horizontal_rule_longer_dashes():
    doc = markdown_to_doc("-----")
    assert doc["content"] == [{"type": "horizontal_rule"}]


def test_horizontal_rule_mid_document():
    doc = markdown_to_doc("Intro\n\n---\n\nOutro")
    types = [n["type"] for n in doc["content"]]
    assert types == ["paragraph", "horizontal_rule", "paragraph"]


def test_horizontal_rule_between_paragraphs_no_blank_lines():
    # hr flushes an in-progress paragraph even without a blank line before it
    doc = markdown_to_doc("Before\n---\nAfter")
    types = [n["type"] for n in doc["content"]]
    assert types == ["paragraph", "horizontal_rule", "paragraph"]


def test_horizontal_rule_regression_body_text_and_signature():
    """'Body text\n\n---\n\nThanks,\nGriffin' — the coordinator's verified bug."""
    doc = markdown_to_doc("Body text\n\n---\n\nThanks,\nGriffin")
    types = [n["type"] for n in doc["content"]]
    assert types == ["paragraph", "horizontal_rule", "paragraph"]
    # The '---' must NOT appear as text
    for node in doc["content"]:
        if node["type"] == "paragraph":
            for inline in node.get("content", []):
                assert inline.get("text") != "---"


def test_star_hr_not_mistaken_for_bold_italic():
    # *** on its own line is a horizontal_rule, not inline bold/italic
    doc = markdown_to_doc("***")
    assert doc["content"] == [{"type": "horizontal_rule"}]


# ---------------------------------------------------------------------------
# hard_break — consecutive lines within a paragraph
# ---------------------------------------------------------------------------

def test_hard_break_two_lines():
    doc = markdown_to_doc("Thanks,\nGriffin")
    para = doc["content"][0]
    assert para["type"] == "paragraph"
    node_types = [n["type"] for n in para["content"]]
    assert "hard_break" in node_types


def test_hard_break_three_lines():
    doc = markdown_to_doc("Line1\nLine2\nLine3")
    para = doc["content"][0]
    node_types = [n["type"] for n in para["content"]]
    assert node_types.count("hard_break") == 2


def test_hard_break_text_content_preserved():
    doc = markdown_to_doc("Thanks,\nGriffin")
    para = doc["content"][0]
    texts = [n["text"] for n in para["content"] if n["type"] == "text"]
    assert "Thanks," in texts
    assert "Griffin" in texts


def test_hard_break_regression_no_concatenation():
    """Regression: 'Thanks,Griffin' (no separator) must NOT appear."""
    doc = markdown_to_doc("Thanks,\nGriffin")
    para = doc["content"][0]
    for node in para["content"]:
        assert node.get("text") != "Thanks,Griffin"


def test_hard_break_with_inline_marks():
    doc = markdown_to_doc("**Bold line**\nPlain line")
    para = doc["content"][0]
    node_types = [n["type"] for n in para["content"]]
    assert "hard_break" in node_types
    # Bold mark present on first text node
    bold_node = para["content"][0]
    assert any(m["type"] == "strong" for m in bold_node.get("marks", []))


# ---------------------------------------------------------------------------
# Existing behaviour — headings, bold, italic, link, list-error, image-error
# ---------------------------------------------------------------------------

def test_single_paragraph():
    doc = markdown_to_doc("Hello world")
    assert doc["type"] == "doc"
    assert len(doc["content"]) == 1
    assert doc["content"][0]["type"] == "paragraph"
    assert doc["content"][0]["content"][0]["text"] == "Hello world"


def test_heading_levels_1_to_3():
    for level, prefix in [(1, "#"), (2, "##"), (3, "###")]:
        doc = markdown_to_doc(f"{prefix} Heading {level}")
        h = doc["content"][0]
        assert h["type"] == "heading"
        assert h["attrs"]["level"] == level
        assert h["content"][0]["text"] == f"Heading {level}"


def test_bold_mark():
    doc = markdown_to_doc("**bold**")
    node = doc["content"][0]["content"][0]
    assert node["text"] == "bold"
    assert any(m["type"] == "strong" for m in node["marks"])


def test_italic_mark():
    doc = markdown_to_doc("*italic*")
    node = doc["content"][0]["content"][0]
    assert node["text"] == "italic"
    assert any(m["type"] == "em" for m in node["marks"])


def test_link_mark():
    doc = markdown_to_doc("[text](url)")
    node = doc["content"][0]["content"][0]
    assert node["text"] == "text"
    link = next(m for m in node["marks"] if m["type"] == "link")
    assert link["attrs"]["href"] == "url"


def test_list_raises_not_implemented():
    with pytest.raises(NotImplementedError) as exc_info:
        markdown_to_doc("- item")
    assert "--body-json" in str(exc_info.value)


def test_image_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        markdown_to_doc("![alt](img.png)")


def test_two_paragraphs_separated_by_blank_line():
    doc = markdown_to_doc("First\n\nSecond")
    assert len(doc["content"]) == 2
    assert doc["content"][0]["type"] == "paragraph"
    assert doc["content"][1]["type"] == "paragraph"


# ---------------------------------------------------------------------------
# doc_to_body_string
# ---------------------------------------------------------------------------

def test_doc_to_body_string_returns_json_string():
    import json
    doc = {"type": "doc", "content": []}
    result = doc_to_body_string(doc)
    assert isinstance(result, str)
    assert json.loads(result) == doc
