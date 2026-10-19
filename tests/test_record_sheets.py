"""Tests for the Sample Record Sheet built in the app (SPEC F11).

Pinned here:

  1. Actual Input = label claim x (1 + overage) / activity, 3 decimals half-up,
     and a blank input (or activity 0) gives no Actual Input, never 0.
  2. Formula % is Actual Input over the total and sums to 100 %; cost per unit
     is per jar (servings per unit); blanks are left out of totals and named.
  3. Activity / overage are remembered per material and pre-fill the next
     line; no history pre-fills blank; a per-line change is that line's only.
  4. Flavor lines come from the flavor profile at read time, BASE excluded,
     at Activity 100 % / Overage 0.
  5. The loaders never touch record-sheet tables.
  6. The copy blocks put each section in the sheet's A:I shape.
  7. A material neither catalog carries can be typed, with a typed price,
     and an older record_lines table gains typed lines without losing rows.

The arithmetic cases are the shape of a real PL Cost Sheet block (label
claim, activity, overage) under synthetic names and prices.
"""

from __future__ import annotations

import sqlite3

import pytest

from dtf_materials import etl
from dtf_materials import flavor_sheets as fs
from dtf_materials import record_sheets as rs
from dtf_materials.db import PROJECT_ROOT, init_db
from dtf_materials.record_copy_block import record_blocks
from dtf_materials.sources import CsvInventorySource

SYNTHETIC_CSV = PROJECT_ROOT / "data" / "synthetic" / "raw_material_inventory.csv"


@pytest.fixture
def conn(tmp_path):
    """A connection to a fresh, empty database."""
    c = init_db(tmp_path / "test.db")
    yield c
    c.close()


def _material(conn, name, part_num, price=None):
    """Insert a warehouse material and return its id."""
    cur = conn.execute(
        "INSERT INTO materials (dtf_part_num, material_name, current_price_per_kilo) VALUES (?,?,?)",
        (part_num, name, price),
    )
    conn.commit()
    return cur.lastrowid


def _line(name, claim, activity, overage, price=None, section="active"):
    """A record line dict as compute() takes it."""
    return {"name": name, "section": section, "label_claim_mg": claim,
            "activity": activity, "overage": overage, "price_per_kilo": price}


# --- the arithmetic -------------------------------------------------------------

@pytest.mark.parametrize("claim, activity, overage, expected", [
    (1000, 0.234, 0.10, 4700.855),
    (100, 0.34, 0.10, 323.529),
    (300, 0.50, 0.10, 660.0),
    (3000, 1.0, 0.0, 3000.0),
])
def test_actual_input_matches_the_sheet(claim, activity, overage, expected):
    assert rs.actual_input(claim, activity, overage) == expected


@pytest.mark.parametrize("claim, activity, overage", [
    (None, 1.0, 0.0), (100, None, 0.0), (100, 1.0, None), (100, 0.0, 0.0),
])
def test_a_blank_input_or_zero_activity_gives_no_actual_input(claim, activity, overage):
    assert rs.actual_input(claim, activity, overage) is None


def test_rounding_is_half_up_not_to_even():
    assert rs.round_half_up(2.0005) == 2.001
    assert rs.round_half_up(0.0005) == 0.001
    assert rs.round_half_up(1.0004) == 1.0


