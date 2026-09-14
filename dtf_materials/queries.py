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


# Rule 2 below matches a sample code found inside a material name. A very
# short code is a substring of half the warehouse, and a wrong link is
# fabricated data — which outranks convenience here (SPEC.md section 2). Real
# codes are 5-7 digits, so this floor excludes nothing that exists.
MIN_LINKABLE_SAMPLE_CODE_LENGTH = 4

# The two ways a lab sample is allowed to be the same thing as a warehouse
# material, in priority order, decided in E2 and written up in
# docs/flavor_sample_sheet_layout.md section 2b. Both loaders store a blank
# cell as NULL (cleaning.clean_text), so IS NOT NULL is the "has a value"
# test. Never a fuzzy name match: two vendors' "Vanilla" are two materials.
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
    """Every (material, lab sample) link touching one of the given rows.

    Matched at read time rather than resolved at load time and stored: both
    loaders full-reload, so a stored link would need invalidating on every
    run to buy nothing at a few hundred rows.

    Both sides are passed in because the question is symmetric — a search
    that hits a warehouse material must still reveal the lab sample nobody
    searched for, and vice versa. Every match is returned; picking one would
    hide a second real material whose name happens to carry the same code.
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
    """One row of search_warehouse_and_lab, same keys whichever sides exist,
    so the caller never has to ask which shape it got."""
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
    """One search over both catalogs: "do we have this — in the warehouse, in
    the lab, or both?"

    The two catalogs answer separately everywhere else in the app, which only
    helps someone who already knows which of the two to try. A lab-only
    sample has no material row at all, so searching the warehouse for it
    finds nothing and says nothing about why.

    Each result names the side(s) it was found on: `Both` for a linked pair
    (with `match_rule` saying which rule linked it, so a surprising link is
    explainable rather than magic), `Warehouse` or `Lab` for a row standing
    alone. Standing alone is the normal case, not an error — most lab
    samples have never been adopted into inventory.

    Returns identity and labels only; the per-side detail comes from the
    existing get_material / get_stocked_locations / total_stock /
    get_lab_sample, rather than a second implementation of them here.

    `limit` applies to each side's search, so a term matching both catalogs
    can return more rows than `limit` — and deliberately so when one sample
    code sits inside several material names.
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
