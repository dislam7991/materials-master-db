"""Tests for the read-side promises `queries.py` makes about stock.

The ETL's job is to load the sheet faithfully; this module's job is to report
what was loaded *honestly*, and "honestly" here has a specific meaning taken
from the source data's limits (see README, "Stock figures are reported
honestly"). The sheet records one quantity per lot and, separately, the
locations that lot occupies — never how the quantity splits between them. So:

  * a lot in two racks contributes its full quantity to each row, and those
    rows must not be summed;
  * stock in a lot with a blank Locations cell is real stock the company
    cannot find from the record, and has to be named rather than quietly
    dropped;
  * a material with nothing on hand still knows where it last lived.

Each of those is one refactor away from inventing inventory that does not
exist, which is the one thing this project promises never to do. The tests
below exist to make that refactor fail loudly.

Fixtures are hand-written source rows run through the real `etl.run()`, not
rows inserted straight into the tables. That costs a few lines and buys
fidelity: a database state the ETL could not actually produce is impossible to
write here, so a passing test always describes something that can really
happen on the shop floor.
"""

from __future__ import annotations

from typing import Iterator

import pytest

from dtf_materials import etl
from dtf_materials import queries as q
from dtf_materials.db import connect
from dtf_materials.sources.base import EXPECTED_HEADERS, InventorySource, RawRow


class ListSource(InventorySource):
    """An in-memory source, so a test can hand the ETL an exact sheet.

    Same shape as the one in tests/test_etl.py. Kept local rather than shared:
    tests/test_lab_samples.py sets the precedent of a module owning its own
    helpers, and two copies of six lines is not yet worth a conftest.
    """

    def __init__(self, rows: list[RawRow]):
        self._rows = rows

    def rows(self) -> Iterator[RawRow]:
        yield from self._rows


def _row(**overrides) -> RawRow:
    """One raw sheet row, blank except for the overrides.

    Keyed by the sheet's own header names, exactly as CsvInventorySource would
    hand them over — so every row here goes through the same staging, cleaning
    and load path the real sheet does.
    """
    row = {header: "" for header in EXPECTED_HEADERS}
    unknown = set(overrides) - set(EXPECTED_HEADERS)
    assert not unknown, f"not columns on the real sheet: {sorted(unknown)}"
    row.update(overrides)
    return row


def _material(part_num: str, name: str, **overrides) -> RawRow:
    """A row with the columns every material needs filled in."""
    return _row(**{
        "DTF Part #": part_num,
        "Material Name": name,
        "Supplier/MFG": "Test Supplier",
        "Category (1 Raw)(2 Flavor)": "1",
        **overrides,
    })


# One small sheet. Each material isolates one behaviour, so a failure names
# the promise it broke rather than "something about stock".
SHEET_ROWS: list[RawRow] = [
    # RM-0001: one lot, two racks. The quantity belongs to the lot, not to
    # either rack, and the sheet never says how it divides.
    _material("RM-0001", "Split Lot Material",
              **{"Receiving Date": "03/15/2025", "DTF Lot #": "L-1001",
                 "Locations": "6L-27-D, 6L-28-A", "Current Stock": "100",
                 "Price Per Kilo": "10.00"}),

    # RM-0002: 40 kg somewhere known, 60 kg nobody wrote down. The 60 is the
    # whole reason unlocated_stock exists.
    _material("RM-0002", "Partly Unlocated Material",
              **{"Receiving Date": "02/01/2025", "DTF Lot #": "L-2001",
                 "Locations": "6R-09-E", "Current Stock": "40",
                 "Price Per Kilo": "8.00"}),
    _material("RM-0002", "Partly Unlocated Material",
              **{"Receiving Date": "03/01/2025", "DTF Lot #": "L-2002",
                 "Locations": "", "Current Stock": "60",
                 "Price Per Kilo": "9.00"}),

    # RM-0003: used up, but the shelf it came off is still known.
    _material("RM-0003", "Depleted Material",
              **{"Receiving Date": "01/10/2025", "DTF Lot #": "L-3001",
                 "Locations": "6R-01-A", "Current Stock": "0"}),
    _material("RM-0003", "Depleted Material",
              **{"Receiving Date": "04/20/2025", "DTF Lot #": "L-3002",
                 "Locations": "6R-13-C", "Current Stock": "0"}),

    # RM-0004: stock on the sheet, but flagged for archive — it should not
    # count as available.
    _material("RM-0004", "Archived Material",
              **{"Receiving Date": "03/05/2025", "DTF Lot #": "L-4001",
                 "Locations": "6L-30-B", "Current Stock": "50",
                 "Ready To Archive": "Yes"}),

    # RM-0005: somebody left the Receiving Date blank. An undated lot must not
    # be mistaken for the newest one.
    _material("RM-0005", "Undated Lot Material",
              **{"Receiving Date": "05/01/2025", "DTF Lot #": "L-5001",
                 "Locations": "6R-20-A", "Current Stock": "10"}),
    _material("RM-0005", "Undated Lot Material",
              **{"Receiving Date": "", "DTF Lot #": "L-5002",
                 "Locations": "6R-21-B", "Current Stock": "10"}),

    # RM-0006 / RM-0007: a percent sign in a real material name, and a decoy
    # that only matches if the percent is treated as a wildcard.
    _material("RM-0006", "Whey Protein Isolate 90%",
              **{"Receiving Date": "03/20/2025", "DTF Lot #": "L-6001",
                 "Locations": "6R-30-A", "Current Stock": "25"}),
    _material("RM-0007", "Creatine 900 Mesh",
              **{"Receiving Date": "03/21/2025", "DTF Lot #": "L-7001",
                 "Locations": "6R-31-A", "Current Stock": "25"}),

    # RM-0008 / RM-0009: the same trap for the underscore wildcard.
    _material("RM-0008", "Vitamin B_12 Blend",
              **{"Receiving Date": "03/22/2025", "DTF Lot #": "L-8001",
                 "Locations": "6R-32-A", "Current Stock": "5"}),
    _material("RM-0009", "Vitamin B112 Blend",
              **{"Receiving Date": "03/23/2025", "DTF Lot #": "L-9001",
                 "Locations": "6R-33-A", "Current Stock": "5"}),

    # RM-0010: a location code with "6L" in the middle rather than at the
    # front, for the aisle-prefix characterization test below.
    _material("RM-0010", "Mid Code Material",
              **{"Receiving Date": "03/24/2025", "DTF Lot #": "L-0010",
                 "Locations": "A6L-99-Z", "Current Stock": "15"}),
]


