"""Tests for subscribers CSV export: trigger_csv_export, poll_csv_export,
download_csv, and the `subscribers export` CLI command.

All HTTP calls are mocked with respx — no real network traffic.
"""
import json
import os
import time

import httpx
import pytest
import respx

from substack_cli.client import SubstackApiError, SubstackClient
from substack_cli.read import (
    download_csv,
    poll_csv_export,
    trigger_csv_export,
)

# ---------------------------------------------------------------------------
# trigger_csv_export
# ---------------------------------------------------------------------------


@respx.mock
def test_trigger_csv_export_issues_post_with_empty_body(fake_cookies, fake_publication_url):
    """trigger_csv_export must POST to /api/v1/publication_export with {}."""
    route = respx.post(f"{fake_publication_url}/api/v1/publication_export").mock(
        return_value=httpx.Response(200, json={"id": 42, "status": "pending"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = trigger_csv_export(client)

    assert route.called
    assert route.calls[0].request.method == "POST"
    body = json.loads(route.calls[0].request.content)
    assert body == {}  # empty body per spec
    assert result == {"id": 42, "status": "pending"}


@respx.mock
def test_trigger_csv_export_raises_on_api_error(fake_cookies, fake_publication_url):
    """A 400/500 from the trigger endpoint surfaces as SubstackApiError."""
    respx.post(f"{fake_publication_url}/api/v1/publication_export").mock(
        return_value=httpx.Response(400, json={"error": "bad request"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError) as exc_info:
        trigger_csv_export(client)
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# poll_csv_export — success paths
# ---------------------------------------------------------------------------


@respx.mock
def test_poll_csv_export_returns_immediately_when_already_complete(fake_cookies, fake_publication_url):
    """If the first poll already shows a complete job, return it immediately."""
    complete_job = {"id": 1, "status": "complete", "download_url": "https://example.com/export.csv"}
    respx.get(f"{fake_publication_url}/api/v1/publication_export").mock(
        return_value=httpx.Response(200, json=[complete_job])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = poll_csv_export(client, timeout_s=10.0)
    assert result["download_url"] == "https://example.com/export.csv"
    assert result["status"] == "complete"


@respx.mock
def test_poll_csv_export_handles_envelope_response(fake_cookies, fake_publication_url):
    """poll_csv_export tolerates {exports: [...]} envelope shape."""
    complete_job = {"id": 7, "status": "complete", "download_url": "https://s3.example.com/file.csv"}
    respx.get(f"{fake_publication_url}/api/v1/publication_export").mock(
        return_value=httpx.Response(200, json={"exports": [complete_job]})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = poll_csv_export(client, timeout_s=10.0)
    assert result["id"] == 7


@respx.mock
def test_poll_csv_export_handles_single_job_dict(fake_cookies, fake_publication_url):
    """poll_csv_export tolerates a single-job dict (not wrapped in a list)."""
    complete_job = {"id": 99, "status": "complete", "download_url": "https://cdn.example.com/x.csv"}
    respx.get(f"{fake_publication_url}/api/v1/publication_export").mock(
        return_value=httpx.Response(200, json=complete_job)
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = poll_csv_export(client, timeout_s=10.0)
    assert result["download_url"] == "https://cdn.example.com/x.csv"


@respx.mock
def test_poll_csv_export_picks_most_recent_when_multiple_complete(fake_cookies, fake_publication_url):
    """When multiple complete jobs exist, the one with the highest id wins."""
    jobs = [
        {"id": 3, "status": "complete", "download_url": "https://s3.example.com/old.csv"},
        {"id": 9, "status": "complete", "download_url": "https://s3.example.com/new.csv"},
    ]
    respx.get(f"{fake_publication_url}/api/v1/publication_export").mock(
        return_value=httpx.Response(200, json=jobs)
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = poll_csv_export(client, timeout_s=10.0)
    assert result["id"] == 9
    assert "new.csv" in result["download_url"]


@respx.mock
def test_poll_csv_export_waits_through_pending_then_succeeds(
    fake_cookies, fake_publication_url, monkeypatch
):
    """poll_csv_export retries until status becomes complete."""
    call_count = 0

    def _mock_response(request):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return httpx.Response(200, json=[{"id": 1, "status": "pending"}])
        return httpx.Response(
            200, json=[{"id": 1, "status": "complete", "download_url": "https://s3.example.com/done.csv"}]
        )

    respx.get(f"{fake_publication_url}/api/v1/publication_export").mock(side_effect=_mock_response)

    # Patch sleep so the test runs instantly
    import substack_cli.read as read_module
    monkeypatch.setattr(read_module.time, "sleep", lambda *_: None)

    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = poll_csv_export(client, timeout_s=60.0)
    assert call_count == 3
    assert result["status"] == "complete"
    assert result["download_url"] == "https://s3.example.com/done.csv"


# ---------------------------------------------------------------------------
# poll_csv_export — failure paths
# ---------------------------------------------------------------------------


@respx.mock
def test_poll_csv_export_raises_on_failed_job(fake_cookies, fake_publication_url):
    """If the only job has status=failed, raise immediately."""
    respx.get(f"{fake_publication_url}/api/v1/publication_export").mock(
        return_value=httpx.Response(200, json=[{"id": 5, "status": "failed"}])
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError) as exc_info:
        poll_csv_export(client, timeout_s=10.0)
    msg = str(exc_info.value).lower()
    assert "failed" in msg


@respx.mock
def test_poll_csv_export_raises_on_timeout(fake_cookies, fake_publication_url, monkeypatch):
    """If timeout elapses before completion, raise with a clear message."""
    respx.get(f"{fake_publication_url}/api/v1/publication_export").mock(
        return_value=httpx.Response(200, json=[{"id": 2, "status": "pending"}])
    )

    # Make time.time() advance past the deadline after the first poll.
    import substack_cli.read as read_module

    original_time = time.time
    call_count = 0

    def _fast_time():
        nonlocal call_count
        call_count += 1
        # First few calls return "now"; after 3 calls, jump past the deadline.
        if call_count > 3:
            return original_time() + 9999
        return original_time()

    monkeypatch.setattr(read_module.time, "time", _fast_time)
    monkeypatch.setattr(read_module.time, "sleep", lambda *_: None)

    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError) as exc_info:
        poll_csv_export(client, timeout_s=1.0)
    msg = str(exc_info.value).lower()
    assert "timeout" in msg or "did not complete" in msg


@respx.mock
def test_poll_csv_export_raises_on_unexpected_shape(fake_cookies, fake_publication_url):
    """A non-dict, non-list response raises SubstackApiError."""
    respx.get(f"{fake_publication_url}/api/v1/publication_export").mock(
        return_value=httpx.Response(200, text="not json at all")
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    # The client will parse the text as a string; poll_csv_export should reject it.
    with pytest.raises(SubstackApiError):
        poll_csv_export(client, timeout_s=1.0)


# ---------------------------------------------------------------------------
# download_csv
# ---------------------------------------------------------------------------


@respx.mock
def test_download_csv_fetches_url_and_returns_bytes(fake_cookies, fake_publication_url):
    """download_csv GETs the download_url and returns the raw bytes."""
    csv_content = b"email,name\ntest@example.com,Test User\n"
    # The download URL is an absolute external URL — mock it directly.
    respx.get("https://s3.example.com/subscribers.csv").mock(
        return_value=httpx.Response(200, content=csv_content, headers={"content-type": "text/csv"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = download_csv(client, "https://s3.example.com/subscribers.csv")
    assert result == csv_content


@respx.mock
def test_download_csv_raises_on_404(fake_cookies, fake_publication_url):
    """A 404 from the download URL surfaces as SubstackApiError."""
    respx.get("https://s3.example.com/expired.csv").mock(
        return_value=httpx.Response(404, text="Not Found")
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    with pytest.raises(SubstackApiError) as exc_info:
        download_csv(client, "https://s3.example.com/expired.csv")
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# CLI integration — `subscribers export` command
# ---------------------------------------------------------------------------


@respx.mock
def test_subscribers_export_cmd_writes_csv_to_stdout(
    fake_cookies, fake_publication_url, authed_env, cli_runner
):
    """End-to-end: trigger → poll → download → stdout."""
    csv_bytes = b"email,name\nalice@example.com,Alice\nbob@example.com,Bob\n"

    respx.post(f"{fake_publication_url}/api/v1/publication_export").mock(
        return_value=httpx.Response(200, json={"id": 10, "status": "pending"})
    )
    respx.get(f"{fake_publication_url}/api/v1/publication_export").mock(
        return_value=httpx.Response(
            200, json=[{"id": 10, "status": "complete", "download_url": "https://s3.example.com/sub.csv"}]
        )
    )
    respx.get("https://s3.example.com/sub.csv").mock(
        return_value=httpx.Response(200, content=csv_bytes, headers={"content-type": "text/csv"})
    )

    from substack_cli.app import app
    result = cli_runner.invoke(app, ["subscribers", "export"])
    assert result.exit_code == 0
    assert b"alice@example.com" in result.stdout_bytes


@respx.mock
def test_subscribers_export_cmd_writes_csv_to_file(
    fake_cookies, fake_publication_url, authed_env, cli_runner, tmp_path
):
    """--output FILE writes the CSV to disk and emits JSON confirmation."""
    csv_bytes = b"email,name\ncharlie@example.com,Charlie\n"
    out_file = str(tmp_path / "subscribers.csv")

    respx.post(f"{fake_publication_url}/api/v1/publication_export").mock(
        return_value=httpx.Response(200, json={"id": 11, "status": "pending"})
    )
    respx.get(f"{fake_publication_url}/api/v1/publication_export").mock(
        return_value=httpx.Response(
            200, json=[{"id": 11, "status": "complete", "download_url": "https://s3.example.com/c.csv"}]
        )
    )
    respx.get("https://s3.example.com/c.csv").mock(
        return_value=httpx.Response(200, content=csv_bytes, headers={"content-type": "text/csv"})
    )

    from substack_cli.app import app
    result = cli_runner.invoke(app, ["subscribers", "export", "--output", out_file])
    assert result.exit_code == 0
    # JSON confirmation on stdout (first non-empty line — progress lines go to stderr
    # but Typer's CliRunner merges stderr+stdout by default)
    first_json_line = next(
        line for line in result.output.splitlines() if line.startswith("{")
    )
    data = json.loads(first_json_line)
    assert data["status"] == "ok"
    assert data["bytes"] == len(csv_bytes)
    # File written on disk
    assert os.path.exists(out_file)
    with open(out_file, "rb") as f:
        assert f.read() == csv_bytes


@respx.mock
def test_subscribers_export_cmd_exits_1_on_trigger_failure(
    fake_cookies, fake_publication_url, authed_env, cli_runner
):
    """A 500 from the trigger POST causes exit code 1 with error JSON to stderr."""
    respx.post(f"{fake_publication_url}/api/v1/publication_export").mock(
        return_value=httpx.Response(500, json={"error": "internal server error"})
    )

    from substack_cli.app import app
    result = cli_runner.invoke(app, ["subscribers", "export"])
    assert result.exit_code == 1
