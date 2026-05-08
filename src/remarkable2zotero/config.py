from __future__ import annotations

import os
from pathlib import Path

import yaml


class ConfigError(Exception):
    pass


DEFAULT_CONFIG_PATH = Path("~/.config/remarkable2zotero/config.yaml").expanduser()


def load_config(path: Path | None = None) -> dict:
    config_path = path or DEFAULT_CONFIG_PATH
    config_path = Path(config_path).expanduser()

    if not config_path.exists():
        raise ConfigError(
            f"Config file not found: {config_path}\n"
            f"Copy config.example.yaml to {config_path} and fill in your credentials."
        )

    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    _apply_env_overrides(config)
    _validate(config)
    return config


def _apply_env_overrides(config: dict) -> None:
    env_map = {
        "R2Z_REMARKABLE_HOST": ("remarkable", "host"),
        "R2Z_REMARKABLE_PASSWORD": ("remarkable", "password"),
        "R2Z_ZOTERO_API_KEY": ("zotero", "api_key"),
        "R2Z_ZOTERO_GROUP_ID": ("zotero", "group_id"),
    }
    for env_var, (section, key) in env_map.items():
        value = os.environ.get(env_var)
        if value is not None:
            config.setdefault(section, {})[key] = value
            if key == "group_id":
                config[section][key] = int(value)


def _validate(config: dict) -> None:
    required = [
        ("remarkable", "host"),
        ("remarkable", "password"),
        ("zotero", "api_key"),
        ("zotero", "group_id"),
    ]
    missing = []
    for section, key in required:
        value = config.get(section, {}).get(key)
        if not value:
            missing.append(f"{section}.{key}")
    if missing:
        raise ConfigError(f"Missing required config values: {', '.join(missing)}")


def get_cache_dir(config: dict) -> Path:
    cache_dir = Path(
        config.get("sync", {}).get("local_cache_dir", "~/.cache/remarkable2zotero/")
    ).expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir
