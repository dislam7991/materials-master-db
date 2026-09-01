"""Tests for the pure cleaning/parsing functions.

These started life as the `if __name__ == "__main__"` self-checks inside
`cleaning.py`, which only ran when someone remembered to run the module by
hand. They are here so they run on every `python -m pytest` (and, once CI
exists, on every push) — same cases, plus the edge formats we know appear in
the real sheet.

The contract being tested is the one `cleaning.py` promises: a raw cell in,
a clean value or None out, never an exception. `None` means "this cell could
not be parsed", which is a finding for the quality report — not a failure to
be papered over here.
"""

from __future__ import annotations

import pytest

from dtf_materials.cleaning import (
    clean_text,
    is_standard_location,
    normalize_supplier_key,
    parse_bool_flag,
    parse_category,
    parse_date,
    parse_float,
    parse_price,
    split_locations,
)


# --- dates ---------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("5/1/2025", "2025-05-01"),      # the sheet's dominant format
        ("2024-03-04", "2024-03-04"),    # already ISO
        ("Oct 19 2024", "2024-10-19"),   # typed by hand
        ("", None),
        (None, None),
    ],
)
def test_parse_date_known_formats(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["1/5/25", "Jan 5, 2026"])
def test_parse_date_unsupported_formats_return_none(raw):
    """Formats the sheet also contains but the parser deliberately does not
    accept yet: a two-digit year and a comma-separated month name.

    They return None rather than a guess, so the row surfaces in the quality
    report for a human. Pinned here so that if someone later teaches
    `_DATE_FORMATS` about them, it is a deliberate decision with a test
    change attached, not an accident.
    """
    assert parse_date(raw) is None


# --- prices --------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("117.38", 117.38),
        ("$17.10/kg", 17.10),      # currency symbol + unit suffix
        (" 18.00 ", 18.00),        # stray whitespace
        ("17,10", 17.10),          # European decimal comma
        ("$1,234.56", 1234.56),    # US thousands separator
        ("1.234,56", 1234.56),     # European thousands separator
        ("TBD", None),
        ("call", None),
        ("", None),
    ],
)
def test_parse_price_known_formats(raw, expected):
    assert parse_price(raw) == expected


@pytest.mark.parametrize("raw", ["(15.00)", "12.50 USD"])
def test_parse_price_unsupported_formats_return_none(raw):
    """Two cells the sheet contains that the parser does not accept.

    `(15.00)` is accounting notation for a negative number, which is not a
    meaningful material price — inventing 15.00 or -15.00 from it would be a
    guess either way. `12.50 USD` is a trailing currency code, which the
    `$`/`/kg` noise stripper does not cover. Both go to the quality report.
    """
    assert parse_price(raw) is None


# --- plain numbers, categories, flags ------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [("42", 42.0), ("0.5", 0.5), ("n/a", None), ("", None), (None, None)],
)
def test_parse_float(raw, expected):
    assert parse_float(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("1", "raw"), ("1 Raw", "raw"), ("2", "flavor"), ("2 Flavor", "flavor"),
     ("packaging", None), ("", None)],
)
def test_parse_category(raw, expected):
    assert parse_category(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("Yes", 1), ("y", 1), ("TRUE", 1), ("1", 1), ("no", 0), ("", 0), (None, 0)],
)
def test_parse_bool_flag(raw, expected):
    """Blank is 0, not None: the column means "archive this", and an empty
    cell means nobody said to."""
    assert parse_bool_flag(raw) == expected


def test_clean_text_blank_becomes_none():
    assert clean_text("  Ascorbic Acid ") == "Ascorbic Acid"
    assert clean_text("   ") is None
    assert clean_text(None) is None


# --- locations -----------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("6L-27-D", ["6L-27-D"]),
        ("6R-09-E, 6R-10-C, 6R-13-C", ["6R-09-E", "6R-10-C", "6R-13-C"]),
        ("6L-27-D,6L-28-D", ["6L-27-D", "6L-28-D"]),
        ("6R-09-E 6R-10-C", ["6R-09-E", "6R-10-C"]),   # space-separated codes
        ("back cooler", ["BACK COOLER"]),              # free text stays whole
        ("cooler", ["COOLER"]),
        ("6l-27-d / 6L-27-D", ["6L-27-D"]),            # case-folded dedupe
        ("", []),
        (None, []),
    ],
)
def test_split_locations(raw, expected):
    assert split_locations(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("6L-27-D,", ["6L-27-D"]),
        ("6R-09-E, 6R-10-C, ", ["6R-09-E", "6R-10-C"]),
        ("6L-27-D;", ["6L-27-D"]),
        (" , 6L-27-D , ", ["6L-27-D"]),
        (",", []),
    ],
)
def test_split_locations_trailing_separators(raw, expected):
    """A trailing comma is what you get from someone deleting the last code
    in the cell. It must not become an empty location row in
    `lot_locations`, which would read as "this lot is stored nowhere"."""
    assert split_locations(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("6L-27-D", True),
        ("cooler", True),      # known named location
        ("back cooler", False),
        ("6L-27-", False),     # trailing hyphen, no final segment
        (None, False),
    ],
)
def test_is_standard_location(raw, expected):
    assert is_standard_location(raw) is expected


# --- supplier keys -------------------------------------------------------

def test_normalize_supplier_key_ignores_formatting_only_differences():
    assert normalize_supplier_key("Sensapure Flavors") == normalize_supplier_key(
        "  sensapure   flavors "
    )


def test_normalize_supplier_key_keeps_genuine_differences():
    """"NutraSci" and "Nutra Sci" may well be the same company, but deciding
    that is a human's call — the ETL only flags it. If this key collapsed
    them, the fuzzy-match finding would never reach the report."""
    assert normalize_supplier_key("NutraSci") != normalize_supplier_key("Nutra Sci")
