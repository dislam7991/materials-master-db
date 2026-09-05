"""ETL pipeline: source rows -> staging_inventory_raw -> typed tables.

This is a full-reload pipeline: each run clears staging + the derived tables
and rebuilds them from the source. That's the right tradeoff for a low-volume
sheet (hundreds of rows, run on demand or nightly) and it sidesteps a whole
class of "did this lot already get loaded" bugs. An incremental version
(load only rows newer than the last run, keyed on Receiving Date + DTF Lot #)
is a natural follow-up and a good scaling conversation for an interview:
the tradeoff is idempotency/simplicity now vs. write volume and history
preservation later, once the sheet is big enough for a full reload to be slow.

Two passes over staging:
  1. Group rows by DTF Part # -> upsert one canonical `materials` row per
     part #. First row wins for name/category/supplier/allergen; later rows
     with the same part # but different material_name are logged as
     conflicts (data quality issue) but still get their lot attached to the
     existing material, because DTF Part # is the identity that matters.
  2. Every row that resolved to a material gets one `lots` row, plus parsed
     `lot_locations` rows. Rows with no DTF Part # can't be tied to a
     material at all and are skipped from lots/materials — that's a real
     gap in the source (a receiving with no assigned Part #), not something
     the ETL should paper over by guessing.
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import cleaning
from .db import DEFAULT_DB_PATH, init_db
from .sources import CsvInventorySource, InventorySource
from .sources.base import EXPECTED_HEADERS


@dataclass
class LoadStats:
    staged_rows: int = 0
    materials_created: int = 0
    materials_updated: int = 0
    materials_conflicted: int = 0
    stale_materials: int = 0
    lots_created: int = 0
    rows_missing_part_num: int = 0
    suppliers_created: int = 0
    header_mismatches: list[str] = field(default_factory=list)


def reset_derived_tables(conn: sqlite3.Connection) -> None:
    """Clear the per-run tables only.

    Deliberately does NOT delete `materials` or `suppliers`. Those are
    upserted instead, because `material_id` is a stable identity that other
    tables (`sample_materials`) reference: deleting and re-inserting would
    reassign every id on each run, so merely re-sorting the source sheet
    would silently repoint sample history at the wrong materials. Lots are
    fully rebuilt from the source each run, which is safe because nothing
    references `lot_id` outside `lot_locations`.

    A consequence worth knowing: a material that disappears from the sheet
    stays in the database rather than vanishing. That is the intended
    behavior for a master table — it keeps history — and `stale_materials`
    in the load stats reports how many were not seen this run.
    """
    for table in ["lot_locations", "lots", "staging_inventory_raw"]:
        conn.execute(f"DELETE FROM {table}")


def stage(conn: sqlite3.Connection, source: InventorySource, stats: LoadStats) -> None:
    rows = list(source.rows())
    if rows:
        missing_cols = [h for h in EXPECTED_HEADERS if h not in rows[0]]
        if missing_cols:
            stats.header_mismatches = missing_cols

    for i, row in enumerate(rows, start=1):
        conn.execute(
            """INSERT INTO staging_inventory_raw
               (source_row, receiving_date, locations, dtf_lot_num, dtf_part_num,
                status, allergen, material_name, supplier_mfg, lot_batch, exp_date,
                start_day_stock, current_stock, filter_moving, check_cycle_count,
                category, price_per_kilo, total_cost, ready_to_archive)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                i,
                row.get("Receiving Date"), row.get("Locations"), row.get("DTF Lot #"),
                row.get("DTF Part #"), row.get("Status"), row.get("Allergen"),
                row.get("Material Name"), row.get("Supplier/MFG"), row.get("Lot/Batch"),
                row.get("EXP. Date"), row.get("Start Day Stock"), row.get("Current Stock"),
                row.get("Filter Moving/Date"), row.get("Check/Cycle count"),
                row.get("Category (1 Raw)(2 Flavor)"), row.get("Price Per Kilo"),
                row.get("Total cost"), row.get("Ready To Archive"),
            ),
        )
        stats.staged_rows += 1


