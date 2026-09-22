"""The formula object: create a recipe, add lines that reference catalog rows,
view and total it.

A formula is the first DB data no loader can rebuild — it's authored here, not
pulled from a sheet (see db/schema.sql). So this module owns both the write side
(create/add) and the read side (view/total) of that one object, the way
lab_samples.py owns the lab catalog; the Streamlit builder (F2) drives these
functions, it doesn't reimplement them.

The load-bearing rule (SPEC.md Phase F design reference): a line REFERENCES a
row, it never copies the row's name or price. So the name and price shown for a
line are resolved at read time from the material or lab sample it points at, and
a repriced material flows through every formula that uses it without touching a
single formula_line. That read-time resolution is why totals live here rather
than being stored — a stored total would silently disagree with the catalog the
moment a price changed. (Snapshotting a formula's numbers at *generation* time,
so a sent flavor sheet can't be contradicted by a reprint, is F4's job, not
this one — the object under edit always reflects the current catalog.)

Functions take a sqlite3 connection and commit their own writes: a formula is
saved work (one writer, low volume — the same rationale the project chose SQLite
on), so "add a line" should persist, not wait for a caller to remember to
commit.
"""

from __future__ import annotations

import sqlite3


def create_formula(
    conn: sqlite3.Connection,
    name: str,
    batch_size: float | None = None,
    batch_unit: str | None = None,
    notes: str | None = None,
) -> int:
    """Create an empty formula and return its formula_id. `name` and the batch
    fields are header values no catalog holds — the only free-typed inputs a
    formula carries; every material still comes in by reference (add_*_line)."""
    cur = conn.execute(
        "INSERT INTO formulas (name, batch_size, batch_unit, notes) VALUES (?,?,?,?)",
        (name, batch_size, batch_unit, notes),
    )
    conn.commit()
    return cur.lastrowid


def add_material_line(
    conn: sqlite3.Connection,
    formula_id: int,
    material_id: int,
    amount: float | None = None,
    unit: str | None = None,
) -> int:
    """Add a line referencing an adopted warehouse material by material_id.

    The FK on material_id means a nonexistent id is rejected here, not
    discovered later as a line that totals to nothing — a formula can only
    point at a real material."""
    cur = conn.execute(
        "INSERT INTO formula_lines (formula_id, material_id, amount, unit) VALUES (?,?,?,?)",
        (formula_id, material_id, amount, unit),
    )
    conn.commit()
    return cur.lastrowid


def add_lab_sample_line(
    conn: sqlite3.Connection,
    formula_id: int,
    rd_id: str,
    amount: float | None = None,
    unit: str | None = None,
) -> int:
    """Add a line referencing a lab sample by its stable RD-ID.

    RD-ID rather than lab_sample_id on purpose: lab_sample_id is a full-reload
    surrogate that can move, RD-ID is the sample's stable identity that survives
    a reload (see db/schema.sql), so the reference stays pointed at the same
    sample across lab-sheet loads. The FK rejects an RD-ID no sample carries."""
    cur = conn.execute(
        "INSERT INTO formula_lines (formula_id, rd_id, amount, unit) VALUES (?,?,?,?)",
        (formula_id, rd_id, amount, unit),
    )
    conn.commit()
    return cur.lastrowid


def list_formulas(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every formula, newest first, each with its line count — enough to fill a
    picker in the builder without loading each formula's lines.

    Newest first because the formula you just started is the one you're most
    likely still editing; the line count lets the picker say "(3 lines)" so an
    empty draft is distinguishable from a built recipe at a glance."""
    return conn.execute(
        """
        SELECT f.formula_id, f.name, f.batch_size, f.batch_unit, f.notes,
               f.created_at,
               (SELECT COUNT(*) FROM formula_lines fl
                 WHERE fl.formula_id = f.formula_id) AS line_count
        FROM formulas f
        ORDER BY f.formula_id DESC
        """
    ).fetchall()


def get_formula(conn: sqlite3.Connection, formula_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM formulas WHERE formula_id = ?", (formula_id,)
    ).fetchone()


def get_lines(conn: sqlite3.Connection, formula_id: int) -> list[sqlite3.Row]:
    """Every line of the formula with its name and unit price resolved from the
    catalog row it references — not from anything stored on the line.

    `line_cost` is amount x price_per_kilo, or NULL when either is unknown: a
    line whose material has no recorded price (or whose amount isn't set yet)
    can't be costed, and inventing a number would be exactly the fabrication
    SPEC §2 forbids. The total (formula_total) reflects that — an uncosted line
    contributes nothing rather than a guess, so a total is only ever a floor
    when some line lacks a price. The UI reads `price_per_kilo IS NULL` to say
    which lines those are instead of a total that quietly understates."""
    return conn.execute(
        """
        SELECT
            fl.formula_line_id,
            fl.material_id,
            fl.rd_id,
            fl.amount,
            fl.unit,
            COALESCE(m.material_name, ls.flavor_name) AS name,
            COALESCE(m.current_price_per_kilo, ls.price_per_kilo) AS price_per_kilo,
            fl.amount * COALESCE(m.current_price_per_kilo, ls.price_per_kilo) AS line_cost
        FROM formula_lines fl
        LEFT JOIN materials m ON m.material_id = fl.material_id
        LEFT JOIN lab_samples ls ON ls.rd_id = fl.rd_id
        WHERE fl.formula_id = ?
        ORDER BY fl.formula_line_id
        """,
        (formula_id,),
    ).fetchall()


def formula_total(conn: sqlite3.Connection, formula_id: int) -> float:
    """Total cost of the formula: sum of each line's amount x current price,
    resolved live from the catalog. Uncosted lines (missing amount or price)
    are excluded by SUM rather than counted as zero-with-confidence — see
    get_lines. Re-run it after a material is repriced and the number moves,
    with no change to any formula_line: that's the whole point of referencing."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(
                   fl.amount * COALESCE(m.current_price_per_kilo, ls.price_per_kilo)
               ), 0)
        FROM formula_lines fl
        LEFT JOIN materials m ON m.material_id = fl.material_id
        LEFT JOIN lab_samples ls ON ls.rd_id = fl.rd_id
        WHERE fl.formula_id = ?
        """,
        (formula_id,),
    ).fetchone()
    return row[0]
