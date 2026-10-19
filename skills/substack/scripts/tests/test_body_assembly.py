"""Tests for --append-markdown / --prepend-markdown via body_assembly + CLI."""
import json
import os
import tempfile

import httpx
import pytest
import respx

from substack_cli.body_assembly import assemble_body
from substack_cli.client import SUBSTACK_COM, SubstackClient


# ---------------------------------------------------------------------------
# assemble_body unit tests
# ---------------------------------------------------------------------------

def test_assemble_body_append_to_markdown():
    result = assemble_body(body_markdown="Hello", append_markdown="---\nBye")
    doc = json.loads(result)
    types = [n["type"] for n in doc["content"]]
    assert types[0] == "paragraph"   # Hello
    assert "horizontal_rule" in types
    assert types[-1] == "paragraph"  # Bye


def test_assemble_body_prepend_to_markdown():
    result = assemble_body(body_markdown="Hello", prepend_markdown="Intro")
    doc = json.loads(result)
    texts = []
    for node in doc["content"]:
        for inline in node.get("content", []):
            texts.append(inline.get("text", ""))
    assert texts[0] == "Intro"
    assert "Hello" in texts


def test_assemble_body_both_append_and_prepend():
    result = assemble_body(body_markdown="Middle", append_markdown="End", prepend_markdown="Start")
    doc = json.loads(result)
    texts = []
    for node in doc["content"]:
        for inline in node.get("content", []):
            texts.append(inline.get("text", ""))
    assert texts[0] == "Start"
    assert texts[-1] == "End"
    assert "Middle" in texts


def test_assemble_body_with_body_json():
    body_doc = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "JSON body"}]}]}
    result = assemble_body(body_json=json.dumps(body_doc), append_markdown="Appended")
    doc = json.loads(result)
    types = [n["type"] for n in doc["content"]]
    assert types[0] == "paragraph"
    assert types[-1] == "paragraph"
    last_para_texts = [n.get("text") for n in doc["content"][-1].get("content", [])]
    assert "Appended" in last_para_texts


def test_assemble_body_snippet_only_no_body_create():
    result = assemble_body(append_markdown="Just a sig", is_update=False)
    doc = json.loads(result)
    assert len(doc["content"]) == 1
    assert doc["content"][0]["type"] == "paragraph"


def test_assemble_body_snippet_only_no_body_update_raises():
    with pytest.raises(ValueError, match="--append-markdown"):
        assemble_body(append_markdown="sig", is_update=True)


def test_assemble_body_no_body_no_snippet_returns_none():
    result = assemble_body()
    assert result is None


def test_assemble_body_file_path_input(tmp_path):
    f = tmp_path / "sig.md"
    f.write_text("---\nSigned")
    result = assemble_body(body_markdown="Body", append_markdown=str(f))
    doc = json.loads(result)
    types = [n["type"] for n in doc["content"]]
    assert "horizontal_rule" in types


def test_assemble_body_body_json_file_path(tmp_path):
    body_doc = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "From file"}]}]}
    f = tmp_path / "body.json"
    f.write_text(json.dumps(body_doc))
    result = assemble_body(body_json=str(f), append_markdown="Appended")
    doc = json.loads(result)
    assert doc["content"][0]["content"][0]["text"] == "From file"


# ---------------------------------------------------------------------------
# CLI integration — drafts create with append/prepend
# ---------------------------------------------------------------------------

@respx.mock
def test_drafts_create_append_markdown_splices_nodes(
    isolated_config, authed_env, write_enabled_env, cli_runner, fake_cookies, fake_publication_url
):
    from substack_cli import config, read, publish, manage
    from substack_cli.app import app

    respx.get(f"{SUBSTACK_COM}/api/v1/user/profile/self").mock(
        return_value=httpx.Response(200, json={"id": 1, "publicationUserId": 2})
    )
    route = respx.post(f"{fake_publication_url}/api/v1/drafts").mock(
        return_value=httpx.Response(200, json={"id": 99})
    )

    result = cli_runner.invoke(app, [
        "drafts", "create",
        "--title", "Test",
        "--body-markdown", "Hello world",
        "--append-markdown", "---\nGriffin",
    ])

    assert route.called
    sent = json.loads(route.calls[0].request.content)
    doc = json.loads(sent["draft_body"])
    types = [n["type"] for n in doc["content"]]
    assert types[0] == "paragraph"      # Hello world
    assert types[1] == "horizontal_rule"
    assert types[2] == "paragraph"      # Griffin


