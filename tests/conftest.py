"""Shared pytest fixtures for the substack_cli test suite.

No test in this suite may perform a real network call or touch the real
~/.config/substack-cli/config.json. Every fixture below exists to enforce
that isolation.
"""

import pytest


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Redirects config.CONFIG_PATH to a tmp file and clears SUBSTACK_* env
    vars for the duration of the test. Yields the tmp Path (not yet created
    on disk — tests exercise save_config() to create it).
    """
    from substack_cli import config as config_module
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config_module, "CONFIG_PATH", cfg_path)
    monkeypatch.delenv("SUBSTACK_COOKIES_STRING", raising=False)
    monkeypatch.delenv("SUBSTACK_PUBLICATION_URL", raising=False)
    monkeypatch.delenv("SUBSTACK_ENABLE_WRITE", raising=False)
    yield cfg_path


@pytest.fixture
def fake_cookies() -> str:
    return "connect.sid=s%3Afaketoken1234567890abcdef; substack.sid=faketoken2222222222"


@pytest.fixture
def fake_publication_url() -> str:
    return "https://charliepgarcia.substack.com"


@pytest.fixture
def authed_env(monkeypatch, fake_cookies, fake_publication_url):
    """Sets SUBSTACK_COOKIES_STRING + SUBSTACK_PUBLICATION_URL so
    resolve_cookies()/resolve_publication_url() succeed without touching
    the config file. Does NOT set SUBSTACK_ENABLE_WRITE — tests that need
    write access must opt in separately via `write_enabled_env`.
    """
    monkeypatch.setenv("SUBSTACK_COOKIES_STRING", fake_cookies)
    monkeypatch.setenv("SUBSTACK_PUBLICATION_URL", fake_publication_url)


@pytest.fixture
def write_enabled_env(monkeypatch):
    monkeypatch.setenv("SUBSTACK_ENABLE_WRITE", "true")


@pytest.fixture
def cli_runner():
    from typer.testing import CliRunner
    return CliRunner()


@pytest.fixture
def no_sleep(monkeypatch):
    """Neutralizes time.sleep so retry/backoff/throttle tests run instantly.
    Patches the `time` module as imported inside substack_cli.client.
    """
    import substack_cli.client as client_module
    monkeypatch.setattr(client_module.time, "sleep", lambda *_args, **_kwargs: None)
