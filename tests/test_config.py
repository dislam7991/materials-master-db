"""Tests for the local config loader.

Two things matter here and nothing else does: a well-formed file produces a
typed config with the key path resolved, and every way the file can be wrong
produces an error that names `config.example.toml`, because that message is
the entire setup documentation an operator gets on a fresh machine.

Every test writes its own TOML into a temp directory, so nothing here reads
(or requires the existence of) a real `config.local.toml`.
"""

from __future__ import annotations

import pytest

from dtf_materials.config import (
    EXAMPLE_CONFIG_PATH,
    ConfigError,
    SheetsConfig,
    load_sheets_config,
)

VALID = """
[sheets]
sheet_id = "1AbCdEf"
tab_name = "Raw Material Inventory"
service_account_key_path = "credentials/service_account.json"
"""


def write_config(tmp_path, text: str):
    path = tmp_path / "config.local.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_valid_config_returns_typed_values(tmp_path):
    config = load_sheets_config(write_config(tmp_path, VALID))

    assert isinstance(config, SheetsConfig)
    assert config.sheet_id == "1AbCdEf"
    assert config.tab_name == "Raw Material Inventory"
    # Relative key paths resolve against the config file, not the cwd.
    assert config.service_account_key_path == tmp_path / "credentials" / "service_account.json"


def test_absolute_key_path_is_left_alone(tmp_path):
    absolute = tmp_path / "keys" / "sa.json"
    config = load_sheets_config(write_config(
        tmp_path, VALID.replace("credentials/service_account.json", str(absolute))
    ))

    assert config.service_account_key_path == absolute


def test_missing_file_names_the_example_file(tmp_path):
    """The whole point of the error: tell the operator which file to create."""
    with pytest.raises(ConfigError) as exc:
        load_sheets_config(tmp_path / "config.local.toml")

    assert EXAMPLE_CONFIG_PATH.name in str(exc.value)


def test_missing_key_names_the_key_and_the_example_file(tmp_path):
    without_tab = "\n".join(
        line for line in VALID.splitlines() if not line.startswith("tab_name")
    )

    with pytest.raises(ConfigError) as exc:
        load_sheets_config(write_config(tmp_path, without_tab))

    assert "tab_name" in str(exc.value)
    assert EXAMPLE_CONFIG_PATH.name in str(exc.value)


def test_blank_value_is_treated_as_missing(tmp_path):
    """A half-filled copy of the example is likelier than a missing key."""
    with pytest.raises(ConfigError) as exc:
        load_sheets_config(write_config(tmp_path, VALID.replace('"1AbCdEf"', '"   "')))

    assert "sheet_id" in str(exc.value)


def test_config_without_a_sheets_section_is_rejected(tmp_path):
    with pytest.raises(ConfigError) as exc:
        load_sheets_config(write_config(tmp_path, 'sheet_id = "1AbCdEf"\n'))

    assert "[sheets]" in str(exc.value)


def test_malformed_toml_is_rejected(tmp_path):
    with pytest.raises(ConfigError) as exc:
        load_sheets_config(write_config(tmp_path, "[sheets\nsheet_id = "))

    assert EXAMPLE_CONFIG_PATH.name in str(exc.value)


def test_the_committed_example_file_is_loadable(tmp_path):
    """The example must stay in step with the loader: if a key is renamed in
    one and not the other, an operator copies a file that cannot load."""
    config = load_sheets_config(
        write_config(tmp_path, EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"))
    )

    assert config.sheet_id and config.tab_name
