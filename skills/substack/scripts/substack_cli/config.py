"""Substack CLI — local config file storage (~/.config/substack-cli/config.json)."""
import json
from pathlib import Path

from substack_cli.app import config_app

CONFIG_PATH = Path.home() / ".config" / "substack-cli" / "config.json"


def load_config() -> dict:
    """Returns {} if file missing or invalid JSON. Never raises."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_config(data: dict) -> None:
    """Merges `data` into existing config (does not blindly overwrite
    unrelated keys). Creates parent dirs as needed."""
    existing = load_config()
    existing.update(data)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)


def set_value(key: str, value) -> None:
    """load -> set one key -> save."""
    data = load_config()
    data[key] = value
    save_config(data)


def show_config(redact_keys: tuple = ("cookies_string",)) -> dict:
    """Returns a copy with redact_keys' values replaced by
    "<first 8 chars>...[REDACTED]" — never the raw secret."""
    data = dict(load_config())
    for key in redact_keys:
        if key in data and isinstance(data[key], str):
            data[key] = f"{data[key][:8]}...[REDACTED]"
    return data


# --- CLI commands, registered on config_app (imported from app.py) ---


@config_app.command("set-cookies")
def set_cookies_cmd(cookies: str):
    """Store the Substack cookies string in the config file."""
    set_value("cookies_string", cookies)
    print("Cookies saved.")


@config_app.command("set-cookies-file")
def set_cookies_file_cmd(path: str):
    """Read cookies from a file (stripping whitespace) and store them."""
    with open(path, "r", encoding="utf-8") as f:
        cookies = f.read().strip()
    set_value("cookies_string", cookies)
    print("Cookies saved.")


@config_app.command("set-publication")
def set_publication_cmd(url: str):
    """Store the publication URL in the config file."""
    set_value("publication_url", url)
    print("Publication URL saved.")


@config_app.command("show")
def show_cmd(pretty: bool = False):
    """Print the current config, with sensitive values redacted.

    NOTE: client.py doesn't exist yet (Wave 1) — this uses print(json.dumps(...))
    directly. Wave 4 will refine this to use the shared output() helper from
    client.py once the CLI layer is wired up.
    """
    data = show_config()
    if pretty:
        print(json.dumps(data, indent=2))
    else:
        print(json.dumps(data))