@respx.mock
def test_drafts_create_prepend_markdown_splices_nodes(
    isolated_config, authed_env, write_enabled_env, cli_runner, fake_cookies, fake_publication_url
):
    from substack_cli import config, read, publish, manage
    from substack_cli.app import app

    respx.get(f"{SUBSTACK_COM}/api/v1/user/profile/self").mock(
        return_value=httpx.Response(200, json={"id": 1, "publicationUserId": 2})
    )
    route = respx.post(f"{fake_publication_url}/api/v1/drafts").mock(
        return_value=httpx.Response(200, json={"id": 99})
    )

    result = cli_runner.invoke(app, [
        "drafts", "create",
        "--title", "Test",
        "--body-markdown", "Hello world",
        "--prepend-markdown", "DISCLAIMER",
    ])

    assert route.called
    sent = json.loads(route.calls[0].request.content)
    doc = json.loads(sent["draft_body"])
    first_text = doc["content"][0]["content"][0]["text"]
    assert first_text == "DISCLAIMER"


@respx.mock
def test_drafts_update_append_markdown_splices_nodes(
    isolated_config, authed_env, write_enabled_env, cli_runner, fake_cookies, fake_publication_url
):
    from substack_cli import config, read, publish, manage
    from substack_cli.app import app

    route = respx.put(f"{fake_publication_url}/api/v1/drafts/5").mock(
        return_value=httpx.Response(200, json={"id": 5})
    )

    result = cli_runner.invoke(app, [
        "drafts", "update", "5",
        "--body-markdown", "Main content",
        "--append-markdown", "Thanks,\nGriffin",
    ])

    assert route.called
    sent = json.loads(route.calls[0].request.content)
    doc = json.loads(sent["draft_body"])
    types = [n["type"] for n in doc["content"]]
    assert types[0] == "paragraph"   # Main content
    assert types[1] == "paragraph"   # Thanks, + hard_break + Griffin
    # Check hard_break in the second paragraph
    sig_para = doc["content"][1]
    node_types = [n["type"] for n in sig_para["content"]]
    assert "hard_break" in node_types


@respx.mock
def test_drafts_update_snippet_without_body_errors(
    isolated_config, authed_env, write_enabled_env, cli_runner, fake_cookies, fake_publication_url
):
    from substack_cli import config, read, publish, manage
    from substack_cli.app import app

    result = cli_runner.invoke(app, [
        "drafts", "update", "5",
        "--append-markdown", "sig",
    ])

    # write gate is open (write_enabled_env) and auth is set (authed_env), so
    # the only guard that can fire is the assemble_body() ValueError for
    # append/prepend without a body on update.  emit_error() writes JSON to
    # stderr; CliRunner mixes_stderr by default so it appears in result.output.
    assert result.exit_code == 1
    assert "append-markdown" in (result.output or "")


@respx.mock
def test_drafts_create_assembled_body_passes_through_unchanged(
    isolated_config, authed_env, write_enabled_env, cli_runner, fake_cookies, fake_publication_url
):
    """CLI path: assemble_body() result is forwarded via draft_body=, not re-parsed.

    Verifies item 1 fix: drafts_create_cmd calls create_draft(..., draft_body=...)
    so the already-serialised string is never run through json.loads again.
    """
    from substack_cli import config, read, publish, manage
    from substack_cli.app import app

    respx.get(f"{SUBSTACK_COM}/api/v1/user/profile/self").mock(
        return_value=httpx.Response(200, json={"id": 1, "publicationUserId": 2})
    )
    route = respx.post(f"{fake_publication_url}/api/v1/drafts").mock(
        return_value=httpx.Response(200, json={"id": 42})
    )

    result = cli_runner.invoke(app, [
        "drafts", "create",
        "--title", "Passthrough Test",
        "--body-markdown", "Hello",
        "--append-markdown", "---\nSigned",
    ])

    assert result.exit_code == 0, result.output
    assert route.called
    sent = json.loads(route.calls[0].request.content)
    # draft_body must be a valid JSON string (not double-encoded)
    body_str = sent["draft_body"]
    assert isinstance(body_str, str)
    doc = json.loads(body_str)
    assert doc["type"] == "doc"
    types = [n["type"] for n in doc["content"]]
    assert types[0] == "paragraph"        # Hello
    assert types[1] == "horizontal_rule"  # ---
    assert types[2] == "paragraph"        # Signed


@respx.mock
def test_drafts_create_append_with_body_json(
    isolated_config, authed_env, write_enabled_env, cli_runner, fake_cookies, fake_publication_url
):
    from substack_cli import config, read, publish, manage
    from substack_cli.app import app

    respx.get(f"{SUBSTACK_COM}/api/v1/user/profile/self").mock(
        return_value=httpx.Response(200, json={"id": 1, "publicationUserId": 2})
    )
    route = respx.post(f"{fake_publication_url}/api/v1/drafts").mock(
        return_value=httpx.Response(200, json={"id": 99})
    )

    body_doc = {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "JSON body"}]}
    ]}

    result = cli_runner.invoke(app, [
        "drafts", "create",
        "--title", "Test",
        "--body-json", json.dumps(body_doc),
        "--append-markdown", "Appended",
    ])

    assert route.called
    sent = json.loads(route.calls[0].request.content)
    doc = json.loads(sent["draft_body"])
    assert doc["content"][0]["content"][0]["text"] == "JSON body"
    assert doc["content"][1]["content"][0]["text"] == "Appended"
