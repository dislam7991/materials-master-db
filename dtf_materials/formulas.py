"""The formula object: a recipe whose lines reference catalog rows.

Superseded in the UI by the Flavor Sheet (SPEC F2); kept until F2e decides
whether the Sample Record Sheet needs it. A line references a material or lab
sample and never copies its name or price, so both resolve at read time and a
reprice flows through every formula. Functions commit their own writes.
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
    """Create an empty formula and return its formula_id."""
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
    """Add a line referencing a warehouse material and return its id (the FK rejects unknown ids)."""
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
    """Add a line referencing a lab sample by its stable RD-ID and return its id (the FK rejects unknown ids)."""
    cur = conn.execute(
        "INSERT INTO formula_lines (formula_id, rd_id, amount, unit) VALUES (?,?,?,?)",
        (formula_id, rd_id, amount, unit),
    )
    conn.commit()
    return cur.lastrowid


def list_formulas(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return every formula, newest first, each with its line count."""
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
    """Return one formula's header row, or None."""
    return conn.execute(
        "SELECT * FROM formulas WHERE formula_id = ?", (formula_id,)
    ).fetchone()


def get_lines(conn: sqlite3.Connection, formula_id: int) -> list[sqlite3.Row]:
    """Return a formula's lines with name, price and line_cost resolved from the catalog at read time.

    line_cost is NULL when the amount or price is unknown, never a guessed number.
    """
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
    """Return the formula's total cost at current catalog prices; uncosted lines are left out."""
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
