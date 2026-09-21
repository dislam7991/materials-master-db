"""Tests for the formula object (Phase F1).

Two things are load-bearing and pinned here:

  1. A line REFERENCES a catalog row, it never copies its name or price. The
     proof is that repricing a material moves an existing formula's total with
     no write to any formula_line — the fabrication SPEC §2 forbids would be a
     stored price drifting from the catalog, and this is what says it can't.
  2. The full-reload ETL never touches formula tables. A formula is the first
     DB data no loader can rebuild, so a reload that cleared it would lose
     authored work; the ETL clears lots and staging, and this proves it leaves
     formulas and formula_lines exactly as they were.

Everything runs against a temp DB seeded directly, plus (for the ETL test) the
committed synthetic CSV, so nothing here needs real data or a Sheets
connection.
"""

from __future__ import annotations

import sqlite3

import pytest

from dtf_materials import etl, formulas
from dtf_materials.db import PROJECT_ROOT, connect, init_db
from dtf_materials.sources import CsvInventorySource

SYNTHETIC_CSV = PROJECT_ROOT / "data" / "synthetic" / "raw_material_inventory.csv"


def _seed_material(conn, name="Citric Acid", price=10.0, part_num="DTF-100"):
    cur = conn.execute(
        "INSERT INTO materials (dtf_part_num, material_name, current_price_per_kilo) VALUES (?,?,?)",
        (part_num, name, price),
    )
    conn.commit()
    return cur.lastrowid


def _seed_lab_sample(conn, rd_id="RD-0001", flavor="Mango", price=25.0):
    conn.execute(
        "INSERT INTO lab_samples (rd_id, flavor_name, price_per_kilo) VALUES (?,?,?)",
        (rd_id, flavor, price),
    )
    conn.commit()
    return rd_id


@pytest.fixture
def conn(tmp_path):
    c = init_db(tmp_path / "test.db")
    yield c
    c.close()


def test_create_and_total_a_material_line(conn):
    material_id = _seed_material(conn, price=10.0)
    formula_id = formulas.create_formula(conn, "Test Batch", batch_size=1.0, batch_unit="kg")

    formulas.add_material_line(conn, formula_id, material_id, amount=2.0, unit="kg")

    assert formulas.formula_total(conn, formula_id) == 20.0  # 2 kg x $10
    lines = formulas.get_lines(conn, formula_id)
    assert len(lines) == 1
    assert lines[0]["name"] == "Citric Acid"       # resolved from the material, not stored
    assert lines[0]["price_per_kilo"] == 10.0
    assert lines[0]["line_cost"] == 20.0


def test_repriced_material_flows_through_an_existing_line(conn):
    """The reference-not-copy invariant: change the catalog price and the
    formula's total moves, with no write to formula_lines."""
    material_id = _seed_material(conn, price=10.0)
    formula_id = formulas.create_formula(conn, "Test Batch")
    formulas.add_material_line(conn, formula_id, material_id, amount=3.0)
    assert formulas.formula_total(conn, formula_id) == 30.0

    line_before = dict(formulas.get_lines(conn, formula_id)[0])
    conn.execute(
        "UPDATE materials SET current_price_per_kilo = ? WHERE material_id = ?",
        (15.0, material_id),
    )
    conn.commit()

    assert formulas.formula_total(conn, formula_id) == 45.0  # 3 kg x the new $15
    line_after = dict(formulas.get_lines(conn, formula_id)[0])
    # The stored line is untouched; only the resolved price/cost changed.
    assert line_after["formula_line_id"] == line_before["formula_line_id"]
    assert line_after["amount"] == line_before["amount"]
    assert line_after["material_id"] == line_before["material_id"]
    assert line_after["price_per_kilo"] == 15.0


def test_lab_sample_line_totals_by_rd_id(conn):
    rd_id = _seed_lab_sample(conn, price=25.0)
    formula_id = formulas.create_formula(conn, "Flavor Trial")

    formulas.add_lab_sample_line(conn, formula_id, rd_id, amount=0.5)

    assert formulas.formula_total(conn, formula_id) == 12.5  # 0.5 kg x $25
    line = formulas.get_lines(conn, formula_id)[0]
    assert line["rd_id"] == "RD-0001"
    assert line["name"] == "Mango"


def test_formula_mixes_material_and_lab_lines(conn):
    material_id = _seed_material(conn, price=10.0)
    rd_id = _seed_lab_sample(conn, price=25.0)
    formula_id = formulas.create_formula(conn, "Mixed")

    formulas.add_material_line(conn, formula_id, material_id, amount=2.0)
    formulas.add_lab_sample_line(conn, formula_id, rd_id, amount=0.5)

    assert formulas.formula_total(conn, formula_id) == 32.5  # 20 + 12.5