@pytest.fixture
def conn(tmp_path):
    """The sheet above, loaded into a throwaway database. Never touches
    db/materials.db."""
    db_path = tmp_path / "test_queries.db"
    etl.run(ListSource(SHEET_ROWS), db_path)
    connection = connect(db_path)
    yield connection
    connection.close()


def material_id(conn, part_num: str) -> int:
    row = conn.execute(
        "SELECT material_id FROM materials WHERE dtf_part_num = ?", (part_num,)
    ).fetchone()
    assert row is not None, f"{part_num} should have loaded"
    return row["material_id"]


def part_nums(rows) -> set[str]:
    return {r["dtf_part_num"] for r in rows}


# --------------------------------------------------------------------------
# Honesty invariants
# --------------------------------------------------------------------------

def test_a_lot_spanning_locations_is_flagged_and_must_not_be_summed(conn):
    """The single most dangerous number in the app.

    One 100 kg lot sits in two racks. Each rack's row carries the full 100,
    because the sheet never records the split — so the rows add up to 200 kg
    of material that does not exist. Both facts are asserted together on
    purpose: the flag is only meaningful next to the discrepancy it warns
    about.
    """
    mid = material_id(conn, "RM-0001")

    locations = q.get_stocked_locations(conn, mid)

    assert {r["location"] for r in locations} == {"6L-27-D", "6L-28-A"}
    assert all(r["stock"] == 100 for r in locations)
    assert all(r["lot_spans_locations"] for r in locations)

    assert sum(r["stock"] for r in locations) == 200
    assert q.total_stock(conn, mid) == 100


def test_stock_in_lots_with_no_location_is_reported_separately(conn):
    """60 kg the company owns and cannot find from the record.

    It is counted in the total but has no row in the locations table. Left
    unnamed, the two numbers would simply disagree and the reader would
    assume the tool was broken — when in fact the tool is reporting the one
    thing it exists to surface.
    """
    mid = material_id(conn, "RM-0002")

    assert q.unlocated_stock(conn, mid) == 60

    locations = q.get_stocked_locations(conn, mid)
    assert {r["location"] for r in locations} == {"6R-09-E"}
    assert sum(r["stock"] for r in locations) == 40


def test_total_stock_reconciles_with_located_plus_unlocated(conn):
    """The arithmetic the app's caption implicitly claims.

    For a material with no split lot, every kilo is either in a rack the
    sheet names or in the unlocated bucket. If this ever fails, one of the
    three queries has started counting a different set of lots from the
    others, and the app is showing numbers that do not add up.
    """
    mid = material_id(conn, "RM-0002")

    located = sum(r["stock"] for r in q.get_stocked_locations(conn, mid))

    assert located + q.unlocated_stock(conn, mid) == q.total_stock(conn, mid)


