"""The Flavor Sheet object: a header plus any number of flavor profiles, each a
BASE amount and material lines in mg per serving.

Built backwards from the deliverable (SPEC Phase F): the Flavor Sheet is what R&D
fills daily, so this object holds exactly what that sheet asks for and nothing
more — customer, product, quote ID, servings per flavor, and per flavor its
materials and mg per serving. Grams per sample are derived (mg x servings /
1000), never stored, so changing the servings can't leave a stale weight behind.

Like formulas.py, this module owns both the write side and the read side; the
Streamlit tab drives these functions and the xlsx renderer
(flavor_sheet_xlsx.py) consumes get_lines output — neither reimplements them.
Functions commit their own writes: a sheet is saved work, one local writer.

A line references its material (material_id / rd_id) so a renamed material
reaches every sheet, or carries a typed_name for a material neither catalog has
yet — see the flavor_sheets comment in db/schema.sql for why that's allowed here.
"""

from __future__ import annotations

import sqlite3
from datetime import date

HEADER_FIELDS = ("customer", "product", "quote_id", "servings", "sample_prefix")


def create_sheet(conn: sqlite3.Connection, **header) -> int:
    """Create an empty flavor sheet from HEADER_FIELDS and return its id."""
    values = [header.get(k) for k in HEADER_FIELDS]
    cur = conn.execute(
        f"INSERT INTO flavor_sheets ({', '.join(HEADER_FIELDS)}) VALUES (?,?,?,?,?)",
        values,
    )
    conn.commit()
    return cur.lastrowid


def update_sheet(conn: sqlite3.Connection, flavor_sheet_id: int, **header) -> None:
    fields = [k for k in header if k in HEADER_FIELDS]
    if not fields:
        return
    conn.execute(
        f"UPDATE flavor_sheets SET {', '.join(f'{k} = ?' for k in fields)} "
        "WHERE flavor_sheet_id = ?",
        [header[k] for k in fields] + [flavor_sheet_id],
    )
    conn.commit()


def delete_sheet(conn: sqlite3.Connection, flavor_sheet_id: int) -> None:
    """Delete a sheet with its profiles and lines (ON DELETE CASCADE — which
    needs foreign_keys ON, as db.connect and the app's connection both set)."""
    conn.execute("DELETE FROM flavor_sheets WHERE flavor_sheet_id = ?", (flavor_sheet_id,))
    conn.commit()


