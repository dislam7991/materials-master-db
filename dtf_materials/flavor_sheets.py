"""The Flavor Sheet object: a header plus flavor profiles, each a BASE amount
and material lines in mg per serving.

Holds exactly what the company Flavor Sheet asks for. Grams per sample are
derived (mg x servings / 1000), never stored. A line references its material
(material_id / rd_id) so name and price resolve at read time, or carries a
typed_name for a material neither catalog has yet. Functions commit their own
writes: a sheet is saved work.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
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


def profile_title(profile) -> str:
    """Return a flavor's display title: its name and Sample ID, or a placeholder."""
    return " ".join(x for x in (profile["flavor_name"], profile["sample_id"]) if x) or "(unnamed flavor)"


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


# --- labels (F4) -----------------------------------------------------------

DEFAULT_SCOOPS = 1.0  # most products are one scoop a serving


def scoops_per_serving(conn: sqlite3.Connection, product: str | None) -> float:
    """Return the product's remembered scoops per serving, or DEFAULT_SCOOPS if none is stored."""
    row = conn.execute(
        "SELECT scoops_per_serving FROM product_scoops WHERE product = ?",
        ((product or "").strip(),),
    ).fetchone()
    return row["scoops_per_serving"] if row else DEFAULT_SCOOPS


def set_scoops_per_serving(conn: sqlite3.Connection, product: str | None, scoops: float) -> None:
    """Remember a product's scoops per serving; a blank product has nothing to remember it by."""
    product = (product or "").strip()
    if not product:
        return
    conn.execute(
        "INSERT INTO product_scoops (product, scoops_per_serving) VALUES (?, ?) "
        "ON CONFLICT (product) DO UPDATE SET scoops_per_serving = excluded.scoops_per_serving",
        (product, scoops),
    )
    conn.commit()


# --- snapshots at download (F2h) -------------------------------------------

def _sheet_numbers(conn: sqlite3.Connection, flavor_sheet_id: int) -> dict:
    """Return everything a download prints, resolved as of now: header, scoops, profiles and lines with prices."""
    sheet = get_sheet(conn, flavor_sheet_id)
    return {
        "header": {k: sheet[k] for k in HEADER_FIELDS},
        "scoops_per_serving": scoops_per_serving(conn, sheet["product"]),
        "profiles": [
            {
                "flavor_name": p["flavor_name"],
                "sample_id": p["sample_id"],
                "base_mg": p["base_mg"],
                "lines": [
                    {k: l[k] for k in ("name", "source", "mg_per_serving", "price_per_kilo")}
                    for l in get_lines(conn, p["flavor_profile_id"])
                ],
            }
            for p in get_profiles(conn, flavor_sheet_id)
        ],
    }


def take_snapshot(conn: sqlite3.Connection, flavor_sheet_id: int, kind: str) -> int | None:
    """Store what a download of `kind` is rendered from, timestamped, and return its id (None if no such sheet).

    Called on every download, so a reprint after a reprice is a second
    snapshot beside the first, never an overwrite of it. `kind` is
    'flavor_sheet' or 'labels'; the schema rejects anything else.
    """
    if get_sheet(conn, flavor_sheet_id) is None:
        return None
    cur = conn.execute(
        "INSERT INTO flavor_sheet_snapshots (flavor_sheet_id, kind, data) VALUES (?,?,?)",
        (flavor_sheet_id, kind, json.dumps(_sheet_numbers(conn, flavor_sheet_id))),
    )
    conn.commit()
    return cur.lastrowid


def list_snapshots(conn: sqlite3.Connection, flavor_sheet_id: int) -> list[sqlite3.Row]:
    """Return a sheet's snapshots (id, kind, taken_at), oldest first."""
    return conn.execute(
        "SELECT snapshot_id, kind, taken_at FROM flavor_sheet_snapshots "
        "WHERE flavor_sheet_id = ? ORDER BY snapshot_id",
        (flavor_sheet_id,),
    ).fetchall()


def get_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> dict | None:
    """Return one snapshot's numbers as stored, or None if it doesn't exist."""
    row = conn.execute(
        "SELECT data FROM flavor_sheet_snapshots WHERE snapshot_id = ?", (snapshot_id,)
    ).fetchone()
    return json.loads(row["data"]) if row else None


# --- folders and search (F6a) ----------------------------------------------
#
# Customer → Product · Quote ID → sheets, derived from the header fields every
# sheet already stores. No folder tables: a folder exists while a sheet is in
# it, and renaming one rewrites that field on its sheets.

FOLDER_FIELDS = ("customer", "product", "quote_id")
BLANK_LABELS = {"customer": "(no customer)", "product": "(no product)", "quote_id": "(no quote ID)"}


def folder_key(value: str | None) -> str:
    """Return the folder a header value files under: trimmed, case-folded, inner spaces collapsed ("" when blank)."""
    return " ".join((value or "").split()).casefold()


def _clean(value: str | None) -> str | None:
    """Return a header value trimmed with inner spaces collapsed, or None when blank."""
    return " ".join((value or "").split()) or None