def test_compute_columns_and_totals():
    result = rs.compute(
        [
            _line("Salt A", 1000, 0.234, 0.10, price=8.0),
            _line("Powder B", 3000, 1.0, 0.0, price=5.0),
            _line("Flow agent", 50, 1.0, 0.0, price=10.0, section="excipient"),
            _line("Flavor C", 150, 1.0, 0.0, price=20.0, section="flavor"),
        ],
        servings_per_unit=5, servings_base=1000,
    )
    salt, powder, flow, flavor = result["lines"]
    assert salt["actual_input_mg"] == 4700.855
    assert salt["g_per_run"] == pytest.approx(4700.855)
    assert salt["kg_per_run"] == pytest.approx(4.700855)
    # per jar: mg/1e6 x $/kg x servings per unit
    assert salt["cost_per_unit"] == pytest.approx(4700.855 / 1e6 * 8.0 * 5)
    assert sum(l["formula_pct"] for l in result["lines"]) == pytest.approx(1.0)
    assert powder["formula_pct"] == pytest.approx(3000 / 7900.855)

    t = result["totals"]
    assert t["actual_input_mg"] == pytest.approx(7900.855)
    assert t["g_per_run"] == pytest.approx(7900.855)
    assert t["kg_per_run"] == pytest.approx(7.900855)
    assert t["actives_cost_per_unit"] == pytest.approx(salt["cost_per_unit"] + powder["cost_per_unit"])
    assert t["inactives_cost_per_unit"] == pytest.approx(flow["cost_per_unit"] + flavor["cost_per_unit"])
    assert t["cost_per_unit"] == pytest.approx(t["actives_cost_per_unit"] + t["inactives_cost_per_unit"])
    assert t["cost_per_serving"] == pytest.approx(t["cost_per_unit"] / 5)
    assert result["incomplete"] == [] and result["unpriced"] == []


def test_blanks_are_left_out_of_totals_and_named():
    result = rs.compute(
        [
            _line("Priced", 100, 1.0, 0.0, price=10.0),
            _line("No activity", 100, None, 0.10, price=10.0),
            _line("No price", 100, 1.0, 0.0),
        ],
        servings_per_unit=5, servings_base=None,
    )
    priced, no_activity, no_price = result["lines"]
    assert no_activity["actual_input_mg"] is None and no_activity["formula_pct"] is None
    assert no_price["cost_per_unit"] is None
    assert priced["g_per_run"] is None          # no batch size, no grams per run
    assert result["totals"]["actual_input_mg"] == 200.0
    assert result["totals"]["g_per_run"] is None
    assert result["totals"]["cost_per_unit"] == pytest.approx(100 / 1e6 * 10 * 5)
    assert result["incomplete"] == ["No activity"]
    assert result["unpriced"] == ["No price"]


def test_nothing_priced_gives_no_cost_not_zero():
    totals = rs.compute([_line("No price", 100, 1.0, 0.0)], 5, 100)["totals"]
    assert totals["cost_per_unit"] is None and totals["cost_per_serving"] is None


# --- saved sheets and remembered activity ----------------------------------------

def test_a_line_resolves_name_part_and_price_from_the_catalog(conn):
    mid = _material(conn, "Salt A", "DTF-1", price=8.0)
    rid = rs.create_record(conn, brand="Acme", servings_per_unit=5)
    rs.add_line(conn, rid, "active", material_id=mid)
    conn.execute("UPDATE materials SET current_price_per_kilo = 9.0 WHERE material_id = ?", (mid,))

    (line,) = rs.get_lines(conn, rid, "active")
    assert (line["name"], line["part_num"], line["price_per_kilo"]) == ("Salt A", "DTF-1", 9.0)


def test_no_history_prefills_blank_then_the_last_used_value(conn):
    mid = _material(conn, "Salt A", "DTF-1")
    first = rs.create_record(conn)
    line_id = rs.add_line(conn, first, "active", material_id=mid)
    (line,) = rs.get_lines(conn, first, "active")
    assert (line["activity"], line["overage"]) == (None, None)

    rs.update_line(conn, line_id, label_claim_mg=1000, activity=0.234, overage=0.10, position=1)
    second = rs.create_record(conn)
    rs.add_line(conn, second, "active", material_id=mid)
    (line,) = rs.get_lines(conn, second, "active")
    assert (line["activity"], line["overage"]) == (0.234, 0.10)
    assert line["label_claim_mg"] is None        # the claim is per sheet, never remembered


