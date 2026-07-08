"""Tests for substack_cli.client — HTTP transport, retries, rate limiting, errors."""
import json
import sys

import httpx
import pytest
import respx

from substack_cli.client import (
    SubstackApiError,
    SubstackClient,
    emit_error,
    output,
    output_list,
    SUBSTACK_COM,
)


# ---------------------------------------------------------------------------
# Basic GET behavior
# ---------------------------------------------------------------------------

@respx.mock
def test_get_sends_cookie_header_with_resolved_value(fake_cookies, fake_publication_url):
    route = respx.get(f"{fake_publication_url}/api/v1/archive").mock(
        return_value=httpx.Response(200, json=[{"id": 1}])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    client.get("/api/v1/archive")
    sent_cookies = route.calls[0].request.headers.get("cookie", "")
    assert fake_cookies in sent_cookies


@respx.mock
def test_get_sends_non_default_user_agent(fake_cookies, fake_publication_url):
    route = respx.get(f"{fake_publication_url}/api/v1/archive").mock(
        return_value=httpx.Response(200, json=[])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    client.get("/api/v1/archive")
    ua = route.calls[0].request.headers.get("user-agent", "")
    assert "Chrome" in ua
    assert "python-httpx" not in ua


@respx.mock
def test_get_returns_parsed_json_on_200(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/api/v1/archive").mock(
        return_value=httpx.Response(200, json={"key": "value"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = client.get("/api/v1/archive")
    assert result == {"key": "value"}


@respx.mock
def test_get_uses_publication_subdomain_by_default(fake_cookies, fake_publication_url):
    route = respx.get(f"{fake_publication_url}/api/v1/archive").mock(
        return_value=httpx.Response(200, json=[])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    client.get("/api/v1/archive")
    assert route.called
    assert route.calls[0].request.url.host == "charliepgarcia.substack.com"


@respx.mock
def test_get_host_a_hits_substack_dot_com(fake_cookies, fake_publication_url):
    route = respx.get(f"{SUBSTACK_COM}/api/v1/categories").mock(
        return_value=httpx.Response(200, json=[])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    client.get("/api/v1/categories", host="A")
    assert route.called
    assert route.calls[0].request.url.host == "substack.com"


# ---------------------------------------------------------------------------
# POST / PUT / DELETE
# ---------------------------------------------------------------------------

@respx.mock
def test_post_sends_content_type_json_and_serialized_body(fake_cookies, fake_publication_url):
    route = respx.post(f"{fake_publication_url}/api/v1/drafts").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    client.post("/api/v1/drafts", json_body={"title": "Test"})
    ct = route.calls[0].request.headers.get("content-type", "")
    assert "application/json" in ct
    body = json.loads(route.calls[0].request.content)
    assert body == {"title": "Test"}


@respx.mock
def test_put_uses_put_method(fake_cookies, fake_publication_url):
    route = respx.put(f"{fake_publication_url}/api/v1/drafts/123").mock(
        return_value=httpx.Response(200, json={"id": 123})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    client.put("/api/v1/drafts/123", json_body={"title": "Updated"})
    assert route.called
    assert route.calls[0].request.method == "PUT"


@respx.mock
def test_delete_sends_no_body(fake_cookies, fake_publication_url):
    route = respx.delete(f"{fake_publication_url}/api/v1/drafts/123").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    client.delete("/api/v1/drafts/123")
    assert route.called
    assert route.calls[0].request.method == "DELETE"
    # Content should be empty or absent
    assert not route.calls[0].request.content or route.calls[0].request.content == b""


# ---------------------------------------------------------------------------
# Retry / backoff
# ---------------------------------------------------------------------------

@respx.mock
def test_429_triggers_retry_then_succeeds(fake_cookies, fake_publication_url, no_sleep):
    route = respx.get(f"{fake_publication_url}/api/v1/archive").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = client.get("/api/v1/archive")
    assert route.call_count == 2
    assert result == {"ok": True}


@respx.mock
def test_429_exhausting_retries_raises_substack_api_error_with_429(
    fake_cookies, fake_publication_url, no_sleep
):
    respx.get(f"{fake_publication_url}/api/v1/archive").mock(
        return_value=httpx.Response(429)
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url, max_retries=2)
    with pytest.raises(SubstackApiError) as exc_info:
        client.get("/api/v1/archive")
    assert exc_info.value.status_code == 429


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@respx.mock
def test_401_raises_with_cookie_expiry_hint_in_message(fake_cookies, fake_publication_url):
    from substack_cli.auth import cookie_expiry_hint

    respx.get(f"{fake_publication_url}/api/v1/drafts").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError) as exc_info:
        client.get("/api/v1/drafts")
    hint = cookie_expiry_hint(401)
    assert hint is not None
    assert hint in str(exc_info.value)


@respx.mock
def test_403_raises_without_claiming_route_does_not_exist(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/api/v1/drafts").mock(
        return_value=httpx.Response(403, json={"error": "forbidden"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError) as exc_info:
        client.get("/api/v1/drafts")
    msg = str(exc_info.value).lower()
    assert "not found" not in msg
    assert "doesn't exist" not in msg
    assert "does not exist" not in msg


@respx.mock
def test_404_raises_with_not_found_framing(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/api/v1/nonexistent").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError) as exc_info:
        client.get("/api/v1/nonexistent")
    msg = str(exc_info.value).lower()
    assert "not found" in msg


def test_rate_limit_throttle_sleeps_between_consecutive_calls(
    fake_cookies, fake_publication_url, monkeypatch
):
    """Assert time.sleep is called with ~min_interval between calls."""
    import substack_cli.client as client_module

    sleep_calls: list = []
    monkeypatch.setattr(
        client_module.time, "sleep", lambda secs, *_a, **_kw: sleep_calls.append(secs)
    )

    with respx.mock:
        respx.get(f"{fake_publication_url}/api/v1/archive").mock(
            return_value=httpx.Response(200, json=[])
        )
        client = SubstackClient(
            cookies=fake_cookies,
            publication_url=fake_publication_url,
            min_interval=0.5,
        )
        client.get("/api/v1/archive")
        client.get("/api/v1/archive")

    # At least one sleep call should be ~min_interval (0.5)
    assert any(abs(s - 0.5) < 0.15 for s in sleep_calls), f"sleep calls: {sleep_calls}"


@respx.mock
def test_cookie_value_never_appears_in_raised_exception_string(fake_publication_url):
    secret_cookie = "connect.sid=SECRET_TOKEN_AAA111BBB222"
    respx.get(f"{fake_publication_url}/api/v1/archive").mock(
        return_value=httpx.Response(
            500, json={"error": "SECRET_TOKEN_AAA111BBB222 leaked"}
        )
    )
    client = SubstackClient(cookies=secret_cookie, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError) as exc_info:
        client.get("/api/v1/archive")
    assert "SECRET_TOKEN_AAA111BBB222" not in str(exc_info.value)
    assert "SECRET_TOKEN_AAA111BBB222" not in str(exc_info.value.body) if exc_info.value.body else True


@respx.mock
def test_malformed_non_json_error_body_falls_back_to_raw_text(
    fake_cookies, fake_publication_url
):
    respx.get(f"{fake_publication_url}/api/v1/archive").mock(
        return_value=httpx.Response(
            500,
            content=b"<html>Internal Server Error</html>",
            headers={"content-type": "text/html"},
        )
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError) as exc_info:
        client.get("/api/v1/archive")
    assert "Internal Server Error" in str(exc_info.value)


# ---------------------------------------------------------------------------
# emit_error / output / output_list
# ---------------------------------------------------------------------------

def test_emit_error_prints_json_to_stderr_and_exits_1(capsys):
    with pytest.raises(SystemExit) as exc_info:
        emit_error("test error", status_code=500)
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    err_json = json.loads(captured.err.strip())
    assert err_json["error"] is True
    assert err_json["message"] == "test error"
    assert err_json["status_code"] == 500


def test_output_pretty_false_prints_compact_json_to_stdout(capsys):
    output({"key": "value"}, pretty=False)
    captured = capsys.readouterr()
    parsed = json.loads(captured.out.strip())
    assert parsed == {"key": "value"}


def test_output_list_normalizes_envelope_before_pretty_printing(capsys):
    output_list({"items": [1, 2, 3]}, pretty=False, title="Test")
    captured = capsys.readouterr()
    parsed = json.loads(captured.out.strip())
    assert parsed == [1, 2, 3]
