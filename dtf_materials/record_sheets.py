"""The Sample Record Sheet, built in the app (SPEC F11, experimental).

A product's formula lives in the PL Cost Sheet as blocks of cells people
copy and paste per quote, which no parser can read without mistakes (user,
2026-10-19). So the actives are picked from the catalogs here, like flavor
lines, and the record sheet is a header plus active and excipient lines. The
chosen flavor profile's lines join the second section at Activity 100 % /
Overage 0, read from the profile, never copied in.

Every derived column is computed here, never stored:

  Actual Input (mg) = label claim x (1 + overage) / activity, 3 decimals half-up
  g/run             = Actual Input x servings (base) / 1000
  kg/run            = g/run / 1000
  Formula %         = Actual Input / total Actual Input   (sums to 100 %)
  Cost/Unit         = Actual Input / 1e6 x price/kg x servings per unit (jar)

Two deliberate differences from the company template (layout map §1):
Formula % divides Actual Input, not label claim, by the total (finding 2,
user's call 2026-10-19), and cost uses Actual Input on every row, where the
template costs excipients on label claim (finding 3) — identical while an
excipient is Activity 100 % / Overage 0.

A blank is never 0: a line with no label claim, activity or overage has no
Actual Input, is left out of every total, and is listed in `incomplete`; an
unpriced line has no cost and is listed in `unpriced`. Functions commit their
own writes.
"""

from __future__ import annotations

import sqlite3
from decimal import ROUND_HALF_UP, Decimal

from . import flavor_sheets as fs
from . import queries

HEADER_FIELDS = (
    "brand", "product", "quote_id", "servings_per_unit", "servings_base", "flavor_profile_id",
)
SECTIONS = ("active", "excipient")
DEFAULT_SERVINGS_PER_UNIT = 5  # a sample jar ships the customer 5 servings


# --- the arithmetic ----------------------------------------------------------

