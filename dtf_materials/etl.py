"""ETL pipeline: source rows -> staging_inventory_raw -> typed tables.

Full reload, in one atomic transaction: each run clears staging and lots and
rebuilds them from the source. Materials and suppliers are upserted instead,
so their ids stay stable across runs.

Rows are grouped by DTF Part #. The first row for a part number supplies the
material's name/category/supplier/allergen; a later row with a different name
counts as a conflict for the quality report but still gets its lot attached.
Rows with no DTF Part # are skipped, not guessed at.

Run with:  python -m dtf_materials.etl [csv_path] [--source sheets] [--db PATH]
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

# Source header -> staging_inventory_raw column. Staging keeps every cell as raw text.
STAGING_COLUMNS = {
    "Receiving Date": "receiving_date",
    "Locations": "locations",
    "DTF Lot #": "dtf_lot_num",
    "DTF Part #": "dtf_part_num",
    "Status": "status",
    "Allergen": "allergen",
    "Material Name": "material_name",
    "Supplier/MFG": "supplier_mfg",
    "Lot/Batch": "lot_batch",
    "EXP. Date": "exp_date",
    "Start Day Stock": "start_day_stock",
    "Current Stock": "current_stock",
    "Filter Moving/Date": "filter_moving",
    "Check/Cycle count": "check_cycle_count",
    "Category (1 Raw)(2 Flavor)": "category",
    "Price Per Kilo": "price_per_kilo",
    "Total cost": "total_cost",
    "Ready To Archive": "ready_to_archive",
}


@dataclass
class LoadStats:
    """Counts reported at the end of a run."""
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
    """Clear the tables rebuilt every run: lot_locations, lots and staging.

    Materials and suppliers are deliberately kept: material_id is referenced
    from outside, so a material missing from the sheet stays (counted as stale).
    """
    for table in ["lot_locations", "lots", "staging_inventory_raw"]:
        conn.execute(f"DELETE FROM {table}")


def stage(conn: sqlite3.Connection, source: InventorySource, stats: LoadStats) -> None:
    """Copy every source row verbatim into staging_inventory_raw, numbered from 1."""
    rows = list(source.rows())
    if rows:
        stats.header_mismatches = [h for h in EXPECTED_HEADERS if h not in rows[0]]

    columns = ", ".join(["source_row", *STAGING_COLUMNS.values()])
    placeholders = ", ".join("?" * (len(STAGING_COLUMNS) + 1))
    sql = f"INSERT INTO staging_inventory_raw ({columns}) VALUES ({placeholders})"
    for i, row in enumerate(rows, start=1):
        conn.execute(sql, (i, *(row.get(header) for header in STAGING_COLUMNS)))
        stats.staged_rows += 1


def resolve_supplier(conn: sqlite3.Connection, raw_name: str | None, stats: LoadStats) -> int | None:
    """Return the supplier_id for a raw spelling, creating the supplier if it's new (None if blank)."""
    name = cleaning.clean_text(raw_name)
    if name is None:
        return None
    key = cleaning.normalize_key(name)

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
    """Turn each staged row into a lot under its material, upserting the material on first sight."""
    staging_rows = conn.execute(
        "SELECT * FROM staging_inventory_raw ORDER BY source_row"
    ).fetchall()

    material_id_by_part: dict[str, int] = {}
    for row in staging_rows:
        part_num = cleaning.clean_text(row["dtf_part_num"])
        if part_num is None:
            stats.rows_missing_part_num += 1
            continue

        supplier_id = resolve_supplier(conn, row["supplier_mfg"], stats)
        if part_num not in material_id_by_part:
            material_id_by_part[part_num] = _upsert_material(conn, part_num, row, supplier_id, stats)
        elif _name_conflicts(conn, material_id_by_part[part_num], row["material_name"]):
            stats.materials_conflicted += 1

        _insert_lot(conn, material_id_by_part[part_num], row)
        stats.lots_created += 1


