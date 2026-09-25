"""Loads the R&D lab's flavor sample catalog from its own Google Sheet.

A separate source from the inventory: each sheet row is one sample, upserted
into the flat `lab_samples` table keyed on RD-ID (see db/schema.sql). Rows
without a usable, unique RD-ID are skipped and reported, never given an
invented id.

gspread/google-auth are imported only when the sheet is fetched, so importing
this module never requires them.

Run with:  python -m dtf_materials.lab_samples
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import cleaning
from .config import ConfigError, SheetConfig, load_lab_sheet_config
from .db import DEFAULT_DB_PATH, init_db
from .sources.base import rows_from_values

# lab_samples column -> (sheet header, parser). Drives the header check, the
# INSERT and the upsert, so the three can't fall out of step.
LAB_COLUMNS: dict[str, tuple[str, Callable[[str | None], object]]] = {
    "vendor": ("Vendor", cleaning.clean_text),
    "flavor_name": ("Flavor Name", cleaning.clean_text),
    "sample_code": ("Sample Code", cleaning.clean_text),
    "declaration_type": ("Flavor Declaration Type (Natural, N&A, Artificial, WONF)", cleaning.clean_text),
    "location_lab": ("Location (Lab)", cleaning.clean_text),
    "flavor_family": ("Flavor Family", cleaning.clean_text),
    "category_subcategory": ("Category/Subcategory", cleaning.clean_text),
    "usage_level": ("Usage Level (recommended)", cleaning.clean_text),
    "dry_aroma_descriptors": ("Dry Aroma Descriptors", cleaning.clean_text),
    "aroma_intensity": ("Aroma Intensity 0-5", cleaning.clean_text),
    "top_note": ("Top Note", cleaning.clean_text),
    "mid_palate_character": ("Mid Palate Character", cleaning.clean_text),
    "finish_note": ("Finish Note", cleaning.clean_text),
    "off_note_tendency": ("Off Note Tendency", cleaning.clean_text),
    "matrix_performance": ("Matrix Performance", cleaning.clean_text),
    "best_pairings": ("Best Pairings", cleaning.clean_text),
    "tested_in": ("Tested In:", cleaning.clean_text),
    "allergens": ("Allergens", cleaning.clean_text),
    "date_received": ("Date Received", cleaning.parse_date),
    "price_per_kilo": ("Price ($/kg)", cleaning.parse_price),
    "dtf_part_num": ("Part # (If applicable)", cleaning.clean_text),
}

EXPECTED_LAB_HEADERS = ["RD-ID", *(header for header, _ in LAB_COLUMNS.values())]

# Upsert on RD-ID. COALESCE keeps a previously known value rather than
# overwriting it with a now-blank cell, mirroring the materials upsert.
_UPSERT_SQL = (
    f"INSERT INTO lab_samples (rd_id, source_row, {', '.join(LAB_COLUMNS)}) "
    f"VALUES ({', '.join('?' * (len(LAB_COLUMNS) + 2))}) "
    "ON CONFLICT(rd_id) DO UPDATE SET source_row = excluded.source_row, "
    + ", ".join(f"{c} = COALESCE(excluded.{c}, lab_samples.{c})" for c in LAB_COLUMNS)
)


class LabSheetHeaderError(Exception):
    """The lab sheet's header row is missing one or more EXPECTED_LAB_HEADERS."""


@dataclass
class LabLoadStats:
    """Counts and flagged rows reported at the end of a load."""
    staged_rows: int = 0
    # Skipped: no RD-ID, a malformed one, or one shared by several rows. RD-ID
    # is the identity, so none of these can be loaded without inventing one.
    rows_missing_rd_id: int = 0
    malformed_rd_ids: list[tuple[int, str]] = field(default_factory=list)  # (source_row, value)
    duplicate_rd_ids: dict[str, list[int]] = field(default_factory=dict)   # rd_id -> source rows
    # Loaded but flagged for a human: a sample code on more than one row.
    duplicate_sample_codes: dict[str, list[int]] = field(default_factory=dict)


def _fetch_values(config: SheetConfig) -> list[list[str]]:
    """Return the lab sheet tab's raw cell values, header row included."""
    try:
        from .sources.gsheets_common import open_worksheet
    except ModuleNotFoundError as exc:
        raise SystemExit(
            f"{exc}. Loading the lab sheet needs the packages in "
            f"requirements-sheets.txt: pip install -r requirements-sheets.txt"
        )

    worksheet = open_worksheet(
        config.sheet_id, config.tab_name, config.service_account_key_path
    )
    return worksheet.get_all_values()


