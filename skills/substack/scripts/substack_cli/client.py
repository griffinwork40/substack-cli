"""Substack CLI — HTTP client: transport, retries, rate limiting, errors."""
import json
import sys
import time
from typing import Any, Literal, Optional

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from substack_cli.auth import build_headers, cookie_expiry_hint, redact
from substack_cli.models import extract_list, extract_pagination_meta

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUBSTACK_COM = "https://substack.com"  # host "A"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MIN_INTERVAL = 1.0
DEFAULT_MAX_RETRIES = 3

_console = Console(stderr=True)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SubstackApiError(Exception):
    """Raised when the Substack API returns an error response."""

    def __init__(
        self, message: str, status_code: Optional[int] = None, body: Any = None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


# ---------------------------------------------------------------------------
# SubstackClient
# ---------------------------------------------------------------------------


class SubstackClient:
    """HTTP transport for the Substack unofficial API.

    Handles two-host routing (publication subdomain vs substack.com),
    request throttling, retry with exponential backoff on 429,
    and cookie-value redaction in all error messages.
    """

    def __init__(
        self,
        cookies: str,
        publication_url: str,
        timeout: float = DEFAULT_TIMEOUT,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        self._cookies = cookies
        # Ensure publication_url has https:// prefix
        if not publication_url.startswith("http"):
            publication_url = "https://" + publication_url
        self._publication_url = publication_url.rstrip("/")
        self._timeout = timeout
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._last_request_time: float = 0.0

        self._httpx_client = httpx.Client(
            headers=build_headers(cookies),
            timeout=timeout,
        )

    # -- private helpers ---------------------------------------------------

    def _base_url(self, host: str) -> str:
        if host == "A":
            return SUBSTACK_COM
        return self._publication_url

    def _throttle(self) -> None:
        """Sleep if we're within min_interval of the last request."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    def _redact_message(self, message: str) -> str:
        """Scrub cookie values from an error message."""
        return redact(message, self._cookies)

    def _parse_error_body(self, response: httpx.Response) -> Any:
        """Try to parse the response body as JSON, fall back to raw text."""
        try:
            return response.json()
        except Exception:
            return response.text

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Raise SubstackApiError for non-2xx responses."""
        status = response.status_code
        if status < 400:
            return

        body = self._parse_error_body(response)
        body_str = body if isinstance(body, str) else json.dumps(body)
        # Build the base message
        message = f"Substack API error {status}: {body_str}"

        # Append cookie expiry hint for 401/403
        hint = cookie_expiry_hint(status)
        if hint:
            message = f"{message}\n{hint}"
        elif status == 404:
            message = f"Substack API error: {response.url.path} not found (404). {body_str}"

        # Redact any cookie values that may have leaked into the message
        message = self._redact_message(message)

        # Redact body too — API error responses can echo cookie values
        if isinstance(body, str):
            redacted_body: Any = self._redact_message(body)
        else:
            redacted_body_str = self._redact_message(json.dumps(body))
            try:
                redacted_body = json.loads(redacted_body_str)
            except Exception:
                redacted_body = redacted_body_str

        raise SubstackApiError(message, status_code=status, body=redacted_body)

    def _request(
        self,
        method: str,
        path: str,
        *,
        host: str = "P",
        json_body: Optional[dict] = None,
        **params: Any,
    ) -> Any:
        """Core request method with retry, throttle, and error handling."""
        base_url = self._base_url(host)
        url = f"{base_url}{path}"

        # Defensive copy — never mutate caller's dicts
        if json_body is not None:
            json_body = dict(json_body)

        attempts = 0
        max_attempts = self._max_retries + 1  # initial + retries

        while True:
            attempts += 1
            self._throttle()

            try:
                if method == "GET":
                    response = self._httpx_client.get(
                        url, params=params or None
                    )
                elif method == "POST":
                    response = self._httpx_client.post(
                        url, params=params or None, json=json_body
                    )
                elif method == "PUT":
                    response = self._httpx_client.put(
                        url, params=params or None, json=json_body
                    )
                elif method == "DELETE":
                    response = self._httpx_client.delete(
                        url, params=params or None
                    )
                else:
                    raise ValueError(f"Unsupported method: {method}")
            except httpx.HTTPError as exc:
                msg = self._redact_message(str(exc))
                if attempts < max_attempts:
                    time.sleep(2 ** (attempts - 1))
                    continue
                raise SubstackApiError(msg, status_code=None) from exc

            self._last_request_time = time.time()

            # Retry on 429
            if response.status_code == 429 and attempts < max_attempts:
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        delay = 2 ** (attempts - 1)
                else:
                    delay = 2 ** (attempts - 1)
                time.sleep(delay)
                continue

            self._raise_for_status(response)

            # Success — parse JSON if content-type allows, else raw text
            content_type = response.headers.get("content-type", "")
            if "json" in content_type:
                return response.json()
            # Try JSON anyway (some endpoints don't set content-type correctly)
            try:
                return response.json()
            except Exception:
                return response.text

    # -- public verbs ------------------------------------------------------

    def get(
        self, path: str, *, host: Literal["A", "P"] = "P", **params: Any
    ) -> Any:
        return self._request("GET", path, host=host, **params)

    def post(
        self,
        path: str,
        *,
        host: Literal["A", "P"] = "P",
        json_body: Optional[dict] = None,
        **params: Any,
    ) -> Any:
        return self._request("POST", path, host=host, json_body=json_body, **params)

    def put(
        self,
        path: str,
        *,
        host: Literal["A", "P"] = "P",
        json_body: Optional[dict] = None,
        **params: Any,
    ) -> Any:
        return self._request("PUT", path, host=host, json_body=json_body, **params)

    def delete(
        self, path: str, *, host: Literal["A", "P"] = "P", **params: Any
    ) -> Any:
        return self._request("DELETE", path, host=host, **params)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def emit_error(
    message: str, *, status_code: Optional[int] = None, pretty: bool = False
) -> None:
    """Print a JSON error to stderr and exit 1.

    Under --pretty, also render a red Rich panel to stderr for human
    readability, but the machine-parseable JSON is always emitted.
    """
    err_obj = {"error": True, "message": message, "status_code": status_code}
    sys.stderr.write(json.dumps(err_obj, ensure_ascii=False) + "\n")

    if pretty:
        _console.print(
            Panel(
                f"[red]{message}[/red]"
                + (f"\nStatus: {status_code}" if status_code else ""),
                title="[red]Error[/red]",
                border_style="red",
            )
        )

    sys.exit(1)


def output(data: Any, pretty: bool) -> None:
    """Print data as compact JSON (default) or Rich panel (--pretty)."""
    if pretty:
        if isinstance(data, dict):
            _console.print(Panel(json.dumps(data, indent=2, ensure_ascii=False), title="Result"))
        elif isinstance(data, list):
            _console.print(Panel(json.dumps(data, indent=2, ensure_ascii=False), title="Result"))
        else:
            _console.print(Panel(str(data), title="Result"))
    else:
        print(json.dumps(data, ensure_ascii=False))


def output_list(data: Any, pretty: bool, title: str = "Results") -> None:
    """Normalize an envelope via extract_list, then output."""
    items = extract_list(data, "posts", "comments", "items", "results", "data")
    if pretty:
        if items and isinstance(items[0], dict):
            table = Table(title=title)
            # Use keys from the first item as columns
            for key in items[0]:
                table.add_column(key, overflow="fold")
            for row in items:
                table.add_row(*[str(row.get(k, "")) for k in items[0]])
            _console.print(table)
        else:
            _console.print(Panel(json.dumps(items, indent=2, ensure_ascii=False), title=title))
    else:
        print(json.dumps(items, ensure_ascii=False))
