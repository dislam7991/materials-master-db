"""The Flavor Sheet object: a header plus flavor profiles, each a BASE amount
and material lines in mg per serving.

Holds exactly what the company Flavor Sheet asks for. Grams per sample are
derived (mg x servings / 1000), never stored. A line references its material
(material_id / rd_id) so name and price resolve at read time, or carries a
typed_name for a material neither catalog has yet. Functions commit their own
writes: a sheet is saved work.
"""

from __future__ import annotations

import sqlite3
from datetime import date

HEADER_FIELDS = ("customer", "product", "quote_id", "servings", "sample_prefix")


def _next_position(conn: sqlite3.Connection, table: str, parent_column: str, parent_id: int) -> int:
    """Return the next free print slot under a parent row (1 when it has none)."""
    (position,) = conn.execute(
        f"SELECT COALESCE(MAX(position), 0) + 1 FROM {table} WHERE {parent_column} = ?",
        (parent_id,),
    ).fetchone()
    return position


def create_sheet(conn: sqlite3.Connection, **header) -> int:
    """Create an empty flavor sheet from HEADER_FIELDS and return its id."""
    cur = conn.execute(
        f"INSERT INTO flavor_sheets ({', '.join(HEADER_FIELDS)}) "
        f"VALUES ({', '.join('?' * len(HEADER_FIELDS))})",
        [header.get(k) for k in HEADER_FIELDS],
    )
    conn.commit()
    return cur.lastrowid


def update_sheet(conn: sqlite3.Connection, flavor_sheet_id: int, **header) -> None:
    """Update the given header fields of a sheet; keys outside HEADER_FIELDS are ignored."""
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
    """Delete a sheet with its profiles and lines (cascade; needs foreign_keys ON, as db.connect sets)."""
    conn.execute("DELETE FROM flavor_sheets WHERE flavor_sheet_id = ?", (flavor_sheet_id,))
    conn.commit()


def list_sheets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return every sheet, newest first, each with its flavor count."""
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
    """Return one sheet's header row, or None if it doesn't exist."""
    return conn.execute(
        "SELECT * FROM flavor_sheets WHERE flavor_sheet_id = ?", (flavor_sheet_id,)
    ).fetchone()


def product_line(sheet) -> str:
    """Return "Customer - Product - Quote ID", skipping blank parts."""
    parts = [sheet[k] for k in ("customer", "product", "quote_id")]
    return " - ".join(p.strip() for p in parts if p and p.strip())


def suggest_sample_id(prefix: str | None, day: date, position: int) -> str:
    """Return a suggested Sample ID, <prefix><YYMMDD>-<NN> (e.g. SMPL260916-01); the user may overwrite it."""
    return f"{(prefix or '').strip()}{day:%y%m%d}-{position:02d}"


def grams_per_sample(mg_per_serving: float | None, servings: float | None) -> float | None:
    """Return grams per sample (mg x servings / 1000), or None if either input is blank.

    A blank amount is not a zero-gram weigh-up.
    """
    if mg_per_serving is None or servings is None:
        return None
    return mg_per_serving * servings / 1000


def line_cost_per_serving(
    mg_per_serving: float | None, price_per_kilo: float | None
) -> float | None:
    """Return one line's cost per serving (mg / 1e6 x price per kilo), or None if either is unknown.

    None, not 0: an unpriced line must be left out of a total, not counted as free.
    """
    if mg_per_serving is None or price_per_kilo is None:
        return None
    return mg_per_serving / 1_000_000 * price_per_kilo


def profile_cost(lines, servings: float | None = None) -> dict:
    """Return a rough cost for a profile's lines: per_serving, per_sample, priced and unpriced counts.

    Summed from priced lines only, so the caller can label it an estimate and
    say what it excludes. A missing price is never filled in; BASE adds no cost.
    """
    per_serving = 0.0
    priced = unpriced = 0
    for line in lines:
        cost = line_cost_per_serving(line["mg_per_serving"], line["price_per_kilo"])
        if cost is None:
            # Only an amountless-but-real line counts as unpriced; a line with no
            # mg yet has nothing to cost and isn't a missing price.
            if line["mg_per_serving"] is not None and line["price_per_kilo"] is None:
                unpriced += 1
            continue
        per_serving += cost
        priced += 1
    return {
        "per_serving": per_serving if priced else None,
        "per_sample": per_serving * servings if priced and servings is not None else None,
        "priced": priced,
        "unpriced": unpriced,
    }


# --- flavor profiles -------------------------------------------------------

def get_profiles(conn: sqlite3.Connection, flavor_sheet_id: int) -> list[sqlite3.Row]:
    """Return a sheet's flavor profiles in print order."""
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
    """Append a flavor profile in the next slot and return its id.

    `copy_lines_from` (another profile's id) copies its BASE and lines; an
    explicit base_mg wins over the copied one.
    """
    next_pos = _next_position(conn, "flavor_profiles", "flavor_sheet_id", flavor_sheet_id)
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
    """Replace a profile's name, Sample ID and BASE amount."""
    conn.execute(
        "UPDATE flavor_profiles SET flavor_name = ?, sample_id = ?, base_mg = ? "
        "WHERE flavor_profile_id = ?",
        (flavor_name, sample_id, base_mg, flavor_profile_id),
    )
    conn.commit()


def delete_profile(conn: sqlite3.Connection, flavor_profile_id: int) -> None:
    """Delete a profile and move the flavors after it up a slot, leaving no gap on the page."""
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
    """Append a line after the profile's existing lines and return its id.

    Pass exactly one of material_id / rd_id / typed_name; the schema rejects anything else.
    """
    next_pos = _next_position(conn, "flavor_profile_lines", "flavor_profile_id", flavor_profile_id)
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
    """Set a line's mg per serving and print position."""
    conn.execute(
        "UPDATE flavor_profile_lines SET mg_per_serving = ?, position = ? "
        "WHERE flavor_profile_line_id = ?",
        (mg_per_serving, position, flavor_profile_line_id),
    )
    conn.commit()


def delete_line(conn: sqlite3.Connection, flavor_profile_line_id: int) -> None:
    """Delete one line."""
    conn.execute(
        "DELETE FROM flavor_profile_lines WHERE flavor_profile_line_id = ?",
        (flavor_profile_line_id,),
    )
    conn.commit()


def get_lines(conn: sqlite3.Connection, flavor_profile_id: int) -> list[sqlite3.Row]:
    """Return a profile's lines in print order, with name, source and price resolved at read time.

    A lab sample prints as flavor name + sample code ("Peach E00000001"): the
    code is what tells two vendors' "Peach" apart on the bench.
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
            END AS name,
            -- NULL for a typed line or an unpriced catalog row. Feeds only the
            -- builder's cost estimate; the printed sheet stays price-free.
            CASE
                WHEN l.material_id IS NOT NULL THEN m.current_price_per_kilo
                WHEN l.rd_id IS NOT NULL THEN ls.price_per_kilo
                ELSE NULL
            END AS price_per_kilo
        FROM flavor_profile_lines l
        LEFT JOIN materials m ON m.material_id = l.material_id
        LEFT JOIN lab_samples ls ON ls.rd_id = l.rd_id
        WHERE l.flavor_profile_id = ?
        ORDER BY l.position, l.flavor_profile_line_id
        """,
        (flavor_profile_id,),
    ).fetchall()