def fetch_rows(config: SheetConfig) -> list[dict[str, str]]:
    """Fetch the lab sheet and return one dict per data row, keyed by header (raises LabSheetHeaderError)."""
    return list(rows_from_values(_fetch_values(config), EXPECTED_LAB_HEADERS, LabSheetHeaderError))


def load_lab_samples(config: SheetConfig, db_path: Path | str = DEFAULT_DB_PATH) -> LabLoadStats:
    """Upsert the lab sheet into `lab_samples` keyed on RD-ID, in one transaction.

    Rows are classified before any write, so a duplicated RD-ID is skipped
    rather than silently overwriting one sample with another. Samples removed
    from the sheet stay in the database, since a flavor line may reference them.
    """
    loadable, stats = _classify_rows(fetch_rows(config))
    conn = init_db(db_path)
    try:
        conn.execute("BEGIN")
        _upsert_samples(conn, loadable, stats)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return stats


def _classify_rows(rows: list[dict[str, str]]) -> tuple[list[tuple[int, str, dict]], LabLoadStats]:
    """Split rows into loadable (source_row, rd_id, row) triples and stats for the rest."""
    stats = LabLoadStats()
    rd_id_rows: dict[str, list[int]] = defaultdict(list)
    candidates: list[tuple[int, str, dict[str, str]]] = []
    for source_row, row in enumerate(rows, start=2):  # row 1 is the header
        rd_id = cleaning.clean_text(row.get("RD-ID"))
        if rd_id is None:
            stats.rows_missing_rd_id += 1
        elif not cleaning.is_valid_rd_id(rd_id):
            stats.malformed_rd_ids.append((source_row, rd_id))
        else:
            rd_id_rows[rd_id].append(source_row)
            candidates.append((source_row, rd_id, row))

    stats.duplicate_rd_ids = {r: rs for r, rs in rd_id_rows.items() if len(rs) > 1}
    loadable = [c for c in candidates if c[1] not in stats.duplicate_rd_ids]
    return loadable, stats


def _upsert_samples(conn, loadable: list[tuple[int, str, dict]], stats: LabLoadStats) -> None:
    """Write each loadable row and record sample codes that appear more than once."""
    by_code: dict[str, list[int]] = defaultdict(list)
    for source_row, rd_id, row in loadable:
        values = [parse(row.get(header)) for header, parse in LAB_COLUMNS.values()]
        conn.execute(_UPSERT_SQL, (rd_id, source_row, *values))
        stats.staged_rows += 1
        code = cleaning.clean_text(row.get("Sample Code"))
        if code:
            by_code[code].append(source_row)
    stats.duplicate_sample_codes = {c: rs for c, rs in by_code.items() if len(rs) > 1}


def _print_stats(stats: LabLoadStats, config: SheetConfig) -> None:
    """Print the load summary and every flagged row."""
    print(
        f"Staged {stats.staged_rows} rows from lab sheet {config.sheet_id} "
        f"(tab {config.tab_name!r})"
    )
    if stats.rows_missing_rd_id:
        print(
            f"  WARNING: {stats.rows_missing_rd_id} row(s) have no RD-ID and were "
            f"skipped — a sample needs one to be loaded and referenced. Fill the "
            f"RD-ID column in the lab sheet."
        )
    if stats.malformed_rd_ids:
        print(
            f"  WARNING: {len(stats.malformed_rd_ids)} row(s) have a malformed "
            f"RD-ID (expected RD-0000..RD-9999) and were skipped:"
        )
        for source_row, value in sorted(stats.malformed_rd_ids):
            print(f"    row {source_row}: {value!r}")
    if stats.duplicate_rd_ids:
        print(
            f"  WARNING: {len(stats.duplicate_rd_ids)} RD-ID(s) appear on more "
            f"than one row — every colliding row was skipped, since the RD-ID is "
            f"the sample's identity and must be unique:"
        )
        for rd_id, source_rows in sorted(stats.duplicate_rd_ids.items()):
            print(f"    {rd_id}: rows {source_rows}")
    if stats.duplicate_sample_codes:
        print(
            f"  WARNING: {len(stats.duplicate_sample_codes)} sample code(s) "
            f"appear on more than one row — likely a data-entry duplicate or "
            f"a genuine collision between two different samples:"
        )
        for code, source_rows in sorted(stats.duplicate_sample_codes.items()):
            print(f"    {code}: rows {source_rows}")


def main() -> None:
    """Command-line entry point: load the configured lab sheet and print a summary."""
    try:
        config = load_lab_sheet_config()
    except ConfigError as exc:
        raise SystemExit(f"Config error: {exc}")
    _print_stats(load_lab_samples(config), config)


if __name__ == "__main__":
    main()