def test_a_depleted_material_still_reports_its_last_known_location(conn):
    """Nothing on hand is not the same as no idea where this lives.

    RM-0003 is empty, so the locations table is rightly blank — but the most
    recent lot came off 6R-13-C, and an older one off 6R-01-A. Reporting the
    older shelf would send somebody to the wrong aisle.
    """
    mid = material_id(conn, "RM-0003")

    assert q.get_stocked_locations(conn, mid) == []
    assert q.total_stock(conn, mid) == 0

    last_known = q.get_last_known_locations(conn, mid)
    assert [r["location"] for r in last_known] == ["6R-13-C"]
    assert last_known[0]["dtf_lot_num"] == "L-3002"


def test_archived_and_zero_stock_lots_are_excluded_from_stock_figures(conn):
    """A drum flagged Ready To Archive is not available stock.

    The sheet still carries a quantity against it, so every stock query has
    to filter it out or the app will offer material nobody should be
    pulling from.
    """
    mid = material_id(conn, "RM-0004")

    assert q.total_stock(conn, mid) == 0
    assert q.get_stocked_locations(conn, mid) == []
    assert q.unlocated_stock(conn, mid) == 0


def test_undated_lots_sort_last_rather_than_newest(conn):
    """A blank Receiving Date is missing information, not an early date.

    SQL sorts NULL first by default, which would make the lot nobody dated
    the "most recent" one — and the last-known-location fallback would then
    name whichever shelf that lot happened to sit on.
    """
    mid = material_id(conn, "RM-0005")

    lots = q.get_lots(conn, mid)
    assert [r["dtf_lot_num"] for r in lots] == ["L-5001", "L-5002"]
    assert lots[-1]["receiving_date"] is None

    last_known = q.get_last_known_locations(conn, mid)
    assert [r["location"] for r in last_known] == ["6R-20-A"]


# --------------------------------------------------------------------------
# Search safety
# --------------------------------------------------------------------------

def test_a_percent_in_a_material_name_matches_literally(conn):
    """Searching "90%" must not become "match everything containing 90".

    "Whey Protein Isolate 90%" is an ordinary material name here. Without
    escaping, the percent becomes a LIKE wildcard and "Creatine 900 Mesh"
    comes back too — the search silently widening rather than failing.
    """
    results = q.search_materials(conn, "90%")

    assert part_nums(results) == {"RM-0006"}


def test_an_underscore_in_a_search_term_matches_literally(conn):
    """The wildcard people forget. Unescaped, "_" matches any single
    character, so "B_12" would also return "Vitamin B112 Blend"."""
    results = q.search_materials(conn, "B_12")

    assert part_nums(results) == {"RM-0008"}


def test_a_blank_search_term_returns_no_rows(conn):
    """An empty search box should show nothing, not the whole table."""
    assert q.search_materials(conn, "") == []
    assert q.search_materials(conn, "   ") == []
    assert q.search_by_location(conn, "") == []
    assert q.search_by_location(conn, "   ") == []


# --------------------------------------------------------------------------
# Rack view: archived lots are shown, not hidden
# --------------------------------------------------------------------------

def test_rack_browsing_shows_archived_lots_and_flags_them(conn):
    """The rack view answers a different question from the material page.

    A material's page answers "can I use this?", where a lot flagged Ready
    To Archive is a no and is filtered out. Browsing a rack answers "what is
    physically on this shelf?" — the drum is still there, and hiding it would
    make the app disagree with the warehouse. So the row is returned, and
    `ready_to_archive` comes back with it so the UI can label it rather than
    mixing it in silently. The flag is the sheet's own column, not a
    judgement this code makes.
    """
    results = q.search_by_location(conn, "6L-30-B")

    assert part_nums(results) == {"RM-0004"}
    assert results[0]["ready_to_archive"] == 1

    # And the same query still reports unarchived stock as unarchived, so the
    # label means something.
    unarchived = q.search_by_location(conn, "6L-27-D")
    assert part_nums(unarchived) == {"RM-0001"}
    assert unarchived[0]["ready_to_archive"] == 0


# --------------------------------------------------------------------------
# Characterization — current behaviour, pending a decision
# --------------------------------------------------------------------------
# Describes what search_by_location does today rather than what it
# necessarily should do. Recorded in SPEC.md's Backlog; when the question is
# answered, the answer changes the assertion here deliberately instead of
# surfacing as a surprise failure.

def test_location_search_currently_matches_a_prefix_anywhere_in_the_code(conn):
    """Aisle search is a substring match, not an anchored prefix.

    "6L" finds the 6L aisle as intended, and also "A6L-99-Z", where 6L is in
    the middle of a different code. See SPEC.md Backlog.
    """
    locations = {r["location"] for r in q.search_by_location(conn, "6L")}

    assert "A6L-99-Z" in locations
    assert {"6L-27-D", "6L-28-A"} <= locations
