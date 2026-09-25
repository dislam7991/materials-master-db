"""Read-only queries behind the lookup app.

Plain functions over a sqlite3 connection, with no Streamlit import. Every
query is parameterized: typed search text never reaches SQL as a fragment.
"""

from __future__ import annotations

import sqlite3


_LIKE_ESCAPE = "!"


def _like(term: str) -> str:
    """Return a %substring% LIKE pattern with the user's % and _ escaped (with '!') to match literally."""
    escaped = (
        term.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )
    return f"%{escaped}%"


def search_materials(conn: sqlite3.Connection, term: str, limit: int = 50) -> list[sqlite3.Row]:
    """Return materials whose Part # or name contains the term, exact/prefix Part # matches first."""
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
    """Return lab samples whose vendor, flavor, sample code or Part # contains the term, exact/prefix code first."""
    if not term or not term.strip():
        return []
    term = term.strip()
    pattern = _like(term)
    return conn.execute(
        """
        SELECT lab_sample_id, rd_id, vendor, flavor_name, sample_code, dtf_part_num,
               flavor_family, location_lab, price_per_kilo
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


# A very short sample code is a substring of half the warehouse, and a wrong
# link is fabricated data. Real codes are 5-7 digits.
MIN_LINKABLE_SAMPLE_CODE_LENGTH = 4

# The two ways a lab sample may be the same thing as a warehouse material, in
# priority order (docs/flavor_sample_sheet_layout.md §2b). Never a fuzzy name
# match: two vendors' "Vanilla" are two materials.
_PART_NUM_MATCH = """
    m.dtf_part_num IS NOT NULL AND ls.dtf_part_num IS NOT NULL
    AND UPPER(m.dtf_part_num) = UPPER(ls.dtf_part_num)
"""
_SAMPLE_CODE_IN_NAME_MATCH = f"""
    ls.sample_code IS NOT NULL
    AND LENGTH(ls.sample_code) >= {MIN_LINKABLE_SAMPLE_CODE_LENGTH}
    AND INSTR(UPPER(m.material_name), UPPER(ls.sample_code)) > 0
"""


def _linked_pairs(
    conn: sqlite3.Connection,
    material_ids: list[int],
    lab_sample_ids: list[int],
) -> list[sqlite3.Row]:
    """Return every (material, lab sample) link that touches one of the given ids on either side.

    Matched at read time, not stored, so a reload never leaves a stale link.
    Every match is returned: picking one could hide a second real material.
    """
    if not material_ids and not lab_sample_ids:
        return []
    material_slots = ",".join("?" * len(material_ids))
    lab_slots = ",".join("?" * len(lab_sample_ids))
    return conn.execute(
        f"""
        SELECT m.material_id, m.dtf_part_num, m.material_name,
               ls.lab_sample_id, ls.sample_code, ls.flavor_name, ls.vendor,
               CASE WHEN {_PART_NUM_MATCH} THEN 'Part #'
                    ELSE 'Sample code in name' END AS match_rule
        FROM materials m
        JOIN lab_samples ls
          ON ({_PART_NUM_MATCH}) OR ({_SAMPLE_CODE_IN_NAME_MATCH})
        WHERE m.material_id IN ({material_slots})
           OR ls.lab_sample_id IN ({lab_slots})
        ORDER BY m.material_name, ls.flavor_name, ls.lab_sample_id
        """,
        (*material_ids, *lab_sample_ids),
    ).fetchall()


def _combined_result(kind: str, material=None, lab=None, match_rule=None) -> dict:
    """Return one search_warehouse_and_lab row, with the same keys whichever sides exist."""
    return {
        "kind": kind,
        "match_rule": match_rule,
        "material_id": material["material_id"] if material else None,
        "dtf_part_num": material["dtf_part_num"] if material else None,
        "material_name": material["material_name"] if material else None,
        "lab_sample_id": lab["lab_sample_id"] if lab else None,
        "sample_code": lab["sample_code"] if lab else None,
        "flavor_name": lab["flavor_name"] if lab else None,
        "vendor": lab["vendor"] if lab else None,
    }


def search_warehouse_and_lab(
    conn: sqlite3.Connection, term: str, limit: int = 50
) -> list[dict]:
    """Search both catalogs and return rows labelled Both (with match_rule), Warehouse or Lab.

    Returns identity and labels only; callers fetch detail with the existing
    per-side queries. `limit` applies to each side, so a term matching both
    catalogs can return more than `limit` rows.
    """
    materials = search_materials(conn, term, limit)
    lab_samples = search_lab_samples(conn, term, limit)
    pairs = _linked_pairs(
        conn,
        [r["material_id"] for r in materials],
        [r["lab_sample_id"] for r in lab_samples],
    )

    by_material: dict[int, list[sqlite3.Row]] = {}
    by_lab: dict[int, list[sqlite3.Row]] = {}
    for pair in pairs:
        by_material.setdefault(pair["material_id"], []).append(pair)
        by_lab.setdefault(pair["lab_sample_id"], []).append(pair)

    # Warehouse hits first, then lab hits, each in the relevance order its own
    # search already put them in (exact Part #/Sample Code first).
    results: list[dict] = []
    paired: set[tuple[int, int]] = set()

    def add_pair(pair: sqlite3.Row) -> None:
        """Append a linked pair once, however many searches found it."""
        key = (pair["material_id"], pair["lab_sample_id"])
        if key in paired:
            return
        paired.add(key)
        results.append(
            _combined_result("Both", pair, pair, match_rule=pair["match_rule"])
        )

    for material in materials:
        links = by_material.get(material["material_id"], [])
        if not links:
            results.append(_combined_result("Warehouse", material=material))
        for pair in links:
            add_pair(pair)

    for lab_sample in lab_samples:
        links = by_lab.get(lab_sample["lab_sample_id"], [])
        if not links:
            results.append(_combined_result("Lab", lab=lab_sample))
        for pair in links:
            add_pair(pair)

    return results


def get_lab_sample(conn: sqlite3.Connection, lab_sample_id: int) -> sqlite3.Row | None:
    """Return one lab sample's full row, or None."""
    return conn.execute(
        "SELECT * FROM lab_samples WHERE lab_sample_id = ?", (lab_sample_id,)
    ).fetchone()


def get_material(conn: sqlite3.Connection, material_id: int) -> sqlite3.Row | None:
    """Return one material's row with its supplier's canonical name, or None."""
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
    """Return a material's lots with their locations, newest first and undated last."""
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
    """Return each location holding in-stock, unarchived lots of a material, with stock and lot count.

    The sheet never records how a lot splits across locations, so a lot in
    three places counts in full on each row; `lot_spans_locations` marks those
    rows, which must not be summed. Inventing a split would fabricate data.
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
    """Return a material's stock on hand, summed per lot (never per location)."""
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
    """Return stock in lots with no recorded location: counted in the total, absent from the locations table."""
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
    """Return the locations of a material's most recent lot, for when nothing is in stock."""
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
    """Return in-stock lots at locations matching the typed code (e.g. "6L" for a whole aisle).

    Archived lots are included, since they are still physically on the shelf;
    `ready_to_archive` is returned so the caller can label them.
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
    """Return (receiving_date, price_per_kilo) for a material's dated, priced lots, oldest first."""
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
    """Return every material with its supplier, price and stock (summed as total_stock does)."""
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


def record_line_catalog(
    conn: sqlite3.Connection,
    *,
    material_id: int | None = None,
    rd_id: str | None = None,
) -> dict:
    """Return {'part_num', 'price_per_kilo'} for a line's referenced catalog row; either may be None.

    Looked up at render time, so a reprice flows through. A missing price stays
    None so the sheet shows a blank, never 0.
    """
    if material_id is not None:
        row = conn.execute(
            "SELECT dtf_part_num, current_price_per_kilo FROM materials WHERE material_id = ?",
            (material_id,),
        ).fetchone()
    elif rd_id is not None:
        row = conn.execute(
            "SELECT dtf_part_num, price_per_kilo FROM lab_samples WHERE rd_id = ?",
            (rd_id,),
        ).fetchone()
    else:
        row = None
    if row is None:
        return {"part_num": None, "price_per_kilo": None}
    return {"part_num": row[0], "price_per_kilo": row[1]}


def database_summary(conn: sqlite3.Connection) -> dict:
    """Return row counts for the header metrics and the empty-lab-catalog check."""
    def scalar(sql: str) -> int:
        """Run a single-value query and return the value."""
        return conn.execute(sql).fetchone()[0]

    return {
        "materials": scalar("SELECT COUNT(*) FROM materials"),
        "lots": scalar("SELECT COUNT(*) FROM lots"),
        "suppliers": scalar("SELECT COUNT(*) FROM suppliers"),
        "locations": scalar("SELECT COUNT(DISTINCT location) FROM lot_locations"),
        "staged_rows": scalar("SELECT COUNT(*) FROM staging_inventory_raw"),
        # Routinely 0: the lab catalog is loaded by its own command.
        "lab_samples": scalar("SELECT COUNT(*) FROM lab_samples"),
    }
