"""Tests for the flavor-lines copy block (record_copy_block.py, SPEC F2i).

The block is pasted into column A of a manager-built Sample Record Sheet and
spreads over A:I, so what's pinned here is exactly what decides where each
value lands: nine fields per row in the sheet's column order, one row per
profile line with BASE left out, the D/E/F defaults, and blanks that stay
blank — a missing Part # or price is an empty field, never "None" or 0, and a
name with a tab or newline in it can't push the fields after it sideways.
"""

from __future__ import annotations

import pytest

from dtf_materials import flavor_sheets as fs
from dtf_materials.db import init_db
from dtf_materials.record_copy_block import build_block


@pytest.fixture
def conn(tmp_path):
    c = init_db(tmp_path / "test.db")
    yield c
    c.close()


def _material(conn, name, part, price):
    cur = conn.execute(
        "INSERT INTO materials (dtf_part_num, material_name, current_price_per_kilo) VALUES (?,?,?)",
        (part, name, price),
    )
    conn.commit()
    return cur.lastrowid


def _profile(conn, base_mg=8000):
    sheet = fs.create_sheet(conn, customer="Acme", product="Pre-Workout", servings=4)
    return fs.add_profile(conn, sheet, "Berry", "AC260925-01", base_mg=base_mg)


def test_one_row_per_line_in_column_order_with_defaults(conn):
    profile = _profile(conn)
    fs.add_line(conn, profile, material_id=_material(conn, "Berry Flavor", "FL-1001", 42.5), mg_per_serving=150)
    fs.add_line(conn, profile, material_id=_material(conn, "Sucralose", "RM-2002", 80), mg_per_serving=12.5)

    block = build_block(conn, profile)

    # BASE (8000 mg) is the actives already on the sheet — not a row here.
    assert block["rows"] == [
        ["FL-1001", "Berry Flavor", "150", "1", "0", "150", "", "", "42.5"],
        ["RM-2002", "Sucralose", "12.5", "1", "0", "12.5", "", "", "80"],
    ]
    assert block["text"] == (
        "FL-1001\tBerry Flavor\t150\t1\t0\t150\t\t\t42.5\n"
        "RM-2002\tSucralose\t12.5\t1\t0\t12.5\t\t\t80"
    )
    assert block["unpriced"] == []
    assert "8000" not in block["text"]


def test_blank_part_and_blank_price_stay_empty_fields(conn):
    profile = _profile(conn)
    # Lab-only sample: no Part #, no price on file.
    conn.execute(
        "INSERT INTO lab_samples (rd_id, flavor_name, sample_code, dtf_part_num, price_per_kilo) "
        "VALUES ('RD-0001','Peach','E00000001',NULL,NULL)"
    )
    conn.commit()
    fs.add_line(conn, profile, rd_id="RD-0001", mg_per_serving=90)
    # Typed line: nothing backs it, and no amount yet.
    fs.add_line(conn, profile, typed_name="Mystery Mango")

    block = build_block(conn, profile)

    assert block["rows"] == [
        ["", "Peach E00000001", "90", "1", "0", "90", "", "", ""],
        ["", "Mystery Mango", "", "1", "0", "", "", "", ""],
    ]
    assert "None" not in block["text"]
    assert block["unpriced"] == ["Peach E00000001", "Mystery Mango"]


def test_every_row_has_nine_fields_even_with_tabs_and_newlines_in_a_name(conn):
    profile = _profile(conn)
    fs.add_line(conn, profile, typed_name="Vanilla\tBean\r\nNatural", mg_per_serving=40)
    fs.add_line(conn, profile, material_id=_material(conn, "Citric\nAcid", "RM-3003", 5), mg_per_serving=300)

    block = build_block(conn, profile)
    lines = block["text"].split("\n")

    assert len(lines) == 2
    assert all(len(line.split("\t")) == 9 for line in lines)
    assert [r[1] for r in block["rows"]] == ["Vanilla Bean Natural", "Citric Acid"]
    assert lines[1].split("\t")[8] == "5"


def test_leading_zero_part_numbers_survive_excel(conn):
    profile = _profile(conn)
    fs.add_line(conn, profile, material_id=_material(conn, "Salt", "00123", 3), mg_per_serving=10)
    fs.add_line(conn, profile, material_id=_material(conn, "Sugar", "4567", 2), mg_per_serving=10)

    parts = [r[0] for r in build_block(conn, profile)["rows"]]

    # ="00123" is text in Excel; a plain 00123 would paste as the number 123.
    assert parts == ['="00123"', "4567"]


def test_a_reprice_reaches_the_next_block(conn):
    material_id = _material(conn, "Berry Flavor", "FL-1001", 42.5)
    profile = _profile(conn)
    fs.add_line(conn, profile, material_id=material_id, mg_per_serving=150)
    assert build_block(conn, profile)["rows"][0][8] == "42.5"

    conn.execute("UPDATE materials SET current_price_per_kilo = 55 WHERE material_id = ?", (material_id,))
    conn.commit()
    assert build_block(conn, profile)["rows"][0][8] == "55"


def test_an_empty_profile_gives_an_empty_block(conn):
    assert build_block(conn, _profile(conn)) == {"rows": [], "text": "", "unpriced": []}