def test_uncosted_line_contributes_nothing_rather_than_a_guess(conn):
    """A material with no recorded price can't be costed. The line shows a NULL
    price so the UI can flag it, and the total is a floor, never an invented
    number."""
    priced = _seed_material(conn, name="Priced", price=10.0, part_num="DTF-1")
    unpriced = _seed_material(conn, name="Unpriced", price=None, part_num="DTF-2")
    formula_id = formulas.create_formula(conn, "Partly priced")
    formulas.add_material_line(conn, formula_id, priced, amount=2.0)
    formulas.add_material_line(conn, formula_id, unpriced, amount=5.0)

    assert formulas.formula_total(conn, formula_id) == 20.0  # only the priced line
    lines = formulas.get_lines(conn, formula_id)
    unpriced_line = next(l for l in lines if l["material_id"] == unpriced)
    assert unpriced_line["price_per_kilo"] is None
    assert unpriced_line["line_cost"] is None


def test_line_must_reference_exactly_one_side(conn):
    """The CHECK constraint: a line is a material XOR a lab sample. Both set, or
    neither, is malformed and rejected — a formula line can't be ambiguous
    about what it's costing."""
    material_id = _seed_material(conn)
    rd_id = _seed_lab_sample(conn)
    formula_id = formulas.create_formula(conn, "Bad lines")

    with pytest.raises(sqlite3.IntegrityError):  # both references set
        conn.execute(
            "INSERT INTO formula_lines (formula_id, material_id, rd_id, amount) VALUES (?,?,?,?)",
            (formula_id, material_id, rd_id, 1.0),
        )
    conn.rollback()

    with pytest.raises(sqlite3.IntegrityError):  # neither reference set
        conn.execute(
            "INSERT INTO formula_lines (formula_id, amount) VALUES (?,?)",
            (formula_id, 1.0),
        )
    conn.rollback()


def test_line_cannot_reference_a_nonexistent_row(conn):
    """The two FKs: a line can only point at a real, resolvable row, so a total
    can never silently drop a line that references nothing."""
    formula_id = formulas.create_formula(conn, "Dangling")

    with pytest.raises(sqlite3.IntegrityError):
        formulas.add_material_line(conn, formula_id, material_id=999, amount=1.0)
    conn.rollback()

    with pytest.raises(sqlite3.IntegrityError):
        formulas.add_lab_sample_line(conn, formula_id, rd_id="RD-9999", amount=1.0)
    conn.rollback()


def test_init_db_is_idempotent_for_formula_tables(tmp_path):
    """Re-running init_db (every ETL run does) must not error on the formula
    tables or wipe them — CREATE TABLE IF NOT EXISTS, same contract as the rest
    of the schema."""
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    material_id = _seed_material(conn)
    formula_id = formulas.create_formula(conn, "Persist me")
    formulas.add_material_line(conn, formula_id, material_id, amount=1.0)
    conn.close()

    conn = init_db(db_path)  # second init on the same file
    assert conn.execute("SELECT COUNT(*) FROM formulas").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM formula_lines").fetchone()[0] == 1
    conn.close()


def test_etl_run_leaves_formula_rows_untouched(tmp_path):
    """A full ETL reload clears lots and staging and rebuilds materials — but a
    formula is authored data no loader can rebuild, so the reload must leave
    formulas and formula_lines exactly as they were.

    The formula is built against a material the ETL itself loaded, so the run
    that follows exercises the real path: an upsert over that material, a full
    reset of the derived tables, and the formula pointing into it throughout."""
    db_path = tmp_path / "test.db"
    source = CsvInventorySource(SYNTHETIC_CSV)
    etl.run(source, db_path)  # first load: populate materials from the sheet

    conn = connect(db_path)
    material_id = conn.execute(
        "SELECT material_id FROM materials ORDER BY material_id LIMIT 1"
    ).fetchone()[0]
    formula_id = formulas.create_formula(conn, "Survives a reload")
    formulas.add_material_line(conn, formula_id, material_id, amount=2.0)
    formulas_before = conn.execute("SELECT * FROM formulas ORDER BY formula_id").fetchall()
    lines_before = conn.execute("SELECT * FROM formula_lines ORDER BY formula_line_id").fetchall()
    conn.close()

    etl.run(source, db_path)  # second load: the reload that must not touch formulas

    conn = connect(db_path)
    formulas_after = conn.execute("SELECT * FROM formulas ORDER BY formula_id").fetchall()
    lines_after = conn.execute("SELECT * FROM formula_lines ORDER BY formula_line_id").fetchall()
    conn.close()

    assert [dict(r) for r in formulas_after] == [dict(r) for r in formulas_before]
    assert [dict(r) for r in lines_after] == [dict(r) for r in lines_before]
