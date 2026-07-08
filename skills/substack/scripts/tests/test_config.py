"""Tests for substack_cli.config — local config file storage."""
import json

from substack_cli import config as config_module
from substack_cli.app import config_app


def test_load_config_returns_empty_dict_when_file_missing(isolated_config):
    assert config_module.load_config() == {}


def test_load_config_returns_empty_dict_on_invalid_json(isolated_config):
    cfg_path = isolated_config
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("not valid json {{{", encoding="utf-8")

    assert config_module.load_config() == {}


def test_save_config_creates_parent_directories(isolated_config, monkeypatch):
    # isolated_config points CONFIG_PATH at tmp_path/config.json, whose parent
    # (tmp_path) already exists courtesy of pytest. Point at a deeper, not-yet-
    # created nested path so parent-directory creation is actually exercised.
    nested_path = isolated_config.parent / "nested" / "dir" / "config.json"
    monkeypatch.setattr(config_module, "CONFIG_PATH", nested_path)
    assert not nested_path.parent.exists()

    config_module.save_config({"a": 1})

    assert nested_path.parent.exists()
    assert nested_path.exists()


def test_save_config_merges_not_overwrites(isolated_config):
    config_module.save_config({"a": 1})
    config_module.save_config({"b": 2})

    data = config_module.load_config()

    assert data["a"] == 1
    assert data["b"] == 2


def test_set_value_persists_and_is_loadable(isolated_config):
    config_module.set_value("publication_url", "https://example.com")

    data = config_module.load_config()

    assert data["publication_url"] == "https://example.com"


def test_show_config_redacts_cookies_string_value(isolated_config, fake_cookies):
    config_module.save_config({"cookies_string": fake_cookies})

    shown = config_module.show_config()

    assert shown["cookies_string"] != fake_cookies
    assert "REDACTED" in shown["cookies_string"]
    assert shown["cookies_string"].startswith(fake_cookies[:8])


def test_show_config_leaves_non_sensitive_keys_untouched(isolated_config, fake_publication_url):
    config_module.save_config({"publication_url": fake_publication_url})

    shown = config_module.show_config()

    assert shown["publication_url"] == fake_publication_url


def test_config_set_cookies_cli_command_writes_file(isolated_config, cli_runner):
    cfg_path = isolated_config

    result = cli_runner.invoke(config_app, ["set-cookies", "fakecookies"])

    assert result.exit_code == 0
    assert cfg_path.exists()
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["cookies_string"] == "fakecookies"


def test_config_set_publication_cli_command_writes_file(isolated_config, cli_runner):
    cfg_path = isolated_config

    result = cli_runner.invoke(config_app, ["set-publication", "https://example.com"])

    assert result.exit_code == 0
    assert cfg_path.exists()
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["publication_url"] == "https://example.com"


def test_config_show_cli_command_outputs_redacted_json(isolated_config, cli_runner, fake_cookies):
    config_module.save_config({"cookies_string": fake_cookies})

    result = cli_runner.invoke(config_app, ["show"])

    assert result.exit_code == 0
    assert "REDACTED" in result.output
    assert fake_cookies not in result.output
