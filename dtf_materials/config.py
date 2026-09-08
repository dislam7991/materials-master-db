"""Loader for the gitignored local config that points at the real sheets.

Everything in this repo runs on synthetic data by default; the only thing
that knows about the company's actual Google Sheets is `config.local.toml`,
which is gitignored and exists solely on a configured machine. This module is
the one place that reads it, so no other module has to know the file's name,
its shape, or that it might be absent.

Unlike `cleaning.py`, this module *does* raise. A bad cell is an ordinary
fact about a messy sheet and belongs in the quality report; a missing or
malformed config means the operator has not set the machine up yet and no
useful work is possible, so the honest response is to stop immediately with a
message that says exactly which file to create and which key is missing.
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
class SheetsConfig:
    """What `SheetsInventorySource` (B2) needs to reach the real inventory tab."""

    sheet_id: str
    tab_name: str
    service_account_key_path: Path


@dataclass(frozen=True)
class LabSheetConfig:
    """What the lab-sample loader (Phase E) needs to reach the flavor sample
    tab — a second, separate Google Sheet from the warehouse inventory."""

    sheet_id: str
    tab_name: str
    service_account_key_path: Path


def _read_toml(path: Path) -> dict:
    """Load and parse the local config file, or raise ConfigError explaining
    exactly what's wrong. Shared by every section-specific loader below, so
    a missing file or broken TOML reads the same regardless of which sheet
    someone was trying to configure."""
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


def _resolve_key_path(raw: str, config_path: Path) -> Path:
    """A relative key path is resolved against the config file's own
    directory, not the working directory, so the ETL behaves the same
    whether it is run from the repo root or from anywhere else."""
    key_path = Path(raw)
    if not key_path.is_absolute():
        key_path = config_path.parent / key_path
    return key_path


def load_sheets_config(path: Path | str = DEFAULT_CONFIG_PATH) -> SheetsConfig:
    """Read the `[sheets]` table out of the local config.

    Raises ConfigError — naming `config.example.toml` — if the file is absent,
    isn't valid TOML, or omits one of the three required keys.
    """
    path = Path(path)
    data = _read_toml(path)

    section = data.get("sheets")
    if not isinstance(section, dict):
        raise ConfigError(
            f"{path} has no [sheets] section. See {EXAMPLE_CONFIG_PATH.name} "
            f"for the expected shape."
        )

    values = {}
    for key in ("sheet_id", "tab_name", "service_account_key_path"):
        value = section.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(
                f"{path} is missing [sheets].{key} (or it is blank). See "
                f"{EXAMPLE_CONFIG_PATH.name} for the expected shape."
            )
        values[key] = value.strip()

    return SheetsConfig(
        sheet_id=values["sheet_id"],
        tab_name=values["tab_name"],
        service_account_key_path=_resolve_key_path(values["service_account_key_path"], path),
    )


def load_lab_sheet_config(path: Path | str = DEFAULT_CONFIG_PATH) -> LabSheetConfig:
    """Read the `[lab_sheet]` table out of the local config.

    `service_account_key_path` is optional here and falls back to
    `[sheets].service_account_key_path` when omitted — the common case is
    one service account shared across both sheets, so nobody has to repeat
    the same path twice.

    Raises ConfigError — naming `config.example.toml` — if the file is
    absent, isn't valid TOML, omits [lab_sheet].sheet_id or .tab_name, or
    has no key path available from either section.
    """
    path = Path(path)
    data = _read_toml(path)

    section = data.get("lab_sheet")
    if not isinstance(section, dict):
        raise ConfigError(
            f"{path} has no [lab_sheet] section. See {EXAMPLE_CONFIG_PATH.name} "
            f"for the expected shape."
        )

    values = {}
    for key in ("sheet_id", "tab_name"):
        value = section.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(
                f"{path} is missing [lab_sheet].{key} (or it is blank). See "
                f"{EXAMPLE_CONFIG_PATH.name} for the expected shape."
            )
        values[key] = value.strip()

    key_path_raw = section.get("service_account_key_path")
    if not isinstance(key_path_raw, str) or not key_path_raw.strip():
        sheets_section = data.get("sheets")
        fallback = sheets_section.get("service_account_key_path") if isinstance(sheets_section, dict) else None
        if not isinstance(fallback, str) or not fallback.strip():
            raise ConfigError(
                f"{path} is missing [lab_sheet].service_account_key_path, and "
                f"there is no [sheets].service_account_key_path to fall back "
                f"to either. See {EXAMPLE_CONFIG_PATH.name} for the expected shape."
            )
        key_path_raw = fallback
    values["service_account_key_path"] = key_path_raw.strip()

    return LabSheetConfig(
        sheet_id=values["sheet_id"],
        tab_name=values["tab_name"],
        service_account_key_path=_resolve_key_path(values["service_account_key_path"], path),
    )