def round_half_up(value: float, places: int = 3) -> float:
    """Round like the sheet does (0.0005 -> 0.001), not Python's round-half-to-even."""
    quantum = Decimal(1).scaleb(-places)
    return float(Decimal(repr(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def actual_input(
    label_claim_mg: float | None, activity: float | None, overage: float | None
) -> float | None:
    """Return mg actually weighed per serving, or None if any input is blank.

    Activity 0 is treated as blank: the template's IFERROR prints 0 there and
    the total silently drops the line.
    """
    if label_claim_mg is None or activity is None or overage is None or activity <= 0:
        return None
    return round_half_up(label_claim_mg * (1 + overage) / activity)


def compute(lines: list[dict], servings_per_unit: float | None, servings_base: float | None) -> dict:
    """Return every computed column and the totals for a record sheet's lines.

    `lines` are dicts with name, section ('active' / 'excipient' / 'flavor'),
    label_claim_mg, activity, overage and price_per_kilo. Returns {'lines':
    copies with actual_input_mg, g_per_run, kg_per_run, formula_pct and
    cost_per_unit added (None where an input is blank), 'totals', 'incomplete'
    and 'unpriced' (names)}.
    """
    rows = [dict(l, actual_input_mg=actual_input(l["label_claim_mg"], l["activity"], l["overage"]))
            for l in lines]
    total_mg = sum(r["actual_input_mg"] for r in rows if r["actual_input_mg"] is not None)

    for r in rows:
        mg, price = r["actual_input_mg"], r["price_per_kilo"]
        r["g_per_run"] = mg * servings_base / 1000 if mg is not None and servings_base is not None else None
        r["kg_per_run"] = r["g_per_run"] / 1000 if r["g_per_run"] is not None else None
        r["formula_pct"] = mg / total_mg if mg is not None and total_mg else None
        r["cost_per_serving"] = mg / 1_000_000 * price if mg is not None and price is not None else None
        r["cost_per_unit"] = (
            r["cost_per_serving"] * servings_per_unit
            if r["cost_per_serving"] is not None and servings_per_unit is not None else None
        )

    def cost(sections: tuple[str, ...]) -> float | None:
        """Sum cost per unit over priced lines in `sections`; None when none is priced."""
        costs = [r["cost_per_unit"] for r in rows if r["section"] in sections and r["cost_per_unit"] is not None]
        return sum(costs) if costs else None

    actives_cost, inactives_cost = cost(("active",)), cost(("excipient", "flavor"))
    priced = [c for c in (actives_cost, inactives_cost) if c is not None]
    total_cost = sum(priced) if priced else None
    has_mg = any(r["actual_input_mg"] is not None for r in rows)
    return {
        "lines": rows,
        "totals": {
            "actual_input_mg": total_mg if has_mg else None,
            "g_per_run": total_mg * servings_base / 1000 if has_mg and servings_base is not None else None,
            "kg_per_run": total_mg * servings_base / 1_000_000 if has_mg and servings_base is not None else None,
            "actives_cost_per_unit": actives_cost,
            "inactives_cost_per_unit": inactives_cost,
            "cost_per_unit": total_cost,
            "cost_per_serving": total_cost / servings_per_unit if total_cost is not None and servings_per_unit else None,
        },
        "incomplete": [r["name"] for r in rows if r["actual_input_mg"] is None],
        "unpriced": [r["name"] for r in rows if r["actual_input_mg"] is not None and r["price_per_kilo"] is None],
    }


# --- record sheets -------------------------------------------------------------

def create_record(conn: sqlite3.Connection, **header) -> int:
    """Create a record sheet from HEADER_FIELDS and return its id."""
    cur = conn.execute(
        f"INSERT INTO record_sheets ({', '.join(HEADER_FIELDS)}) "
        f"VALUES ({', '.join('?' * len(HEADER_FIELDS))})",
        [header.get(k) for k in HEADER_FIELDS],
    )
    conn.commit()
    return cur.lastrowid


def update_record(conn: sqlite3.Connection, record_sheet_id: int, **header) -> None:
    """Update the given header fields; keys outside HEADER_FIELDS are ignored."""
    fields = [k for k in header if k in HEADER_FIELDS]
    if not fields:
        return
    conn.execute(
        f"UPDATE record_sheets SET {', '.join(f'{k} = ?' for k in fields)} WHERE record_sheet_id = ?",
        [header[k] for k in fields] + [record_sheet_id],
    )
    conn.commit()


def delete_record(conn: sqlite3.Connection, record_sheet_id: int) -> None:
    """Delete a record sheet and its lines (cascade); the flavor profile it used is kept."""
    conn.execute("DELETE FROM record_sheets WHERE record_sheet_id = ?", (record_sheet_id,))
    conn.commit()


def list_records(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return every record sheet, newest first."""
    return conn.execute("SELECT * FROM record_sheets ORDER BY record_sheet_id DESC").fetchall()


def get_record(conn: sqlite3.Connection, record_sheet_id: int) -> sqlite3.Row | None:
    """Return one record sheet's header row, or None."""
    return conn.execute(
        "SELECT * FROM record_sheets WHERE record_sheet_id = ?", (record_sheet_id,)
    ).fetchone()


def record_title(record) -> str:
    """Return "Brand - Product - Quote ID", skipping blanks."""
    parts = [record[k] for k in ("brand", "product", "quote_id")]
    return " - ".join(p.strip() for p in parts if p and p.strip()) or "(untitled)"


# --- lines ---------------------------------------------------------------------

def last_used(conn: sqlite3.Connection, *, material_id: int | None = None, rd_id: str | None = None) -> tuple:
    """Return the (activity, overage) last used for a material, or (None, None) with no history.

    No history pre-fills blank, never a guessed number.
    """
    column, ref = ("material_id", material_id) if material_id is not None else ("rd_id", rd_id)
    row = conn.execute(
        f"SELECT activity, overage FROM material_last_used WHERE {column} = ?", (ref,)
    ).fetchone()
    return (row["activity"], row["overage"]) if row else (None, None)


def _remember(conn: sqlite3.Connection, material_id, rd_id, activity, overage) -> None:
    """Store a line's activity and overage as the material's last used; a blank keeps the old value."""
    column, ref = ("material_id", material_id) if material_id is not None else ("rd_id", rd_id)
    cur = conn.execute(
        f"UPDATE material_last_used SET activity = COALESCE(?, activity), "
        f"overage = COALESCE(?, overage) WHERE {column} = ?",
        (activity, overage, ref),
    )
    if cur.rowcount == 0 and (activity is not None or overage is not None):
        conn.execute(
            f"INSERT INTO material_last_used ({column}, activity, overage) VALUES (?,?,?)",
            (ref, activity, overage),
        )


def add_line(
    conn: sqlite3.Connection,
    record_sheet_id: int,
    section: str,
    *,
    material_id: int | None = None,
    rd_id: str | None = None,
) -> int:
    """Append a line to a section, pre-filled with the material's last-used activity and overage, and return its id."""
    activity, overage = last_used(conn, material_id=material_id, rd_id=rd_id)
    (position,) = conn.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 FROM record_lines WHERE record_sheet_id = ? AND section = ?",
        (record_sheet_id, section),
    ).fetchone()
    cur = conn.execute(
        "INSERT INTO record_lines (record_sheet_id, section, position, material_id, rd_id, activity, overage) "
        "VALUES (?,?,?,?,?,?,?)",
        (record_sheet_id, section, position, material_id, rd_id, activity, overage),
    )
    conn.commit()
    return cur.lastrowid


def update_line(
    conn: sqlite3.Connection,
    record_line_id: int,
    *,
    label_claim_mg: float | None,
    activity: float | None,
    overage: float | None,
    position: int,
) -> None:
    """Set a line's inputs and position, and remember its activity/overage for the next sheet."""
    conn.execute(
        "UPDATE record_lines SET label_claim_mg = ?, activity = ?, overage = ?, position = ? "
        "WHERE record_line_id = ?",
        (label_claim_mg, activity, overage, position, record_line_id),
    )
    row = conn.execute(
        "SELECT material_id, rd_id FROM record_lines WHERE record_line_id = ?", (record_line_id,)
    ).fetchone()
    if row is not None:
        _remember(conn, row["material_id"], row["rd_id"], activity, overage)
    conn.commit()


def delete_line(conn: sqlite3.Connection, record_line_id: int) -> None:
    """Delete one line."""
    conn.execute("DELETE FROM record_lines WHERE record_line_id = ?", (record_line_id,))
    conn.commit()


def get_lines(conn: sqlite3.Connection, record_sheet_id: int, section: str) -> list[dict]:
    """Return one section's lines in order, with name, Part # and price resolved from the catalog now."""
    rows = conn.execute(
        """
        SELECT l.*,
               CASE WHEN l.material_id IS NOT NULL THEN m.material_name
                    ELSE TRIM(COALESCE(ls.flavor_name, '') || ' ' || COALESCE(ls.sample_code, ''))
               END AS name,
               COALESCE(m.dtf_part_num, ls.dtf_part_num) AS part_num,
               CASE WHEN l.material_id IS NOT NULL THEN m.current_price_per_kilo
                    ELSE ls.price_per_kilo
               END AS price_per_kilo
        FROM record_lines l
        LEFT JOIN materials m ON m.material_id = l.material_id
        LEFT JOIN lab_samples ls ON ls.rd_id = l.rd_id
        WHERE l.record_sheet_id = ? AND l.section = ?
        ORDER BY l.position, l.record_line_id
        """,
        (record_sheet_id, section),
    ).fetchall()
    return [dict(r) for r in rows]


def flavor_lines(conn: sqlite3.Connection, flavor_profile_id: int | None) -> list[dict]:
    """Return the flavor profile's lines as record lines: Activity 100 %, Overage 0, BASE left out.

    BASE is the actives and excipients already on the sheet, so it is never a line.
    """
    if flavor_profile_id is None:
        return []
    return [
        {
            "section": "flavor",
            "name": l["name"],
            "part_num": _part_num(conn, l),
            "label_claim_mg": l["mg_per_serving"],
            "activity": 1.0,
            "overage": 0.0,
            "price_per_kilo": l["price_per_kilo"],
        }
        for l in fs.get_lines(conn, flavor_profile_id)
    ]


def _part_num(conn: sqlite3.Connection, line) -> str | None:
    """Return a flavor line's Part # from its catalog row (None for a typed line)."""
    return queries.record_line_catalog(conn, material_id=line["material_id"], rd_id=line["rd_id"])["part_num"]


def all_lines(conn: sqlite3.Connection, record) -> list[dict]:
    """Return the sheet's lines in print order: actives, excipients, then the flavor profile's lines."""
    rid = record["record_sheet_id"]
    return (
        get_lines(conn, rid, "active")
        + get_lines(conn, rid, "excipient")
        + flavor_lines(conn, record["flavor_profile_id"])
    )