def test_overriding_one_line_leaves_other_sheets_alone(conn):
    mid = _material(conn, "Extract", "DTF-2")
    a, b = rs.create_record(conn), rs.create_record(conn)
    line_a = rs.add_line(conn, a, "active", material_id=mid)
    rs.update_line(conn, line_a, label_claim_mg=200, activity=1.0, overage=0.0, position=1)
    line_b = rs.add_line(conn, b, "active", material_id=mid)
    rs.update_line(conn, line_b, label_claim_mg=6, activity=0.03, overage=0.0, position=1)

    assert rs.get_lines(conn, a, "active")[0]["activity"] == 1.0
    assert rs.last_used(conn, material_id=mid) == (0.03, 0.0)


def test_clearing_a_value_keeps_the_remembered_one(conn):
    mid = _material(conn, "Salt A", "DTF-1")
    rid = rs.create_record(conn)
    line_id = rs.add_line(conn, rid, "active", material_id=mid)
    rs.update_line(conn, line_id, label_claim_mg=100, activity=0.36, overage=0.10, position=1)
    rs.update_line(conn, line_id, label_claim_mg=100, activity=None, overage=0.10, position=1)
    assert rs.last_used(conn, material_id=mid) == (0.36, 0.10)


def test_the_schema_refuses_an_activity_over_100_percent(conn):
    mid = _material(conn, "Salt A", "DTF-1")
    rid = rs.create_record(conn)
    line_id = rs.add_line(conn, rid, "active", material_id=mid)
    with pytest.raises(sqlite3.IntegrityError):
        rs.update_line(conn, line_id, label_claim_mg=100, activity=36, overage=0.0, position=1)


def test_sections_keep_their_own_order(conn):
    a, b = _material(conn, "A", "DTF-1"), _material(conn, "B", "DTF-2")
    rid = rs.create_record(conn)
    rs.add_line(conn, rid, "active", material_id=a)
    rs.add_line(conn, rid, "excipient", material_id=b)
    rs.add_line(conn, rid, "active", material_id=b)
    assert [l["name"] for l in rs.get_lines(conn, rid, "active")] == ["A", "B"]
    assert [l["position"] for l in rs.get_lines(conn, rid, "excipient")] == [1]


# --- flavor lines ------------------------------------------------------------------

def test_flavor_lines_come_from_the_profile_without_base(conn):
    mid = _material(conn, "Flavor C", "DTF-3", price=20.0)
    sheet = fs.create_sheet(conn, customer="Acme", servings=30)
    profile = fs.add_profile(conn, sheet, flavor_name="Citrus", base_mg=9000)
    line = fs.add_line(conn, profile, material_id=mid, mg_per_serving=150)
    rid = rs.create_record(conn, flavor_profile_id=profile)

    (flavor,) = rs.flavor_lines(conn, profile)
    assert flavor == {"section": "flavor", "source": "Warehouse", "name": "Flavor C", "part_num": "DTF-3",
                      "label_claim_mg": 150, "activity": 1.0, "overage": 0.0, "price_per_kilo": 20.0}

    fs.update_line(conn, line, mg_per_serving=175, position=1)   # an edit on the flavor sheet
    assert rs.all_lines(conn, rs.get_record(conn, rid))[0]["label_claim_mg"] == 175


def test_deleting_the_flavor_keeps_the_record_sheet(conn):
    sheet = fs.create_sheet(conn)
    profile = fs.add_profile(conn, sheet, flavor_name="Citrus")
    rid = rs.create_record(conn, flavor_profile_id=profile)
    fs.delete_profile(conn, profile)
    assert rs.get_record(conn, rid)["flavor_profile_id"] is None


# --- loaders -------------------------------------------------------------------------

