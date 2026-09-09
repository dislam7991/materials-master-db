"""Read-only queries behind the lookup app.

Kept separate from the Streamlit UI on purpose: these are plain functions
over a sqlite3 connection, so they can be tested from a REPL or reused by a
future CLI/API without importing Streamlit. Every query is parameterized —
user-typed search text never reaches SQL as a string fragment.
"""

from __future__ import annotations

import sqlite3


_LIKE_ESCAPE = "!"


def _like(term: str) -> str:
    """Wrap user input as a LIKE pattern, escaping the wildcards first so a
    search for '100%' or 'B_12' matches literally instead of turning into a
    match-everything pattern. '!' is the escape character (rather than a
    backslash) purely to keep the SQL readable."""
    escaped = (
        term.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )
    return f"%{escaped}%"


def search_materials(conn: sqlite3.Connection, term: str, limit: int = 50) -> list[sqlite3.Row]:
    """Search by DTF Part # or material name (case-insensitive substring).

    Ordered so exact/prefix Part # matches surface first — when someone types
    a part number they almost always want that exact material, not the
    alphabetically-first material whose name happens to contain the string.
    """
    if not term or not term.strip():
        return []
    term = term.strip()
    pattern = _like(term)
    return conn.execute(
        """
        SELECT m.material_id, m.dtf_part_num, m.material_name, m.category,
               m.current_price_per_kilo, s.canonical_name AS supplier
        FROM materials m
        LEFT JOIN suppliers s ON s.supplier_id = m.supplier_id
        WHERE m.dtf_part_num LIKE ? ESCAPE '!'
           OR m.material_name LIKE ? ESCAPE '!'
        ORDER BY
            CASE
                WHEN UPPER(m.dtf_part_num) = UPPER(?) THEN 0
                WHEN UPPER(m.dtf_part_num) LIKE UPPER(?) || '%' THEN 1
                ELSE 2
            END,
            m.material_name
        LIMIT ?
        """,
        (pattern, pattern, term, term, limit),
    ).fetchall()


def search_lab_samples(conn: sqlite3.Connection, term: str, limit: int = 50) -> list[sqlite3.Row]:
    """Search the R&D lab's flavor sample catalog by vendor, flavor name,
    sample code, or DTF Part # (case-insensitive substring), same shape as
    search_materials but against `lab_samples` — a separate table fed by a
    separate Google Sheet, see db/schema.sql.

    Ordered so exact/prefix Sample Code matches surface first, for the same
    reason search_materials prioritizes Part # matches: someone typing a
    code wants that exact sample, not the alphabetically-first flavor whose
    name happens to contain the string.
    """
    if not term or not term.strip():
        return []
    term = term.strip()
    pattern = _like(term)
    return conn.execute(
        """
        SELECT lab_sample_id, vendor, flavor_name, sample_code, dtf_part_num,
               flavor_family, location_lab
        FROM lab_samples
        WHERE vendor LIKE ? ESCAPE '!'
           OR flavor_name LIKE ? ESCAPE '!'
           OR sample_code LIKE ? ESCAPE '!'
           OR dtf_part_num LIKE ? ESCAPE '!'
        ORDER BY
            CASE
                WHEN UPPER(sample_code) = UPPER(?) THEN 0
                WHEN UPPER(sample_code) LIKE UPPER(?) || '%' THEN 1
                ELSE 2
            END,
            flavor_name
        LIMIT ?
        """,
        (pattern, pattern, pattern, pattern, term, term, limit),
    ).fetchall()


def get_lab_sample(conn: sqlite3.Connection, lab_sample_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM lab_samples WHERE lab_sample_id = ?", (lab_sample_id,)
    ).fetchone()


