"""Tests for substack_cli.publish — `drafts set-cover` command.

`set-cover` is a PURE draft edit: it uploads an image, then PUTs the draft
with `cover_image=<url>`. It MUST NOT publish or schedule anything.

No test in this suite performs a real network call; every Substack API
interaction is mocked with respx.
"""
import json

import httpx
import pytest
import respx

# Importing these modules registers their CLI commands on the shared Typer
# apps as an import side effect (mirrors the rest of the suite).
from substack_cli import config, read, publish, manage  # noqa: F401
from substack_cli.app import drafts_app


# The publication host under test. Set directly via env (same mechanism as the
# `authed_env` fixture) so the client resolves to this exact host.
PUB_HOST = "griffinlong.substack.com"
PUB_URL = f"https://{PUB_HOST}"


@pytest.fixture
def set_cover_env(monkeypatch, fake_cookies):
    """Cookies + publication URL for the griffinlong host, WITHOUT the write
    gate (tests opt into writes explicitly). Mirrors conftest's authed_env but
    pins the host the set-cover tests mock."""
    monkeypatch.setenv("SUBSTACK_COOKIES_STRING", fake_cookies)
    monkeypatch.setenv("SUBSTACK_PUBLICATION_URL", PUB_URL)
    monkeypatch.delenv("SUBSTACK_ENABLE_WRITE", raising=False)


@pytest.fixture
def cover_image_file(tmp_path):
    """A small fake PNG file — content need not decode as a real image;
    upload_image only reads+base64-encodes the bytes."""
    path = tmp_path / "cover.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    return str(path)


@respx.mock
def test_set_cover_uploads_then_puts_cover_image_url(
    isolated_config, set_cover_env, write_enabled_env, cli_runner, cover_image_file
):
    """Happy path: with write enabled, `set-cover 123 <img>` uploads the image
    then PUTs the draft with cover_image == the uploaded url. Exit 0."""
    uploaded_url = "https://substackcdn.com/image/abc.png"

    image_route = respx.post(f"{PUB_URL}/api/v1/image").mock(
        return_value=httpx.Response(
            200,
            json={
                "url": uploaded_url,
                "bytes": 1,
                "imageWidth": 2400,
                "imageHeight": 1600,
            },
        )
    )
    put_route = respx.put(f"{PUB_URL}/api/v1/drafts/123").mock(
        return_value=httpx.Response(
            200, json={"id": 123, "cover_image": uploaded_url}
        )
    )

    result = cli_runner.invoke(drafts_app, ["set-cover", "123", cover_image_file])

    assert result.exit_code == 0, result.output

    # The image upload must have happened.
    assert image_route.called

    # The draft PUT body must carry cover_image == the uploaded url.
    assert put_route.called
    put_body = json.loads(put_route.calls[0].request.content)
    assert put_body["cover_image"] == uploaded_url

    # It is a pure draft edit — no publish/schedule endpoints touched.
    called_paths = [call.request.url.path for call in respx.calls]
    assert not any("publish" in p for p in called_paths)
    assert not any("scheduled_release" in p for p in called_paths)

    # Success prints the PUT result as JSON on stdout.
    assert json.loads(result.stdout.strip())["cover_image"] == uploaded_url


def test_set_cover_refuses_without_write_gate(
    isolated_config, set_cover_env, cli_runner, cover_image_file
):
    """With the write gate DISABLED, `set-cover` must exit 1 with a write
    error BEFORE any HTTP call. No respx mock registered on purpose."""
    result = cli_runner.invoke(drafts_app, ["set-cover", "123", cover_image_file])

    assert result.exit_code == 1
    assert "write" in result.output.lower()
    assert "SUBSTACK_ENABLE_WRITE" in result.output