def test_the_etl_leaves_record_sheets_untouched(tmp_path):
    db_path = tmp_path / "etl.db"
    etl.run(CsvInventorySource(SYNTHETIC_CSV), db_path)
    conn = init_db(db_path)
    mid = conn.execute("SELECT material_id FROM materials LIMIT 1").fetchone()[0]
    rid = rs.create_record(conn, brand="Acme")
    line_id = rs.add_line(conn, rid, "active", material_id=mid)
    rs.update_line(conn, line_id, label_claim_mg=100, activity=0.5, overage=0.1, position=1)
    conn.close()

    etl.run(CsvInventorySource(SYNTHETIC_CSV), db_path)
    conn = init_db(db_path)
    assert rs.get_record(conn, rid)["brand"] == "Acme"
    assert rs.get_lines(conn, rid, "active")[0]["activity"] == 0.5
    assert rs.last_used(conn, material_id=mid) == (0.5, 0.1)
    conn.close()


# --- copy blocks ------------------------------------------------------------------------

def test_record_blocks_fill_each_section_in_the_sheet_shape(conn):
    salt = _material(conn, "Salt A", "00123", price=8.0)
    flow = _material(conn, "Flow agent", "DTF-5")
    flavor = _material(conn, "Flavor C", "DTF-3", price=20.0)
    sheet = fs.create_sheet(conn)
    profile = fs.add_profile(conn, sheet, flavor_name="Citrus", base_mg=9000)
    fs.add_line(conn, profile, material_id=flavor, mg_per_serving=150)
    rid = rs.create_record(conn, servings_per_unit=5, servings_base=1000, flavor_profile_id=profile)
    rs.update_line(conn, rs.add_line(conn, rid, "active", material_id=salt),
                   label_claim_mg=1000, activity=0.234, overage=0.10, position=1)
    rs.add_line(conn, rid, "excipient", material_id=flow)   # no activity yet

    blocks = record_blocks(conn, rid)
    assert blocks["actives"]["rows"] == [
        ['="00123"', "Salt A", "1000", "0.234", "0.1", "4700.855", "", "", "8"],
    ]
    assert blocks["second"]["rows"] == [
        ["DTF-5", "Flow agent", "", "", "", "", "", "", ""],
        ["DTF-3", "Flavor C", "150", "1", "0", "150", "", "", "20"],
    ]
    assert blocks["second"]["unpriced"] == ["Flow agent"]
    assert blocks["actives"]["text"].count("\t") == 8


# --- typed lines ---------------------------------------------------------------------------

def test_a_typed_line_carries_its_own_name_and_price(conn):
    rid = rs.create_record(conn, servings_per_unit=5)
    line_id = rs.add_line(conn, rid, "active", typed_name="Green Tea Extract (98% L-Theanine)")
    rs.update_line(conn, line_id, label_claim_mg=150, activity=0.98, overage=0.0, position=1)
    assert rs.set_typed_price(conn, line_id, 60.0)

    (line,) = rs.get_lines(conn, rid, "active")
    assert (line["source"], line["name"], line["part_num"], line["price_per_kilo"]) == (
        "Typed", "Green Tea Extract (98% L-Theanine)", None, 60.0)
    computed = rs.compute([line], 5, 1000)["lines"][0]
    assert computed["actual_input_mg"] == 153.061
    assert computed["cost_per_unit"] == pytest.approx(153.061 / 1e6 * 60.0 * 5)


def test_a_typed_line_without_a_price_is_unpriced_not_free(conn):
    rid = rs.create_record(conn, servings_per_unit=5)
    line_id = rs.add_line(conn, rid, "active", typed_name="New extract")
    rs.update_line(conn, line_id, label_claim_mg=100, activity=1.0, overage=0.0, position=1)
    result = rs.compute(rs.get_lines(conn, rid, "active"), 5, None)
    assert result["unpriced"] == ["New extract"]
    assert result["totals"]["cost_per_unit"] is None


