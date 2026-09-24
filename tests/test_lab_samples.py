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
from dtf_materials.config import SheetConfig
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


def test_duplicate_expected_header_raises_instead_of_silently_overwriting():
    """A sheet column mislabeled with a name the loader already relies on
    used to be invisible: `dict(zip(...))` keeps the rightmost, so a second
    column headed "Vendor" but filled with lab locations became every row's
    vendor, with flavor name, sample code and location all still correct —
    nothing downstream could tell. It now fails loudly, naming the column.

    Written after a "Vendor: A-5-1" (a lab location) turned up on the
    Warehouse + Lab tab, as the one loader-side explanation for it that
    leaves every other field intact."""
    header = VALID_HEADER + ["Vendor "]  # trailing space: normalizes to "Vendor"

    with pytest.raises(lab_samples.LabSheetHeaderError) as exc:
        list(rows_from_values(
            [header, _row(Vendor="Flavorchem") + ["A-5-1"]],
            lab_samples.EXPECTED_LAB_HEADERS,
            lab_samples.LabSheetHeaderError,
        ))

    assert "Vendor" in str(exc.value)


def test_unnamed_duplicate_columns_are_still_tolerated():
    """The duplicate check must not reject the sheets that actually exist:
    both real sheets carry blank trailing header cells (the company
    inventory sheet has three), which collide on the key "" that nothing
    reads. Only names the loader relies on are ambiguous."""
    header = VALID_HEADER + ["", "", ""]

    rows = list(rows_from_values(
        [header, _row(Vendor="Flavorchem") + ["junk", "junk", "junk"]],
        lab_samples.EXPECTED_LAB_HEADERS,
        lab_samples.LabSheetHeaderError,
    ))

    assert rows[0]["Vendor"] == "Flavorchem"


def test_every_source_column_lands_in_its_own_db_column(tmp_path, monkeypatch, config):
    """One sentinel per source column, end to end, asserting each arrives in
    the database column it belongs to and no other.

    The INSERT names 23 columns and passes 23 positional values; swap any
    two of those values and every other test here still passes, because they
    only ever set two or three fields at once. This is what says a location
    in the vendor column came from the sheet rather than from the loader."""
    text_columns = {
        "Vendor": "vendor",
        "Flavor Name": "flavor_name",
        "Sample Code": "sample_code",
        "Flavor Declaration Type (Natural, N&A, Artificial, WONF)": "declaration_type",
        "Location (Lab)": "location_lab",
        "Flavor Family": "flavor_family",
        "Category/Subcategory": "category_subcategory",
        "Usage Level (recommended)": "usage_level",
        "Dry Aroma Descriptors": "dry_aroma_descriptors",
        "Aroma Intensity 0-5": "aroma_intensity",
        "Top Note": "top_note",
        "Mid Palate Character": "mid_palate_character",
        "Finish Note": "finish_note",
        "Off Note Tendency": "off_note_tendency",
        "Matrix Performance": "matrix_performance",
        "Best Pairings": "best_pairings",
        "Tested In:": "tested_in",
        "Allergens": "allergens",
        "Part # (If applicable)": "dtf_part_num",
    }
    # Distinct, recognizable value per column, so a mix-up names both sides.
    cells = {header: f"<{header}>" for header in text_columns}
    cells["Date Received"] = "9/1/2021"      # parsed, not stored verbatim
    cells["Price ($/kg)"] = "$9.03"          # parsed, not stored verbatim
    cells["RD-ID"] = "RD-0001"               # validated, not a free-text sentinel

    monkeypatch.setattr(lab_samples, "_fetch_values", lambda cfg: [VALID_HEADER, _row(**cells)])
    db_path = tmp_path / "test.db"

    lab_samples.load_lab_samples(config, db_path)

    conn = init_db(db_path)
    row = conn.execute("SELECT * FROM lab_samples").fetchone()
    for header, column in text_columns.items():
        assert row[column] == f"<{header}>", (
            f"{header!r} landed somewhere other than lab_samples.{column}"
        )
    assert row["rd_id"] == "RD-0001"
    assert row["date_received"] == "2021-09-01"
    assert row["price_per_kilo"] == 9.03
    assert row["source_row"] == 2  # header is row 1 — the handle back to the sheet


@pytest.fixture
def config(tmp_path):
    return SheetConfig(
        sheet_id="fake", tab_name="fake", service_account_key_path=tmp_path / "key.json"
    )


