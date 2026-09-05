"""Loader for the gitignored local config that points at the real sheet.

Everything in this repo runs on synthetic data by default; the only thing
that knows about the company's actual Google Sheet is `config.local.toml`,
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


def load_sheets_config(path: Path | str = DEFAULT_CONFIG_PATH) -> SheetsConfig:
    """Read the `[sheets]` table out of the local config.

    Raises ConfigError — naming `config.example.toml` — if the file is absent,
    isn't valid TOML, or omits one of the three required keys.
    """
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"No local config at {path}. Copy {EXAMPLE_CONFIG_PATH.name} to "
            f"{path.name} and fill in your sheet ID, tab name, and service "
            f"account key path. It is gitignored; never commit it."
        )

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"{path} is not valid TOML ({exc}). See {EXAMPLE_CONFIG_PATH.name} "
            f"for the expected shape."
        ) from exc

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

    # A relative key path is resolved against the config file's own directory,
    # not the working directory, so the ETL behaves the same whether it is run
    # from the repo root or from anywhere else.
    key_path = Path(values["service_account_key_path"])
    if not key_path.is_absolute():
        key_path = path.parent / key_path

    return SheetsConfig(
        sheet_id=values["sheet_id"],
        tab_name=values["tab_name"],
        service_account_key_path=key_path,
    )