def get_material(conn: sqlite3.Connection, material_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT m.*, s.canonical_name AS supplier
        FROM materials m
        LEFT JOIN suppliers s ON s.supplier_id = m.supplier_id
        WHERE m.material_id = ?
        """,
        (material_id,),
    ).fetchone()


def get_lots(conn: sqlite3.Connection, material_id: int) -> list[sqlite3.Row]:
    """All lots for a material, newest receiving first. Undated lots sort last
    rather than being treated as oldest."""
    return conn.execute(
        """
        SELECT l.*,
               (SELECT GROUP_CONCAT(ll.location, ', ')
                  FROM lot_locations ll WHERE ll.lot_id = l.lot_id) AS locations
        FROM lots l
        WHERE l.material_id = ?
        ORDER BY
            CASE WHEN l.receiving_date IS NULL THEN 1 ELSE 0 END,
            l.receiving_date DESC, l.lot_id DESC
        """,
        (material_id,),
    ).fetchall()


def get_stocked_locations(conn: sqlite3.Connection, material_id: int) -> list[sqlite3.Row]:
    """Where this material can actually be found right now: locations of lots
    that still have stock and aren't flagged for archive. This is the answer
    to the everyday R&D question, which is not the same as 'every location
    this material has ever occupied'.

    On stock figures: the source records one quantity per lot and, separately,
    the location(s) that lot occupies — it never records how the quantity is
    split between them. So a lot spanning three locations contributes its
    full quantity to each row here, and those rows must not be summed. The
    `lot_spans_locations` flag marks exactly those rows so the UI can say so
    rather than implying inventory that doesn't exist. Inventing a split
    (dividing evenly, say) would be fabricating data the company doesn't have.
    """
    return conn.execute(
        """
        SELECT ll.location,
               SUM(l.current_stock) AS stock,
               COUNT(DISTINCT l.lot_id) AS lot_count,
               MAX((SELECT COUNT(*) FROM lot_locations x WHERE x.lot_id = l.lot_id)) > 1
                   AS lot_spans_locations
        FROM lot_locations ll
        JOIN lots l ON l.lot_id = ll.lot_id
        WHERE l.material_id = ?
          AND COALESCE(l.current_stock, 0) > 0
          AND l.ready_to_archive = 0
        GROUP BY ll.location
        ORDER BY stock DESC
        """,
        (material_id,),
    ).fetchall()


def total_stock(conn: sqlite3.Connection, material_id: int) -> float:
    """Total stock on hand, summed per lot (never per location — see
    get_stocked_locations)."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(current_stock), 0)
        FROM lots
        WHERE material_id = ? AND COALESCE(current_stock, 0) > 0
          AND ready_to_archive = 0
        """,
        (material_id,),
    ).fetchone()
    return row[0]


def unlocated_stock(conn: sqlite3.Connection, material_id: int) -> float:
    """Stock sitting in lots whose Locations cell was blank.

    This is material the company physically has and cannot find from the
    record. It has to be reported explicitly: it is counted in the total but
    has no row in the locations table, so without this the two numbers
    disagree for no visible reason.
    """
    row = conn.execute(
        """
        SELECT COALESCE(SUM(current_stock), 0)
        FROM lots
        WHERE material_id = ?
          AND COALESCE(current_stock, 0) > 0
          AND ready_to_archive = 0
          AND lot_id NOT IN (SELECT lot_id FROM lot_locations)
        """,
        (material_id,),
    ).fetchone()
    return row[0]


def get_last_known_locations(conn: sqlite3.Connection, material_id: int) -> list[sqlite3.Row]:
    """Fallback for a material with nothing currently in stock: where its most
    recent lot was stored. 'No stock on hand' is not the same as 'we have no
    idea where this lives', and the app should not render the second when it
    only knows the first."""
    return conn.execute(
        """
        SELECT ll.location, l.receiving_date, l.dtf_lot_num
        FROM lot_locations ll
        JOIN lots l ON l.lot_id = ll.lot_id
        WHERE l.material_id = ?
          AND l.lot_id = (
              SELECT lot_id FROM lots
              WHERE material_id = ?
              ORDER BY
                  CASE WHEN receiving_date IS NULL THEN 1 ELSE 0 END,
                  receiving_date DESC, lot_id DESC
              LIMIT 1
          )
        ORDER BY ll.location
        """,
        (material_id, material_id),
    ).fetchall()


def search_by_location(conn: sqlite3.Connection, prefix: str, limit: int = 200) -> list[sqlite3.Row]:
    """What is stored at (or under) a location code. A bare aisle prefix like
    '6L' matches every position in it.

    Unlike the material-page queries, this one deliberately does NOT filter
    out lots flagged Ready To Archive. The two views answer different
    questions: a material's page answers "can I use this?", where an archived
    lot is a no, while this answers "what is physically on this shelf?", where
    the drum really is sitting there and hiding it would make the app
    disagree with the warehouse. `ready_to_archive` is returned so the caller
    can label those rows rather than silently mixing them in — the flag comes
    straight from the sheet's own Ready To Archive column, so this reports
    what the sheet says and nothing more.
    """
    if not prefix or not prefix.strip():
        return []
    pattern = _like(prefix.strip().upper()).rstrip("%") + "%"
    return conn.execute(
        """
        SELECT ll.location, m.dtf_part_num, m.material_name,
               l.current_stock, l.dtf_lot_num, l.exp_date, l.ready_to_archive
        FROM lot_locations ll
        JOIN lots l ON l.lot_id = ll.lot_id
        JOIN materials m ON m.material_id = l.material_id
        WHERE ll.location LIKE ? ESCAPE '!'
          AND COALESCE(l.current_stock, 0) > 0
        ORDER BY ll.location, m.material_name
        LIMIT ?
        """,
        (pattern, limit),
    ).fetchall()


def price_history(conn: sqlite3.Connection, material_id: int) -> list[sqlite3.Row]:
    """Price per kilo over time — the payoff of storing price on the lot
    rather than overwriting one number on the material."""
    return conn.execute(
        """
        SELECT receiving_date, price_per_kilo
        FROM lots
        WHERE material_id = ? AND price_per_kilo IS NOT NULL
          AND receiving_date IS NOT NULL
        ORDER BY receiving_date
        """,
        (material_id,),
    ).fetchall()


def list_materials(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every material as one flat table, for a spreadsheet-style overview.

    Stock is summed the same way total_stock() does (in-stock, non-archived
    lots only) but inlined here as one query instead of one call per row.
    """
    return conn.execute(
        """
        SELECT m.dtf_part_num, m.material_name, m.category,
               s.canonical_name AS supplier, m.current_price_per_kilo,
               m.allergen,
               COALESCE((
                   SELECT SUM(l.current_stock) FROM lots l
                   WHERE l.material_id = m.material_id
                     AND COALESCE(l.current_stock, 0) > 0
                     AND l.ready_to_archive = 0
               ), 0) AS total_stock
        FROM materials m
        LEFT JOIN suppliers s ON s.supplier_id = m.supplier_id
        ORDER BY m.material_name
        """
    ).fetchall()


def database_summary(conn: sqlite3.Connection) -> dict:
    def scalar(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    return {
        "materials": scalar("SELECT COUNT(*) FROM materials"),
        "lots": scalar("SELECT COUNT(*) FROM lots"),
        "suppliers": scalar("SELECT COUNT(*) FROM suppliers"),
        "locations": scalar("SELECT COUNT(DISTINCT location) FROM lot_locations"),
        "staged_rows": scalar("SELECT COUNT(*) FROM staging_inventory_raw"),
        # Loaded by a separate command from a separate sheet, so it is
        # routinely 0 while the inventory tables are full. The app uses this
        # to explain an empty Lab Samples tab instead of silently returning
        # nothing for every search.
        "lab_samples": scalar("SELECT COUNT(*) FROM lab_samples"),
    }
