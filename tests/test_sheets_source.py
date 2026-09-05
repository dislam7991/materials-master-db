"""Tests for the Sheets source's header-checking and row-shaping logic.

These test `_rows_from_values` directly, against plain lists of lists, so
none of this needs a real Google connection or credentials. gspread is an
optional dependency (see requirements-sheets.txt) that CI does not install,
so the whole module is skipped rather than failing to import when it's
absent — the same reason the default CSV pipeline never imports it either.
"""

from __future__ import annotations

import pytest

gspread = pytest.importorskip("gspread")

from dtf_materials.sources.base import EXPECTED_HEADERS
from dtf_materials.sources.sheets_source import SheetHeaderError, SheetsInventorySource

# The real company sheet's actual header row, copied verbatim (blank cells
# and all) from a live run against it. Three of these differ from
# EXPECTED_HEADERS only in whitespace — "Lot / Batch" vs "Lot/Batch", a
# trailing space on "Current Stock " and "Price Per Kilo ", and a space
# between the two parenthetical bits in "Category (1 Raw) (2 Flavor)" —
# which is exactly what _normalize_header exists to collapse.
REAL_SHEET_HEADER = [
    "Receiving Date", "", "", "Locations", "DTF Lot #", "DTF Part #", "Status",
    "Allergen", "Material Name", "Supplier/MFG", "Lot / Batch", "EXP. Date", "",
    "Start Day Stock", "Current Stock ", "Filter Moving / Date",
    "Check / Cycle count", "Category (1 Raw) (2 Flavor)", "Price Per Kilo ",
    "Total cost", "Ready To Archive",
]


def test_valid_values_produce_one_dict_per_data_row():
    values = [
        EXPECTED_HEADERS,
        ["2025-01-01", "6L-27-D", "L1", "RM-1409", "Active"] + [""] * (len(EXPECTED_HEADERS) - 5),
    ]

    rows = list(SheetsInventorySource._rows_from_values(values))

    assert len(rows) == 1
    assert rows[0]["DTF Part #"] == "RM-1409"
    assert rows[0]["Locations"] == "6L-27-D"


def test_extra_and_reordered_columns_are_tolerated():
    """Rows are matched by header name, not position, so a human-maintained
    sheet can add a column or reorder them without breaking the load."""
    header = ["Extra Column"] + list(reversed(EXPECTED_HEADERS))
    values = [header, ["ignored"] + ["x"] * len(EXPECTED_HEADERS)]

    rows = list(SheetsInventorySource._rows_from_values(values))

    assert rows[0]["DTF Part #"] == "x"
    assert "Extra Column" in rows[0]


def test_missing_expected_header_raises_immediately():
    header = [h for h in EXPECTED_HEADERS if h != "DTF Part #"]

    with pytest.raises(SheetHeaderError) as exc:
        list(SheetsInventorySource._rows_from_values([header, ["x"] * len(header)]))

    assert "DTF Part #" in str(exc.value)


def test_empty_sheet_yields_no_rows():
    assert list(SheetsInventorySource._rows_from_values([])) == []


def test_real_sheet_header_whitespace_variants_are_normalized():
    """Regression test for the real company sheet, which failed with
    'missing expected column(s)' before _normalize_header existed — every
    one of those six was present, just spaced differently than the code
    assumed."""
    values = [REAL_SHEET_HEADER, ["x"] * len(REAL_SHEET_HEADER)]

    rows = list(SheetsInventorySource._rows_from_values(values))

    row = rows[0]
    for canonical in ("Lot/Batch", "Current Stock", "Filter Moving/Date",
                      "Check/Cycle count", "Category (1 Raw)(2 Flavor)", "Price Per Kilo"):
        assert row[canonical] == "x", f"{canonical!r} not populated under its canonical key"


def test_short_row_yields_a_dict_missing_the_trailing_keys():
    """gspread pads short rows by default, but the source's own contract
    shouldn't depend on that — a short row should degrade gracefully rather
    than crash, since downstream code already treats a missing key as blank."""
    values = [EXPECTED_HEADERS, ["2025-01-01"]]

    rows = list(SheetsInventorySource._rows_from_values(values))

    assert rows[0]["Receiving Date"] == "2025-01-01"
    assert "Ready To Archive" not in rows[0]