def _upsert_material(
    conn: sqlite3.Connection, part_num: str, row: sqlite3.Row, supplier_id: int | None, stats: LoadStats
) -> int:
    """Insert or update the material for a part number and return its stable material_id.

    COALESCE keeps a previously known value rather than overwriting it with a blank cell.
    """
    existed = conn.execute(
        "SELECT 1 FROM materials WHERE dtf_part_num = ?", (part_num,)
    ).fetchone() is not None
    conn.execute(
        """INSERT INTO materials
               (dtf_part_num, material_name, supplier_id, category, allergen)
           VALUES (?,?,?,?,?)
           ON CONFLICT(dtf_part_num) DO UPDATE SET
               material_name = COALESCE(excluded.material_name, materials.material_name),
               supplier_id   = COALESCE(excluded.supplier_id,   materials.supplier_id),
               category      = COALESCE(excluded.category,      materials.category),
               allergen      = COALESCE(excluded.allergen,      materials.allergen)""",
        (
            part_num,
            cleaning.clean_text(row["material_name"]),
            supplier_id,
            cleaning.parse_category(row["category"]),
            cleaning.clean_text(row["allergen"]),
        ),
    )
    if existed:
        stats.materials_updated += 1
    else:
        stats.materials_created += 1
    return conn.execute(
        "SELECT material_id FROM materials WHERE dtf_part_num = ?", (part_num,)
    ).fetchone()["material_id"]


def _name_conflicts(conn: sqlite3.Connection, material_id: int, raw_name: str | None) -> bool:
    """True if a row's material name genuinely differs from the material already loaded."""
    name = cleaning.clean_text(raw_name)
    existing = conn.execute(
        "SELECT material_name FROM materials WHERE material_id = ?", (material_id,)
    ).fetchone()
    return bool(
        existing and name
        and cleaning.normalize_key(existing["material_name"]) != cleaning.normalize_key(name)
    )


def _insert_lot(conn: sqlite3.Connection, material_id: int, row: sqlite3.Row) -> None:
    """Insert one lot from a staged row, plus one lot_locations row per parsed location."""
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
            cleaning.parse_price(row["price_per_kilo"]),
            cleaning.parse_price(row["total_cost"]),
            cleaning.clean_text(row["locations"]),
            cleaning.parse_bool_flag(row["ready_to_archive"]),
        ),
    )
    for loc in cleaning.split_locations(row["locations"]):
        conn.execute(
            "INSERT OR IGNORE INTO lot_locations (lot_id, location) VALUES (?, ?)",
            (cur.lastrowid, loc),
        )


def refresh_current_prices(conn: sqlite3.Connection) -> None:
    """Set each material's current price from its most recent priced lot (undated lots count as oldest)."""
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


def _count_stale_materials(conn: sqlite3.Connection) -> int:
    """Count stocked (non-sample) materials that no longer have any lot in the sheet."""
    return conn.execute(
        """SELECT COUNT(*) FROM materials m
           WHERE NOT EXISTS (SELECT 1 FROM lots l WHERE l.material_id = m.material_id)
             AND m.is_sample_only = 0"""
    ).fetchone()[0]


def run(source: InventorySource, db_path: Path | str = DEFAULT_DB_PATH) -> LoadStats:
    """Load the source into the database as one atomic transaction and return the stats.

    Atomic because the run starts by clearing lots: a failure partway through
    must leave the previous data in place, not an emptied database.
    """
    conn = init_db(db_path)          # schema DDL commits on its own
    stats = LoadStats()
    try:
        conn.execute("BEGIN")
        reset_derived_tables(conn)
        stage(conn, source, stats)
        load_materials_and_lots(conn, stats)
        refresh_current_prices(conn)
        stats.stale_materials = _count_stale_materials(conn)
        conn.commit()
    except Exception:
        conn.rollback()              # database is left exactly as it was
        raise
    finally:
        conn.close()
    return stats


def _build_source(args: argparse.Namespace) -> tuple[InventorySource, str]:
    """Return the source chosen on the command line and a label for printing."""
    if args.source == "csv":
        return CsvInventorySource(args.csv_path), args.csv_path

    # Deferred imports: gspread/google-auth are only needed for --source sheets.
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
    return SheetsInventorySource(config), f"Google Sheet {config.sheet_id} (tab {config.tab_name!r})"


def _print_stats(stats: LoadStats, source_label: str) -> None:
    """Print the run summary."""
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


def main() -> None:
    """Command-line entry point: load the chosen source and print a summary."""
    parser = argparse.ArgumentParser(description="Load the inventory sheet into the materials master DB.")
    parser.add_argument("csv_path", nargs="?", default="data/synthetic/raw_material_inventory.csv")
    parser.add_argument("--source", choices=["csv", "sheets"], default="csv",
                        help="Where to read inventory rows from (default: csv).")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = parser.parse_args()

    source, source_label = _build_source(args)
    stats = run(source, args.db)
    _print_stats(stats, source_label)


if __name__ == "__main__":
    main()
