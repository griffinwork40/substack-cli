"""Substack CLI — credential resolution, header construction, redaction."""
import os
from typing import Optional

from substack_cli.config import load_config

ENV_COOKIES = "SUBSTACK_COOKIES_STRING"
ENV_PUBLICATION_URL = "SUBSTACK_PUBLICATION_URL"
ENV_ENABLE_WRITE = "SUBSTACK_ENABLE_WRITE"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class AuthError(Exception):
    """Raised when credentials/publication URL cannot be resolved."""
    pass


def resolve_cookies() -> str:
    """ENV_COOKIES -> config['cookies_string'] -> raise AuthError naming both paths."""
    value = os.environ.get(ENV_COOKIES)
    if value:
        return value

    value = load_config().get("cookies_string")
    if value:
        return value

    raise AuthError(
        f"No Substack cookies found. Set the {ENV_COOKIES} environment variable "
        f"or run `substack config set-cookies <cookies>` (persisted to the "
        f"config file) to provide them."
    )


def resolve_cookies_optional() -> str:
    """Like resolve_cookies(), but returns "" instead of raising when no
    cookies are configured. For anonymous/public endpoints (archive, post,
    feed) that work without auth — a cookie is used if present, but its
    absence is not an error."""
    value = os.environ.get(ENV_COOKIES)
    if value:
        return value

    value = load_config().get("cookies_string")
    if value:
        return value

    return ""


def _normalize_publication_host(value: str) -> str:
    """Normalize various input forms to a full hostname:
    - "charliepgarcia" -> "charliepgarcia.substack.com"
    - "charliepgarcia.substack.com" -> "charliepgarcia.substack.com"
    - "https://charliepgarcia.substack.com" -> "charliepgarcia.substack.com"
    - "https://charliepgarcia.substack.com/" -> "charliepgarcia.substack.com"
    """
    host = value.strip()
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0]
    if "." not in host:
        host = f"{host}.substack.com"
    return host


def resolve_publication_url() -> str:
    """ENV_PUBLICATION_URL -> config['publication_url'] -> raise AuthError.
    Normalizes bare subdomain / bare host / full URL to a bare hostname
    via _normalize_publication_host() (e.g. "charliepgarcia" ->
    "charliepgarcia.substack.com"), then returns a full https:// URL."""
    value = os.environ.get(ENV_PUBLICATION_URL)
    if not value:
        value = load_config().get("publication_url")

    if not value:
        raise AuthError(
            f"No publication URL found. Set the {ENV_PUBLICATION_URL} "
            f"environment variable or run `substack config set-publication "
            f"<url>` (persisted to the config file) to provide one."
        )

    return f"https://{_normalize_publication_host(value)}"


def is_write_enabled() -> bool:
    """ENV_ENABLE_WRITE == "true" (case-insensitive) OR config['enable_write'] is True."""
    env_value = os.environ.get(ENV_ENABLE_WRITE)
    if env_value is not None and env_value.strip().lower() == "true":
        return True

    return load_config().get("enable_write") is True


def build_headers(cookies: str) -> dict:
    """{"Cookie": cookies, "User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}"""
    return {
        "Cookie": cookies,
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json",
    }


def redact(text: str, cookies: str) -> str:
    """Splits `cookies` on ';', for each crumb "name=value" with len(value) >= 6,
    replaces every occurrence of `value` in `text` with "[REDACTED]".
    No-op on empty/short values."""
    if not text or not cookies:
        return text

    result = text
    for crumb in cookies.split(";"):
        crumb = crumb.strip()
        if not crumb or "=" not in crumb:
            continue
        _, _, value = crumb.partition("=")
        value = value.strip()
        if len(value) >= 6:
            result = result.replace(value, "[REDACTED]")
    return result


def cookie_expiry_hint(status_code: int) -> Optional[str]:
    """401 or 403 -> a remediation string noting Substack uses both codes
    inconsistently for "not authenticated" and suggesting re-extraction.
    Anything else -> None."""
    if status_code in (401, 403):
        return (
            f"Received HTTP {status_code}. Substack inconsistently uses both "
            "401 and 403 to mean \"not authenticated\" — your cookies have "
            "likely expired or been invalidated. Re-extract fresh cookies from "
            "a logged-in browser session and update them via "
            f"`substack config set-cookies` or the {ENV_COOKIES} environment "
            "variable."
        )
    return None