def resolve_supplier(conn: sqlite3.Connection, raw_name: str | None, stats: LoadStats) -> int | None:
    """Look up (or create) the canonical supplier for a raw spelling, via the
    case/whitespace-insensitive alias key. Returns supplier_id, or None if
    the cell was blank."""
    name = cleaning.clean_text(raw_name)
    if name is None:
        return None
    key = cleaning.normalize_supplier_key(name)

    row = conn.execute(
        "SELECT s.supplier_id FROM supplier_aliases a "
        "JOIN suppliers s ON s.supplier_id = a.supplier_id WHERE a.alias = ?",
        (key,),
    ).fetchone()
    if row:
        return row["supplier_id"]

    cur = conn.execute("INSERT INTO suppliers (canonical_name) VALUES (?)", (name,))
    supplier_id = cur.lastrowid
    conn.execute("INSERT INTO supplier_aliases (alias, supplier_id) VALUES (?, ?)", (key, supplier_id))
    stats.suppliers_created += 1
    return supplier_id


def load_materials_and_lots(conn: sqlite3.Connection, stats: LoadStats) -> None:
    staging_rows = conn.execute(
        "SELECT * FROM staging_inventory_raw ORDER BY source_row"
    ).fetchall()

    material_id_by_part: dict[str, int] = {}

    for row in staging_rows:
        part_num = cleaning.clean_text(row["dtf_part_num"])
        if part_num is None:
            stats.rows_missing_part_num += 1
            continue

        name = cleaning.clean_text(row["material_name"])
        category = cleaning.parse_category(row["category"])
        allergen = cleaning.clean_text(row["allergen"])
        supplier_id = resolve_supplier(conn, row["supplier_mfg"], stats)

        if part_num not in material_id_by_part:
            # Upsert on the business key so material_id stays stable across runs.
            # First row seen for a part # in THIS run supplies the canonical
            # attributes; COALESCE keeps a previously-known value rather than
            # overwriting it with a blank cell.
            existing_row = conn.execute(
                "SELECT material_id FROM materials WHERE dtf_part_num = ?", (part_num,)
            ).fetchone()
            conn.execute(
                """INSERT INTO materials
                       (dtf_part_num, material_name, supplier_id, category, allergen)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(dtf_part_num) DO UPDATE SET
                       material_name = COALESCE(excluded.material_name, materials.material_name),
                       supplier_id   = COALESCE(excluded.supplier_id,   materials.supplier_id),
                       category      = COALESCE(excluded.category,      materials.category),
                       allergen      = COALESCE(excluded.allergen,      materials.allergen)""",
                (part_num, name, supplier_id, category, allergen),
            )
            row_id = conn.execute(
                "SELECT material_id FROM materials WHERE dtf_part_num = ?", (part_num,)
            ).fetchone()["material_id"]
            material_id_by_part[part_num] = row_id
            if existing_row is None:
                stats.materials_created += 1
            else:
                stats.materials_updated += 1
        else:
            existing = conn.execute(
                "SELECT material_name FROM materials WHERE material_id = ?",
                (material_id_by_part[part_num],),
            ).fetchone()
            if existing and name and cleaning.normalize_key(existing["material_name"]) != cleaning.normalize_key(name):
                stats.materials_conflicted += 1

        material_id = material_id_by_part[part_num]
        price = cleaning.parse_price(row["price_per_kilo"])
        cur = conn.execute(
            """INSERT INTO lots
               (material_id, dtf_lot_num, supplier_lot_num, receiving_date, exp_date,
                status, start_day_stock, current_stock, price_per_kilo, total_cost,
                locations_raw, ready_to_archive)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                material_id,
                cleaning.clean_text(row["dtf_lot_num"]),
                cleaning.clean_text(row["lot_batch"]),
                cleaning.parse_date(row["receiving_date"]),
                cleaning.parse_date(row["exp_date"]),
                cleaning.clean_text(row["status"]),
                cleaning.parse_float(row["start_day_stock"]),
                cleaning.parse_float(row["current_stock"]),
                price,
                cleaning.parse_price(row["total_cost"]),
                cleaning.clean_text(row["locations"]),
                cleaning.parse_bool_flag(row["ready_to_archive"]),
            ),
        )
        stats.lots_created += 1
        lot_id = cur.lastrowid

        for loc in cleaning.split_locations(row["locations"]):
            conn.execute(
                "INSERT OR IGNORE INTO lot_locations (lot_id, location) VALUES (?, ?)",
                (lot_id, loc),
            )


def refresh_current_prices(conn: sqlite3.Connection) -> None:
    """Set materials.current_price_per_kilo from each material's most recent
    lot (by receiving_date, falling back to lot_id for undated lots)."""
    conn.execute(
        """
        UPDATE materials
        SET current_price_per_kilo = (
            SELECT l.price_per_kilo FROM lots l
            WHERE l.material_id = materials.material_id
              AND l.price_per_kilo IS NOT NULL
            ORDER BY
                CASE WHEN l.receiving_date IS NULL THEN 1 ELSE 0 END,
                l.receiving_date DESC,
                l.lot_id DESC
            LIMIT 1
        )
        """
    )


def run(source: InventorySource, db_path: Path | str = DEFAULT_DB_PATH) -> LoadStats:
    """Load the source into the database as a single atomic transaction.

    Atomicity matters more than it looks: the run begins by clearing lots and
    staging, so a failure partway through (a malformed row, a dropped network
    connection mid-fetch once the source is the live Google Sheet) would
    otherwise leave the database emptied and not repopulated. Everything
    between reset and refresh commits together or not at all.
    """
    conn = init_db(db_path)          # schema DDL commits on its own
    stats = LoadStats()
    try:
        conn.execute("BEGIN")
        reset_derived_tables(conn)
        stage(conn, source, stats)
        load_materials_and_lots(conn, stats)
        refresh_current_prices(conn)
        stats.stale_materials = conn.execute(
            """SELECT COUNT(*) FROM materials m
               WHERE NOT EXISTS (SELECT 1 FROM lots l WHERE l.material_id = m.material_id)
                 AND m.is_sample_only = 0"""
        ).fetchone()[0]
        conn.commit()
    except Exception:
        conn.rollback()              # database is left exactly as it was
        raise
    finally:
        conn.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Load the inventory sheet into the materials master DB.")
    parser.add_argument("csv_path", nargs="?", default="data/synthetic/raw_material_inventory.csv")
    parser.add_argument("--source", choices=["csv", "sheets"], default="csv",
                         help="Where to read inventory rows from (default: csv).")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = parser.parse_args()

    if args.source == "sheets":
        # Deferred imports: gspread/google-auth are only needed for this
        # branch (see requirements-sheets.txt), so the default CSV path
        # never has to import them.
        try:
            from .sources.sheets_source import SheetsInventorySource
        except ModuleNotFoundError as exc:
            raise SystemExit(
                f"{exc}. --source sheets needs the packages in "
                f"requirements-sheets.txt: pip install -r requirements-sheets.txt"
            )
        from .config import ConfigError, load_sheets_config

        try:
            config = load_sheets_config()
        except ConfigError as exc:
            raise SystemExit(f"Config error: {exc}")
        source = SheetsInventorySource(config)
        source_label = f"Google Sheet {config.sheet_id} (tab {config.tab_name!r})"
    else:
        source = CsvInventorySource(args.csv_path)
        source_label = args.csv_path

    stats = run(source, args.db)

    print(f"Staged {stats.staged_rows} rows from {source_label}")
    if stats.header_mismatches:
        print(f"  WARNING: source is missing expected columns: {stats.header_mismatches}")
    print(f"Materials created: {stats.materials_created}, updated: {stats.materials_updated}"
          f"  (suppliers created: {stats.suppliers_created})")
    print(f"Lots created: {stats.lots_created}")
    print(f"Rows skipped (missing DTF Part #): {stats.rows_missing_part_num}")
    print(f"Materials with conflicting name on a repeated Part #: {stats.materials_conflicted}")
    if stats.stale_materials:
        print(f"Materials in DB no longer present in the sheet: {stats.stale_materials}")


if __name__ == "__main__":
    main()
