"""Tests for substack_cli.auth — credential resolution, headers, redaction."""
import pytest

from substack_cli import auth as auth_module
from substack_cli import config as config_module


# --- resolve_cookies ---


def test_resolve_cookies_env_var_takes_priority_over_config(isolated_config, monkeypatch):
    monkeypatch.setenv(auth_module.ENV_COOKIES, "env-cookie-value")
    config_module.save_config({"cookies_string": "config-cookie-value"})

    assert auth_module.resolve_cookies() == "env-cookie-value"


def test_resolve_cookies_falls_back_to_config_file(isolated_config):
    config_module.save_config({"cookies_string": "config-cookie-value"})

    assert auth_module.resolve_cookies() == "config-cookie-value"


def test_resolve_cookies_raises_auth_error_when_neither_present(isolated_config):
    with pytest.raises(auth_module.AuthError) as excinfo:
        auth_module.resolve_cookies()

    message = str(excinfo.value)
    assert auth_module.ENV_COOKIES in message
    assert "config" in message.lower()


# --- resolve_cookies_optional ---


def test_resolve_cookies_optional_returns_empty_when_neither_present(isolated_config):
    assert auth_module.resolve_cookies_optional() == ""


def test_resolve_cookies_optional_env_var_takes_priority_over_config(isolated_config, monkeypatch):
    monkeypatch.setenv(auth_module.ENV_COOKIES, "env-cookie-value")
    config_module.save_config({"cookies_string": "config-cookie-value"})

    assert auth_module.resolve_cookies_optional() == "env-cookie-value"


def test_resolve_cookies_optional_falls_back_to_config_file(isolated_config):
    config_module.save_config({"cookies_string": "config-cookie-value"})

    assert auth_module.resolve_cookies_optional() == "config-cookie-value"


# --- resolve_publication_url ---


def test_resolve_publication_url_from_env_var(isolated_config, monkeypatch):
    monkeypatch.setenv(auth_module.ENV_PUBLICATION_URL, "https://charliepgarcia.substack.com")

    assert auth_module.resolve_publication_url() == "https://charliepgarcia.substack.com"


def test_resolve_publication_url_falls_back_to_config(isolated_config, fake_publication_url):
    config_module.save_config({"publication_url": fake_publication_url})

    assert auth_module.resolve_publication_url() == fake_publication_url


def test_resolve_publication_url_normalizes_bare_subdomain(isolated_config, monkeypatch):
    monkeypatch.setenv(auth_module.ENV_PUBLICATION_URL, "charliepgarcia")

    assert auth_module.resolve_publication_url() == "https://charliepgarcia.substack.com"


def test_resolve_publication_url_normalizes_full_url_to_bare_host(isolated_config, monkeypatch):
    monkeypatch.setenv(
        auth_module.ENV_PUBLICATION_URL,
        "https://charliepgarcia.substack.com/some/path",
    )

    assert auth_module.resolve_publication_url() == "https://charliepgarcia.substack.com"


def test_resolve_publication_url_raises_when_neither_present(isolated_config):
    with pytest.raises(auth_module.AuthError):
        auth_module.resolve_publication_url()


# --- is_write_enabled ---


def test_is_write_enabled_true_via_env(isolated_config, monkeypatch):
    monkeypatch.setenv(auth_module.ENV_ENABLE_WRITE, "true")

    assert auth_module.is_write_enabled() is True


def test_is_write_enabled_true_via_config(isolated_config):
    config_module.save_config({"enable_write": True})

    assert auth_module.is_write_enabled() is True


def test_is_write_enabled_false_by_default(isolated_config):
    assert auth_module.is_write_enabled() is False


# --- build_headers ---


def test_build_headers_includes_exact_cookie_value():
    headers = auth_module.build_headers("mycookie123")

    assert headers["Cookie"] == "mycookie123"


def test_build_headers_user_agent_is_not_default_httpx_ua():
    headers = auth_module.build_headers("mycookie123")

    assert "Chrome" in headers["User-Agent"]
    assert not headers["User-Agent"].startswith("python-httpx/")


# --- redact ---


def test_redact_scrubs_connect_sid_value():
    cookies = "connect.sid=s%3Alongsecretvalue123456"
    text = "connect.sid=s%3Alongsecretvalue123456; other"

    result = auth_module.redact(text, cookies)

    assert "longsecretvalue123456" not in result
    assert "[REDACTED]" in result


def test_redact_scrubs_multiple_crumbs_in_one_pass():
    cookies = "connect.sid=firstsecretvalue; substack.sid=secondsecretvalue"
    text = "logs mention firstsecretvalue and also secondsecretvalue in the body"

    result = auth_module.redact(text, cookies)

    assert "firstsecretvalue" not in result
    assert "secondsecretvalue" not in result
    assert result.count("[REDACTED]") == 2


def test_redact_is_noop_on_text_without_any_cookie_value():
    cookies = "connect.sid=somesecretvalue"
    text = "nothing sensitive is mentioned here at all"

    result = auth_module.redact(text, cookies)

    assert result == text


def test_redact_handles_empty_cookie_string_safely():
    text = "some plain text with no secrets"

    result = auth_module.redact(text, "")

    assert result == text


# --- cookie_expiry_hint ---


def test_cookie_expiry_hint_for_401():
    hint = auth_module.cookie_expiry_hint(401)

    assert hint is not None
    assert "re-extract" in hint.lower() or "cookie" in hint.lower()


def test_cookie_expiry_hint_for_403():
    hint = auth_module.cookie_expiry_hint(403)

    assert hint is not None
    assert "re-extract" in hint.lower() or "cookie" in hint.lower()


def test_cookie_expiry_hint_none_for_404():
    assert auth_module.cookie_expiry_hint(404) is None
