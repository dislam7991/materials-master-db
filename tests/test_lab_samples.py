"""Tests for the lab sample loader.

Header-check/row-shaping logic is the shared sources.base.rows_from_values,
already covered by tests/test_sheets_source.py — the tests here focus on
what's specific to this loader: its own header list, and load_lab_samples'
DB-writing and duplicate-code detection. _fetch_values is monkeypatched
everywhere here, so none of this needs a real Sheets connection or gspread
installed, matching the same reason the default CSV pipeline never needs it.
"""

from __future__ import annotations

import pytest

from dtf_materials import lab_samples
from dtf_materials.config import LabSheetConfig
from dtf_materials.db import init_db
from dtf_materials.sources.base import rows_from_values

VALID_HEADER = lab_samples.EXPECTED_LAB_HEADERS


def _row(**overrides) -> list[str]:
    """One data row matching VALID_HEADER, blank except for overrides."""
    values = {h: "" for h in VALID_HEADER}
    values.update(overrides)
    return [values[h] for h in VALID_HEADER]


def test_valid_header_produces_expected_dict_keys():
    values = [VALID_HEADER, _row(Vendor="Sensapure", **{"Flavor Name": "Mango", "Sample Code": "7182011"})]

    rows = list(rows_from_values(values, lab_samples.EXPECTED_LAB_HEADERS, lab_samples.LabSheetHeaderError))

    assert rows[0]["Vendor"] == "Sensapure"
    assert rows[0]["Sample Code"] == "7182011"


def test_missing_expected_column_raises():
    header = [h for h in VALID_HEADER if h != "Sample Code"]

    with pytest.raises(lab_samples.LabSheetHeaderError) as exc:
        list(rows_from_values([header, []], lab_samples.EXPECTED_LAB_HEADERS, lab_samples.LabSheetHeaderError))

    assert "Sample Code" in str(exc.value)


@pytest.fixture
def config(tmp_path):
    return LabSheetConfig(
        sheet_id="fake", tab_name="fake", service_account_key_path=tmp_path / "key.json"
    )


def test_load_lab_samples_writes_rows_and_flags_duplicate_codes(tmp_path, monkeypatch, config):
    values = [
        VALID_HEADER,
        _row(Vendor="Sensapure", **{
            "Flavor Name": "Mango", "Sample Code": "7182011",
            "Price ($/kg)": "$9.03 ", "Date Received": "9/1/2021",
        }),
        _row(Vendor="Prinova", **{"Flavor Name": "Lime", "Sample Code": "42936"}),
        _row(Vendor="Virginia Dare", **{"Flavor Name": "Lime", "Sample Code": "42936"}),
    ]
    monkeypatch.setattr(lab_samples, "_fetch_values", lambda cfg: values)
    db_path = tmp_path / "test.db"

    stats = lab_samples.load_lab_samples(config, db_path)

    assert stats.staged_rows == 3
    # source rows: header is row 1, so the three data rows are 2, 3, 4 —
    # the Prinova/Virginia Dare collision is rows 3 and 4.
    assert stats.duplicate_sample_codes == {"42936": [3, 4]}

    conn = init_db(db_path)
    row = conn.execute(
        "SELECT * FROM lab_samples WHERE sample_code = '7182011'"
    ).fetchone()
    assert row["vendor"] == "Sensapure"
    assert row["price_per_kilo"] == 9.03
    assert row["date_received"] == "2021-09-01"


def test_load_lab_samples_clears_previous_rows_on_reload(tmp_path, monkeypatch, config):
    db_path = tmp_path / "test.db"

    monkeypatch.setattr(lab_samples, "_fetch_values", lambda cfg: [VALID_HEADER, _row(**{"Sample Code": "A"})])
    lab_samples.load_lab_samples(config, db_path)

    monkeypatch.setattr(lab_samples, "_fetch_values", lambda cfg: [VALID_HEADER, _row(**{"Sample Code": "B"})])
    stats = lab_samples.load_lab_samples(config, db_path)

    conn = init_db(db_path)
    codes = [r[0] for r in conn.execute("SELECT sample_code FROM lab_samples")]
    assert codes == ["B"]
    assert stats.staged_rows == 1