def list_sheets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every sheet, newest first (the one you're still working on), with its
    flavor count so the picker can tell an empty draft from a finished sheet."""
    return conn.execute(
        """
        SELECT s.*,
               (SELECT COUNT(*) FROM flavor_profiles p
                 WHERE p.flavor_sheet_id = s.flavor_sheet_id) AS flavor_count
        FROM flavor_sheets s
        ORDER BY s.flavor_sheet_id DESC
        """
    ).fetchall()


def get_sheet(conn: sqlite3.Connection, flavor_sheet_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM flavor_sheets WHERE flavor_sheet_id = ?", (flavor_sheet_id,)
    ).fetchone()


def product_line(sheet) -> str:
    """The sheet's one identifying line — Customer - Product - Quote ID — with
    blanks skipped rather than printed as empty separators."""
    parts = [sheet[k] for k in ("customer", "product", "quote_id")]
    return " - ".join(p.strip() for p in parts if p and p.strip())


def suggest_sample_id(prefix: str | None, day: date, position: int) -> str:
    """<prefix><YYMMDD>-<NN>, the shape of the Sample IDs on existing sheets
    (e.g. SMPL260916-01): the date the sheet is made and the flavor's slot.
    Only a suggestion — the user can overwrite it, and nothing checks it."""
    return f"{(prefix or '').strip()}{day:%y%m%d}-{position:02d}"


def grams_per_sample(mg_per_serving: float | None, servings: float | None) -> float | None:
    """The sheet's g column: mg x servings / 1000. None when either input is
    missing — a blank amount is not a zero-gram weigh-up."""
    if mg_per_serving is None or servings is None:
        return None
    return mg_per_serving * servings / 1000


# --- flavor profiles -------------------------------------------------------

def get_profiles(conn: sqlite3.Connection, flavor_sheet_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM flavor_profiles WHERE flavor_sheet_id = ? "
        "ORDER BY position, flavor_profile_id",
        (flavor_sheet_id,),
    ).fetchall()


def add_profile(
    conn: sqlite3.Connection,
    flavor_sheet_id: int,
    flavor_name: str | None = None,
    sample_id: str | None = None,
    base_mg: float | None = None,
    copy_lines_from: int | None = None,
) -> int:
    """Append a flavor profile in the next slot. `copy_lines_from` (another
    profile's id) copies its BASE amount and lines — flavors on one sheet
    usually share the base, acids and sweeteners and differ only in flavor and
    color, so the second flavor starts from the first instead of from nothing.
    A base_mg passed explicitly wins over the copied one."""
    (next_pos,) = conn.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 FROM flavor_profiles WHERE flavor_sheet_id = ?",
        (flavor_sheet_id,),
    ).fetchone()
    if copy_lines_from is not None and base_mg is None:
        src = conn.execute(
            "SELECT base_mg FROM flavor_profiles WHERE flavor_profile_id = ?",
            (copy_lines_from,),
        ).fetchone()
        base_mg = src["base_mg"] if src else None
    cur = conn.execute(
        "INSERT INTO flavor_profiles (flavor_sheet_id, position, flavor_name, sample_id, base_mg) "
        "VALUES (?,?,?,?,?)",
        (flavor_sheet_id, next_pos, flavor_name, sample_id, base_mg),
    )
    profile_id = cur.lastrowid
    if copy_lines_from is not None:
        conn.execute(
            """
            INSERT INTO flavor_profile_lines
                (flavor_profile_id, position, material_id, rd_id, typed_name, mg_per_serving)
            SELECT ?, position, material_id, rd_id, typed_name, mg_per_serving
            FROM flavor_profile_lines WHERE flavor_profile_id = ?
            """,
            (profile_id, copy_lines_from),
        )
    conn.commit()
    return profile_id


def update_profile(
    conn: sqlite3.Connection,
    flavor_profile_id: int,
    flavor_name: str | None,
    sample_id: str | None,
    base_mg: float | None,
) -> None:
    conn.execute(
        "UPDATE flavor_profiles SET flavor_name = ?, sample_id = ?, base_mg = ? "
        "WHERE flavor_profile_id = ?",
        (flavor_name, sample_id, base_mg, flavor_profile_id),
    )
    conn.commit()


def delete_profile(conn: sqlite3.Connection, flavor_profile_id: int) -> None:
    """Delete a profile and close the gap, so the flavors after it move up a
    slot instead of leaving an empty box on the printed page."""
    row = conn.execute(
        "SELECT flavor_sheet_id, position FROM flavor_profiles WHERE flavor_profile_id = ?",
        (flavor_profile_id,),
    ).fetchone()
    if row is None:
        return
    conn.execute("DELETE FROM flavor_profiles WHERE flavor_profile_id = ?", (flavor_profile_id,))
    conn.execute(
        "UPDATE flavor_profiles SET position = position - 1 "
        "WHERE flavor_sheet_id = ? AND position > ?",
        (row["flavor_sheet_id"], row["position"]),
    )
    conn.commit()


# --- lines -----------------------------------------------------------------

def add_line(
    conn: sqlite3.Connection,
    flavor_profile_id: int,
    *,
    material_id: int | None = None,
    rd_id: str | None = None,
    typed_name: str | None = None,
    mg_per_serving: float | None = None,
) -> int:
    """Append a line, printed after the profile's existing lines. Exactly one
    of material_id / rd_id / typed_name — the schema CHECK rejects anything
    else, and the FKs reject an id no catalog row carries."""
    (next_pos,) = conn.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 FROM flavor_profile_lines WHERE flavor_profile_id = ?",
        (flavor_profile_id,),
    ).fetchone()
    cur = conn.execute(
        """
        INSERT INTO flavor_profile_lines
            (flavor_profile_id, position, material_id, rd_id, typed_name, mg_per_serving)
        VALUES (?,?,?,?,?,?)
        """,
        (flavor_profile_id, next_pos, material_id, rd_id, typed_name, mg_per_serving),
    )
    conn.commit()
    return cur.lastrowid


def update_line(
    conn: sqlite3.Connection,
    flavor_profile_line_id: int,
    *,
    mg_per_serving: float | None,
    position: int,
) -> None:
    conn.execute(
        "UPDATE flavor_profile_lines SET mg_per_serving = ?, position = ? "
        "WHERE flavor_profile_line_id = ?",
        (mg_per_serving, position, flavor_profile_line_id),
    )
    conn.commit()


def delete_line(conn: sqlite3.Connection, flavor_profile_line_id: int) -> None:
    conn.execute(
        "DELETE FROM flavor_profile_lines WHERE flavor_profile_line_id = ?",
        (flavor_profile_line_id,),
    )
    conn.commit()


def get_lines(conn: sqlite3.Connection, flavor_profile_id: int) -> list[sqlite3.Row]:
    """The profile's lines in print order, each with the name the sheet prints
    resolved from its source at read time:

    - warehouse material: its material_name;
    - lab sample: flavor name + sample code (e.g. "Peach E00000001") — how
      existing flavor sheets name a lab flavor, since the code is what tells
      two vendors' "Peach" apart on the bench;
    - typed: the typed name, with `source` = 'Typed' so the UI can flag it.
    """
    return conn.execute(
        """
        SELECT
            l.flavor_profile_line_id,
            l.position,
            l.material_id,
            l.rd_id,
            l.typed_name,
            l.mg_per_serving,
            CASE
                WHEN l.material_id IS NOT NULL THEN 'Warehouse'
                WHEN l.rd_id IS NOT NULL THEN 'Lab'
                ELSE 'Typed'
            END AS source,
            CASE
                WHEN l.material_id IS NOT NULL THEN m.material_name
                WHEN l.rd_id IS NOT NULL THEN
                    TRIM(COALESCE(ls.flavor_name, '') || ' ' || COALESCE(ls.sample_code, ''))
                ELSE l.typed_name
            END AS name
        FROM flavor_profile_lines l
        LEFT JOIN materials m ON m.material_id = l.material_id
        LEFT JOIN lab_samples ls ON ls.rd_id = l.rd_id
        WHERE l.flavor_profile_id = ?
        ORDER BY l.position, l.flavor_profile_line_id
        """,
        (flavor_profile_id,),
    ).fetchall()
