"""Tests for substack_cli.publish — image upload (JSON + base64, not multipart).

No test in this suite performs a real network call; every Substack API
interaction is mocked with respx.
"""
import json

import httpx
import pytest
import respx

from substack_cli.publish import upload_image
from substack_cli.client import SubstackClient


@pytest.fixture
def tiny_image_file(tmp_path):
    """A small fake PNG file — content doesn't need to be a real decodable
    image, upload_image only needs to read+base64-encode bytes."""
    path = tmp_path / "tiny.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return str(path)


@respx.mock
def test_upload_image_sends_json_base64_not_multipart(
    fake_cookies, fake_publication_url, tiny_image_file
):
    route = respx.post(f"{fake_publication_url}/api/v1/image").mock(
        return_value=httpx.Response(
            200,
            json={
                "bytes": 12345,
                "imageWidth": 800,
                "imageHeight": 600,
                "url": "https://substack-post-media.s3.amazonaws.com/fake.png",
            },
        )
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)

    upload_image(client, tiny_image_file)

    request = route.calls[0].request
    content_type = request.headers.get("content-type", "")
    assert "application/json" in content_type
    assert "multipart" not in content_type

    sent_body = json.loads(request.content)
    assert "data:image" in sent_body["image"]
    assert "base64" in sent_body["image"]


@respx.mock
def test_upload_image_reads_bytes_imagewidth_imageheight_url_fields(
    fake_cookies, fake_publication_url, tiny_image_file
):
    expected = {
        "bytes": 12345,
        "imageWidth": 800,
        "imageHeight": 600,
        "url": "https://substack-post-media.s3.amazonaws.com/fake.png",
    }
    respx.post(f"{fake_publication_url}/api/v1/image").mock(
        return_value=httpx.Response(200, json=expected)
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)

    result = upload_image(client, tiny_image_file)

    assert result == expected


def test_upload_image_rejects_missing_file_with_clear_usage_error(
    fake_cookies, fake_publication_url
):
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)

    with pytest.raises((FileNotFoundError, ValueError)) as exc_info:
        upload_image(client, "/nonexistent/path.png")

    assert "/nonexistent/path.png" in str(exc_info.value)