def sheet_folder(sheet) -> tuple[str, tuple[str, str]]:
    """Return the (customer key, (product key, quote key)) a sheet files under."""
    return folder_key(sheet["customer"]), (folder_key(sheet["product"]), folder_key(sheet["quote_id"]))


def _sort_key(key: str) -> tuple[bool, str]:
    """Sort folders alphabetically with the blank "(no …)" folder last."""
    return (key == "", key)


def _sheets_with_profiles(conn: sqlite3.Connection) -> list[dict]:
    """Return every sheet newest first as {"sheet": row, "profiles": [rows in print order]}."""
    by_sheet: dict[int, list] = defaultdict(list)
    for p in conn.execute("SELECT * FROM flavor_profiles ORDER BY flavor_sheet_id, position, flavor_profile_id"):
        by_sheet[p["flavor_sheet_id"]].append(p)
    return [{"sheet": s, "profiles": by_sheet[s["flavor_sheet_id"]]} for s in list_sheets(conn)]


def folders(conn: sqlite3.Connection) -> list[dict]:
    """Return the folder tree: customers A–Z, each with its Product · Quote ID folders A–Z, each with its sheets newest first.

    A customer is {"key", "customer", "quotes"}; a quote folder is {"key":
    (product key, quote key), "product", "quote_id", "sheets"}, where sheets
    are _sheets_with_profiles entries. A folder is named by its most-used
    spelling, the newest on a tie, so one typo doesn't rename it (None when
    blank).
    """
    tree: dict[str, dict] = {}
    for entry in _sheets_with_profiles(conn):
        sheet = entry["sheet"]
        ckey, qkey = sheet_folder(sheet)
        customer = tree.setdefault(ckey, {"key": ckey, "spellings": Counter(), "quotes": {}})
        customer["spellings"][_clean(sheet["customer"])] += 1
        quote = customer["quotes"].setdefault(qkey, {"key": qkey, "spellings": Counter(), "sheets": []})
        quote["spellings"][(_clean(sheet["product"]), _clean(sheet["quote_id"]))] += 1
        quote["sheets"].append(entry)

    def named(quote: dict) -> dict:
        """Return a quote folder with its display product and quote ID."""
        product, quote_id = _most_used(quote["spellings"])
        return {"key": quote["key"], "product": product, "quote_id": quote_id, "sheets": quote["sheets"]}

    return [
        {
            "key": c["key"],
            "customer": _most_used(c["spellings"]),
            "quotes": [named(c["quotes"][k]) for k in sorted(c["quotes"], key=lambda k: (_sort_key(k[0]), _sort_key(k[1])))],
        }
        for c in (tree[k] for k in sorted(tree, key=_sort_key))
    ]


def _most_used(spellings: Counter):
    """Return the most-used spelling; sheets are counted newest first, and max keeps the first of a tie."""
    return max(spellings, key=spellings.__getitem__)


def search_sheets(conn: sqlite3.Connection, term: str) -> list[dict]:
    """Return sheets (newest first, as _sheets_with_profiles entries) matching `term` anywhere a person would look.

    Case-insensitive substring of customer, product, quote ID, or any of the
    sheet's flavor names or Sample IDs. A blank term matches nothing.
    """
    needle = term.strip().casefold()
    if not needle:
        return []

    def hit(value) -> bool:
        """Return True if `value` contains the search term."""
        return needle in (value or "").casefold()

    return [
        e for e in _sheets_with_profiles(conn)
        if any(hit(e["sheet"][f]) for f in FOLDER_FIELDS)
        or any(hit(p["flavor_name"]) or hit(p["sample_id"]) for p in e["profiles"])
    ]


def rename_customer(conn: sqlite3.Connection, customer_key: str, new_name: str | None) -> int:
    """Set the customer on every sheet in one customer folder and return how many changed.

    Renaming onto another folder's name merges the two: that is the fix for a
    typo split, done by a person on purpose.
    """
    ids = [e["sheet"]["flavor_sheet_id"] for e in _sheets_with_profiles(conn)
           if folder_key(e["sheet"]["customer"]) == customer_key]
    return _set_on(conn, ids, customer=_clean(new_name))


def rename_quote_folder(
    conn: sqlite3.Connection,
    customer_key: str,
    quote_folder_key: tuple[str, str],
    product: str | None,
    quote_id: str | None,
) -> int:
    """Set product and quote ID on every sheet in one Product · Quote ID folder and return how many changed."""
    ids = [e["sheet"]["flavor_sheet_id"] for e in _sheets_with_profiles(conn)
           if sheet_folder(e["sheet"]) == (customer_key, quote_folder_key)]
    return _set_on(conn, ids, product=_clean(product), quote_id=_clean(quote_id))


def _set_on(conn: sqlite3.Connection, sheet_ids: list[int], **fields) -> int:
    """Write the given header fields to each sheet in one transaction and return the count."""
    with conn:
        for sheet_id in sheet_ids:
            conn.execute(
                f"UPDATE flavor_sheets SET {', '.join(f'{k} = ?' for k in fields)} WHERE flavor_sheet_id = ?",
                [*fields.values(), sheet_id],
            )
    return len(sheet_ids)