def test_load_lab_samples_writes_rows_and_flags_duplicate_codes(tmp_path, monkeypatch, config):
    values = [
        VALID_HEADER,
        _row(Vendor="Sensapure", **{
            "RD-ID": "RD-0001",
            "Flavor Name": "Mango", "Sample Code": "7182011",
            "Price ($/kg)": "$9.03 ", "Date Received": "9/1/2021",
        }),
        _row(Vendor="Prinova", **{"RD-ID": "RD-0002", "Flavor Name": "Lime", "Sample Code": "42936"}),
        _row(Vendor="Virginia Dare", **{"RD-ID": "RD-0003", "Flavor Name": "Lime", "Sample Code": "42936"}),
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


def test_reload_upserts_in_place_keeping_lab_sample_id_stable(tmp_path, monkeypatch, config):
    """The reason this loader upserts on RD-ID instead of full-reloading: a
    Phase F formula line references a lab sample by an id that must survive a
    reload. Reorder the rows and edit a field, and each RD-ID must keep its
    lab_sample_id while the edit lands."""
    db_path = tmp_path / "test.db"

    first = [
        VALID_HEADER,
        _row(**{"RD-ID": "RD-0001", "Flavor Name": "Mango"}),
        _row(**{"RD-ID": "RD-0002", "Flavor Name": "Lime"}),
    ]
    monkeypatch.setattr(lab_samples, "_fetch_values", lambda cfg: first)
    lab_samples.load_lab_samples(config, db_path)

    conn = init_db(db_path)
    ids_before = dict(conn.execute("SELECT rd_id, lab_sample_id FROM lab_samples"))

    # Rows reordered, and RD-0002's flavor name corrected.
    second = [
        VALID_HEADER,
        _row(**{"RD-ID": "RD-0002", "Flavor Name": "Key Lime"}),
        _row(**{"RD-ID": "RD-0001", "Flavor Name": "Mango"}),
    ]
    monkeypatch.setattr(lab_samples, "_fetch_values", lambda cfg: second)
    lab_samples.load_lab_samples(config, db_path)

    conn = init_db(db_path)
    ids_after = dict(conn.execute("SELECT rd_id, lab_sample_id FROM lab_samples"))
    assert ids_after == ids_before  # same identity survived the reorder
    edited = conn.execute("SELECT flavor_name FROM lab_samples WHERE rd_id = 'RD-0002'").fetchone()
    assert edited["flavor_name"] == "Key Lime"


def test_reload_never_deletes_a_row_dropped_from_the_sheet(tmp_path, monkeypatch, config):
    """No more DELETE-and-reinsert: a sample removed from the sheet persists,
    the same never-delete rule materials follow, because a formula may point at
    it. Load two, reload with only one, both remain."""
    db_path = tmp_path / "test.db"

    monkeypatch.setattr(lab_samples, "_fetch_values", lambda cfg: [
        VALID_HEADER,
        _row(**{"RD-ID": "RD-0001", "Flavor Name": "Mango"}),
        _row(**{"RD-ID": "RD-0002", "Flavor Name": "Lime"}),
    ])
    lab_samples.load_lab_samples(config, db_path)

    monkeypatch.setattr(lab_samples, "_fetch_values", lambda cfg: [
        VALID_HEADER,
        _row(**{"RD-ID": "RD-0001", "Flavor Name": "Mango"}),
    ])
    lab_samples.load_lab_samples(config, db_path)

    conn = init_db(db_path)
    rd_ids = sorted(r[0] for r in conn.execute("SELECT rd_id FROM lab_samples"))
    assert rd_ids == ["RD-0001", "RD-0002"]


def test_blank_rd_id_row_is_skipped_and_counted(tmp_path, monkeypatch, config):
    """A row with no RD-ID can't be keyed, so it's skipped and flagged rather
    than loaded with an invented or null identity."""
    monkeypatch.setattr(lab_samples, "_fetch_values", lambda cfg: [
        VALID_HEADER,
        _row(**{"RD-ID": "RD-0001", "Flavor Name": "Mango"}),
        _row(**{"Flavor Name": "Orphan"}),  # no RD-ID
    ])
    db_path = tmp_path / "test.db"

    stats = lab_samples.load_lab_samples(config, db_path)

    assert stats.staged_rows == 1
    assert stats.rows_missing_rd_id == 1
    conn = init_db(db_path)
    names = [r[0] for r in conn.execute("SELECT flavor_name FROM lab_samples")]
    assert names == ["Mango"]


def test_malformed_rd_id_row_is_skipped_and_flagged(tmp_path, monkeypatch, config):
    """A typo'd RD-ID must never become a stable handle — skip and flag it,
    with the source row and the bad value, rather than coerce it."""
    monkeypatch.setattr(lab_samples, "_fetch_values", lambda cfg: [
        VALID_HEADER,
        _row(**{"RD-ID": "RD-1", "Flavor Name": "Short"}),      # too few digits
        _row(**{"RD-ID": "X-001", "Flavor Name": "WrongPrefix"}),
        _row(**{"RD-ID": "RD-0007", "Flavor Name": "Good"}),
    ])
    db_path = tmp_path / "test.db"

    stats = lab_samples.load_lab_samples(config, db_path)

    assert stats.staged_rows == 1
    assert stats.malformed_rd_ids == [(2, "RD-1"), (3, "X-001")]
    conn = init_db(db_path)
    rd_ids = [r[0] for r in conn.execute("SELECT rd_id FROM lab_samples")]
    assert rd_ids == ["RD-0007"]


def test_duplicate_rd_id_rows_are_all_skipped_not_merged(tmp_path, monkeypatch, config):
    """A duplicate RD-ID is a collision on the sample's identity. Because it's
    the UNIQUE upsert key, letting both through would silently overwrite one
    sample with the other — so every colliding row is skipped and flagged."""
    monkeypatch.setattr(lab_samples, "_fetch_values", lambda cfg: [
        VALID_HEADER,
        _row(**{"RD-ID": "RD-0001", "Flavor Name": "Mango"}),
        _row(**{"RD-ID": "RD-0009", "Flavor Name": "Lime"}),
        _row(**{"RD-ID": "RD-0009", "Flavor Name": "Lemon"}),
    ])
    db_path = tmp_path / "test.db"

    stats = lab_samples.load_lab_samples(config, db_path)

    assert stats.staged_rows == 1
    assert stats.duplicate_rd_ids == {"RD-0009": [3, 4]}
    conn = init_db(db_path)
    rd_ids = [r[0] for r in conn.execute("SELECT rd_id FROM lab_samples")]
    assert rd_ids == ["RD-0001"]  # RD-0009 loaded neither Lime nor Lemon


def test_missing_rd_id_header_raises():
    header = [h for h in VALID_HEADER if h != "RD-ID"]

    with pytest.raises(lab_samples.LabSheetHeaderError) as exc:
        list(rows_from_values([header, []], lab_samples.EXPECTED_LAB_HEADERS, lab_samples.LabSheetHeaderError))

    assert "RD-ID" in str(exc.value)
