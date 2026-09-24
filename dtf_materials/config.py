"""Loader for the gitignored local config that points at the real sheets.

`config.local.toml` exists only on a configured machine; this module is the one
place that reads it. Unlike cleaning.py it raises: without a valid config no
useful work is possible, so it stops with a message naming the missing piece.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .db import PROJECT_ROOT

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.local.toml"
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config.example.toml"


class ConfigError(Exception):
    """The local config is missing, unreadable, or incomplete."""


@dataclass(frozen=True)
class SheetConfig:
    """Where one Google Sheet tab lives and the service account key that can read it."""

    sheet_id: str
    tab_name: str
    service_account_key_path: Path


def load_sheets_config(path: Path | str = DEFAULT_CONFIG_PATH) -> SheetConfig:
    """Read the inventory sheet's `[sheets]` section (raises ConfigError if absent or incomplete)."""
    path = Path(path)
    section = _section(_read_toml(path), "sheets", path)
    return SheetConfig(
        sheet_id=_required_str(section, "sheets", "sheet_id", path),
        tab_name=_required_str(section, "sheets", "tab_name", path),
        service_account_key_path=_resolve_key_path(
            _required_str(section, "sheets", "service_account_key_path", path), path
        ),
    )


def load_lab_sheet_config(path: Path | str = DEFAULT_CONFIG_PATH) -> SheetConfig:
    """Read the lab sheet's `[lab_sheet]` section (raises ConfigError if absent or incomplete).

    The key path may be omitted and falls back to `[sheets]`, since one service
    account usually reads both sheets.
    """
    path = Path(path)
    data = _read_toml(path)
    section = _section(data, "lab_sheet", path)
    sheet_id = _required_str(section, "lab_sheet", "sheet_id", path)
    tab_name = _required_str(section, "lab_sheet", "tab_name", path)
    key_path = _optional_str(section, "service_account_key_path")
    if key_path is None:
        sheets = data.get("sheets")
        key_path = _optional_str(sheets, "service_account_key_path") if isinstance(sheets, dict) else None
    if key_path is None:
        raise ConfigError(
            f"{path} is missing [lab_sheet].service_account_key_path, and "
            f"there is no [sheets].service_account_key_path to fall back "
            f"to either. See {EXAMPLE_CONFIG_PATH.name} for the expected shape."
        )
    return SheetConfig(sheet_id, tab_name, _resolve_key_path(key_path, path))


def _read_toml(path: Path) -> dict:
    """Parse the config file, or raise ConfigError saying what's wrong with it."""
    if not path.exists():
        raise ConfigError(
            f"No local config at {path}. Copy {EXAMPLE_CONFIG_PATH.name} to "
            f"{path.name} and fill in your sheet ID, tab name, and service "
            f"account key path. It is gitignored; never commit it."
        )
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"{path} is not valid TOML ({exc}). See {EXAMPLE_CONFIG_PATH.name} "
            f"for the expected shape."
        ) from exc


def _section(data: dict, name: str, path: Path) -> dict:
    """Return the `[name]` table, or raise ConfigError if it's missing."""
    section = data.get(name)
    if not isinstance(section, dict):
        raise ConfigError(
            f"{path} has no [{name}] section. See {EXAMPLE_CONFIG_PATH.name} "
            f"for the expected shape."
        )
    return section


def _optional_str(section: dict, key: str) -> str | None:
    """Return a key's trimmed string value, or None if it's missing, blank or not a string."""
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _required_str(section: dict, section_name: str, key: str, path: Path) -> str:
    """Return a key's trimmed string value, or raise ConfigError naming the missing key."""
    value = _optional_str(section, key)
    if value is None:
        raise ConfigError(
            f"{path} is missing [{section_name}].{key} (or it is blank). See "
            f"{EXAMPLE_CONFIG_PATH.name} for the expected shape."
        )
    return value


def _resolve_key_path(raw: str, config_path: Path) -> Path:
    """Resolve a relative key path against the config file's folder, not the working directory."""
    key_path = Path(raw)
    if not key_path.is_absolute():
        key_path = config_path.parent / key_path
    return key_path
