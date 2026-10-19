"""Substack CLI — body assembly for --append-markdown / --prepend-markdown.

Splices snippet nodes onto the start or end of a ProseMirror doc's content
list. Works whether the body came from --body-markdown or --body-json.
"""
import json
import os
from typing import Optional

from substack_cli.markdown_doc import doc_to_body_string, markdown_to_doc


def _read_markdown(value: str) -> str:
    """If *value* is a path to an existing file, read it; otherwise return as-is."""
    if os.path.isfile(value):
        with open(value) as f:
            return f.read()
    return value


def _snippet_nodes(markdown_snippet: str) -> list:
    """Convert a Markdown snippet to a list of ProseMirror block nodes."""
    doc = markdown_to_doc(markdown_snippet)
    return doc.get("content", [])


def assemble_body(
    *,
    body_markdown: Optional[str] = None,
    body_json: Optional[str] = None,
    append_markdown: Optional[str] = None,
    prepend_markdown: Optional[str] = None,
    is_update: bool = False,
) -> Optional[str]:
    """Build the final draft_body JSON string given optional body + snippets.

    Args:
        body_markdown: Raw Markdown for the main body (not a file path).
        body_json: Raw ProseMirror JSON string or file path for the main body.
        append_markdown: Markdown snippet (or file path) to append.
        prepend_markdown: Markdown snippet (or file path) to prepend.
        is_update: True when called from drafts update (tightens validation).

    Returns:
        A JSON string suitable for draft_body, or None when no body/snippets
        were supplied.

    Raises:
        ValueError: When update is called with snippets but no body.
        NotImplementedError: Propagated from the converter for unsupported
            Markdown constructs.
    """
    has_snippet = append_markdown is not None or prepend_markdown is not None
    has_body = body_markdown is not None or body_json is not None

    # update without body + snippet: error clearly
    if is_update and has_snippet and not has_body:
        raise ValueError(
            "--append-markdown / --prepend-markdown on 'drafts update' requires "
            "a body (--body-markdown or --body-json) to be supplied at the same "
            "time; without a body the command would overwrite the existing post "
            "body with only the snippet."
        )

    # Nothing at all
    if not has_body and not has_snippet:
        return None

    # Build base doc
    if body_markdown is not None:
        doc = markdown_to_doc(body_markdown)
    elif body_json is not None:
        src = body_json
        if os.path.isfile(src):
            with open(src) as f:
                doc = json.load(f)
        else:
            doc = json.loads(src)
    else:
        # Only snippets, no main body (valid for create)
        doc = {"type": "doc", "content": []}

    nodes: list = list(doc.get("content", []))

    if prepend_markdown is not None:
        md = _read_markdown(prepend_markdown)
        nodes = _snippet_nodes(md) + nodes

    if append_markdown is not None:
        md = _read_markdown(append_markdown)
        nodes = nodes + _snippet_nodes(md)

    doc["content"] = nodes
    return doc_to_body_string(doc)