def test_a_catalog_line_price_cannot_be_typed(conn):
    mid = _material(conn, "Salt A", "DTF-1", price=8.0)
    rid = rs.create_record(conn)
    line_id = rs.add_line(conn, rid, "active", material_id=mid)
    assert not rs.set_typed_price(conn, line_id, 1.0)
    assert rs.get_lines(conn, rid, "active")[0]["price_per_kilo"] == 8.0
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE record_lines SET typed_price_per_kilo = 1 WHERE record_line_id = ?", (line_id,))


def test_a_typed_line_is_never_remembered(conn):
    rid = rs.create_record(conn)
    line_id = rs.add_line(conn, rid, "active", typed_name="New extract")
    rs.update_line(conn, line_id, label_claim_mg=100, activity=0.5, overage=0.1, position=1)
    assert conn.execute("SELECT COUNT(*) FROM material_last_used").fetchone()[0] == 0
    second = rs.add_line(conn, rs.create_record(conn), "active", typed_name="New extract")
    assert conn.execute("SELECT activity FROM record_lines WHERE record_line_id = ?", (second,)).fetchone()[0] is None


def test_a_line_needs_exactly_one_source(conn):
    mid = _material(conn, "Salt A", "DTF-1")
    rid = rs.create_record(conn)
    with pytest.raises(sqlite3.IntegrityError):
        rs.add_line(conn, rid, "active")
    with pytest.raises(sqlite3.IntegrityError):
        rs.add_line(conn, rid, "active", material_id=mid, typed_name="Salt A")


def test_a_typed_line_copies_with_a_blank_part_number(conn):
    rid = rs.create_record(conn, servings_per_unit=5)
    line_id = rs.add_line(conn, rid, "active", typed_name="Green Tea Extract (98% L-Theanine)")
    rs.update_line(conn, line_id, label_claim_mg=150, activity=0.98, overage=0.0, position=1)
    rs.set_typed_price(conn, line_id, 60.0)
    assert record_blocks(conn, rid)["actives"]["rows"] == [
        ["", "Green Tea Extract (98% L-Theanine)", "150", "0.98", "0", "153.061", "", "", "60"],
    ]


def test_an_older_record_lines_table_gains_typed_lines_and_keeps_its_rows(tmp_path):
    """A database made by the first F11 commit: catalog-only CHECK, no typed columns."""
    path = tmp_path / "old.db"
    conn = init_db(path)
    mid = _material(conn, "Salt A", "DTF-1")
    rid = rs.create_record(conn, brand="Acme")
    conn.executescript(f"""
        DROP TABLE record_lines;
        CREATE TABLE record_lines (
            record_line_id INTEGER PRIMARY KEY,
            record_sheet_id INTEGER NOT NULL REFERENCES record_sheets(record_sheet_id) ON DELETE CASCADE,
            section TEXT NOT NULL, position INTEGER NOT NULL,
            material_id INTEGER REFERENCES materials(material_id), rd_id TEXT REFERENCES lab_samples(rd_id),
            label_claim_mg REAL, activity REAL, overage REAL,
            CHECK ((material_id IS NULL) <> (rd_id IS NULL)));
        CREATE INDEX idx_record_lines_sheet ON record_lines(record_sheet_id);
        INSERT INTO record_lines (record_sheet_id, section, position, material_id, label_claim_mg, activity, overage)
            VALUES ({rid}, 'active', 1, {mid}, 1000, 0.234, 0.1);
    """)
    conn.close()

    conn = init_db(path)
    (line,) = rs.get_lines(conn, rid, "active")
    assert (line["name"], line["label_claim_mg"], line["activity"]) == ("Salt A", 1000, 0.234)
    rs.add_line(conn, rid, "active", typed_name="New extract")
    assert [l["source"] for l in rs.get_lines(conn, rid, "active")] == ["Warehouse", "Typed"]
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'idx_record_lines_sheet'").fetchone()
    assert not conn.execute("SELECT name FROM sqlite_master WHERE name = 'record_lines_before_typed'").fetchone()
    conn.close()
