"""Loads the R&D lab's flavor sample catalog from its own Google Sheet.

A second, separate source from the main inventory (see sources/sheets_source.py):
a different sheet, a different shape, and it feeds a flat `lab_samples` table
rather than materials/lots — each source row already *is* one sample, no
one-to-many relationship to model (see db/schema.sql for the full reasoning).

Only ever invoked via `python -m dtf_materials.lab_samples`, on a machine with
`config.local.toml`'s [lab_sheet] section set up. gspread/google-auth are
imported only inside fetch_rows(), never at module load time, so importing
this module (or dtf_materials.queries, which reads the table this writes)
never requires them — see requirements-sheets.txt.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import cleaning
from .config import ConfigError, LabSheetConfig, load_lab_sheet_config
from .db import DEFAULT_DB_PATH, init_db
from .sources.base import rows_from_values

EXPECTED_LAB_HEADERS = [
    "RD-ID", "Vendor", "Flavor Name", "Sample Code",
    "Flavor Declaration Type (Natural, N&A, Artificial, WONF)",
    "Location (Lab)", "Flavor Family", "Category/Subcategory",
    "Usage Level (recommended)", "Dry Aroma Descriptors",
    "Aroma Intensity 0-5", "Top Note", "Mid Palate Character", "Finish Note",
    "Off Note Tendency", "Matrix Performance", "Best Pairings", "Tested In:",
    "Allergens", "Date Received", "Price ($/kg)", "Part # (If applicable)",
]


class LabSheetHeaderError(Exception):
    """The lab sheet's header row is missing one or more EXPECTED_LAB_HEADERS."""


@dataclass
class LabLoadStats:
    staged_rows: int = 0
    # Rows skipped because they can't be given a stable identity. RD-ID is the
    # upsert key (see load_lab_samples), so a row without a usable one can't be
    # loaded without either inventing an id or letting a typo become a phantom
    # handle a formula could point at — neither is allowed (SPEC §2). All three
    # are flagged for a human, not fatal.
    rows_missing_rd_id: int = 0
    malformed_rd_ids: list[tuple[int, str]] = field(default_factory=list)  # (source_row, value)
    # rd_id -> source rows, for any RD-ID on more than one row. Stricter than a
    # duplicate sample code: RD-ID is the identity, so colliding rows are skipped
    # entirely rather than warned-but-loaded — a UNIQUE upsert would otherwise
    # silently overwrite one sample with another.
    duplicate_rd_ids: dict[str, list[int]] = field(default_factory=dict)
    # sample_code -> the source rows it appears on, for every code seen more
    # than once among loaded rows. Not fatal — flagged for a human to reconcile,
    # same posture as the main ETL's data-quality findings.
    duplicate_sample_codes: dict[str, list[int]] = field(default_factory=dict)


def _fetch_values(config: LabSheetConfig) -> list[list[str]]:
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


def fetch_rows(config: LabSheetConfig) -> list[dict[str, str]]:
    """Connect, validate the header, and return one dict per data row keyed
    by normalized header name. Raises LabSheetHeaderError if a column this
    loader relies on is missing from the live sheet.

    Split from _fetch_values so the header-check/row-shaping logic (shared
    with SheetsInventorySource, see sources.base.rows_from_values) can be
    unit-tested without a real Sheets connection."""
    return list(rows_from_values(_fetch_values(config), EXPECTED_LAB_HEADERS, LabSheetHeaderError))


def load_lab_samples(config: LabSheetConfig, db_path: Path | str = DEFAULT_DB_PATH) -> LabLoadStats:
    """Upsert the lab sheet into `lab_samples`, keyed on RD-ID.

    RD-ID is the sample's stable, human-maintained identity (see db/schema.sql).
    We upsert on it — ON CONFLICT(rd_id) DO UPDATE, the same pattern the main
    ETL uses on materials.dtf_part_num — rather than the full DELETE+reinsert
    this loader used to do, so lab_sample_id stays stable across runs and a
    Phase F formula line can reference a sample the next load won't repoint.
    COALESCE keeps a previously-known value rather than overwriting it with a
    now-blank cell, again mirroring the materials upsert.

    A consequence of dropping the DELETE: a row removed from the sheet now
    persists in the DB (same never-delete rationale as materials — a formula may
    reference it). Rows without a usable RD-ID are skipped and flagged, never
    invented (SPEC §2); the classification happens up front so a duplicate id
    can be excluded before any write instead of silently overwriting via the
    UNIQUE key."""
    rows = fetch_rows(config)
    stats = LabLoadStats()

    # Pass 1 — classify every row by its RD-ID before touching the DB.
    rd_id_rows: dict[str, list[int]] = defaultdict(list)
    loadable: list[tuple[int, str, dict[str, str]]] = []  # (source_row, rd_id, row)
    for source_row, row in enumerate(rows, start=2):  # row 1 is the header
        rd_id = cleaning.clean_text(row.get("RD-ID"))
        if rd_id is None:
            stats.rows_missing_rd_id += 1
            continue
        if not cleaning.is_valid_rd_id(rd_id):
            stats.malformed_rd_ids.append((source_row, rd_id))
            continue
        rd_id_rows[rd_id].append(source_row)
        loadable.append((source_row, rd_id, row))

    stats.duplicate_rd_ids = {r: rs for r, rs in rd_id_rows.items() if len(rs) > 1}

    # Pass 2 — write only the rows with a valid, unique RD-ID.
    by_code: dict[str, list[int]] = defaultdict(list)
    conn = init_db(db_path)
    try:
        conn.execute("BEGIN")
        for source_row, rd_id, row in loadable:
            if rd_id in stats.duplicate_rd_ids:
                continue  # colliding id — flagged above, never overwrite one sample with another
            code = cleaning.clean_text(row.get("Sample Code"))
            if code:
                by_code[code].append(source_row)

            conn.execute(
                """INSERT INTO lab_samples
                   (rd_id, source_row, vendor, flavor_name, sample_code, declaration_type,
                    location_lab, flavor_family, category_subcategory, usage_level,
                    dry_aroma_descriptors, aroma_intensity, top_note,
                    mid_palate_character, finish_note, off_note_tendency,
                    matrix_performance, best_pairings, tested_in, allergens,
                    date_received, price_per_kilo, dtf_part_num)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(rd_id) DO UPDATE SET
                       source_row           = excluded.source_row,
                       vendor               = COALESCE(excluded.vendor, lab_samples.vendor),
                       flavor_name          = COALESCE(excluded.flavor_name, lab_samples.flavor_name),
                       sample_code          = COALESCE(excluded.sample_code, lab_samples.sample_code),
                       declaration_type     = COALESCE(excluded.declaration_type, lab_samples.declaration_type),
                       location_lab         = COALESCE(excluded.location_lab, lab_samples.location_lab),
                       flavor_family        = COALESCE(excluded.flavor_family, lab_samples.flavor_family),
                       category_subcategory = COALESCE(excluded.category_subcategory, lab_samples.category_subcategory),
                       usage_level          = COALESCE(excluded.usage_level, lab_samples.usage_level),
                       dry_aroma_descriptors= COALESCE(excluded.dry_aroma_descriptors, lab_samples.dry_aroma_descriptors),
                       aroma_intensity      = COALESCE(excluded.aroma_intensity, lab_samples.aroma_intensity),
                       top_note             = COALESCE(excluded.top_note, lab_samples.top_note),
                       mid_palate_character = COALESCE(excluded.mid_palate_character, lab_samples.mid_palate_character),
                       finish_note          = COALESCE(excluded.finish_note, lab_samples.finish_note),
                       off_note_tendency    = COALESCE(excluded.off_note_tendency, lab_samples.off_note_tendency),
                       matrix_performance   = COALESCE(excluded.matrix_performance, lab_samples.matrix_performance),
                       best_pairings        = COALESCE(excluded.best_pairings, lab_samples.best_pairings),
                       tested_in            = COALESCE(excluded.tested_in, lab_samples.tested_in),
                       allergens            = COALESCE(excluded.allergens, lab_samples.allergens),
                       date_received        = COALESCE(excluded.date_received, lab_samples.date_received),
                       price_per_kilo       = COALESCE(excluded.price_per_kilo, lab_samples.price_per_kilo),
                       dtf_part_num         = COALESCE(excluded.dtf_part_num, lab_samples.dtf_part_num)""",
                (
                    rd_id,
                    source_row,
                    cleaning.clean_text(row.get("Vendor")),
                    cleaning.clean_text(row.get("Flavor Name")),
                    code,
                    cleaning.clean_text(
                        row.get("Flavor Declaration Type (Natural, N&A, Artificial, WONF)")
                    ),
                    cleaning.clean_text(row.get("Location (Lab)")),
                    cleaning.clean_text(row.get("Flavor Family")),
                    cleaning.clean_text(row.get("Category/Subcategory")),
                    cleaning.clean_text(row.get("Usage Level (recommended)")),
                    cleaning.clean_text(row.get("Dry Aroma Descriptors")),
                    cleaning.clean_text(row.get("Aroma Intensity 0-5")),
                    cleaning.clean_text(row.get("Top Note")),
                    cleaning.clean_text(row.get("Mid Palate Character")),
                    cleaning.clean_text(row.get("Finish Note")),
                    cleaning.clean_text(row.get("Off Note Tendency")),
                    cleaning.clean_text(row.get("Matrix Performance")),
                    cleaning.clean_text(row.get("Best Pairings")),
                    cleaning.clean_text(row.get("Tested In:")),
                    cleaning.clean_text(row.get("Allergens")),
                    cleaning.parse_date(row.get("Date Received")),
                    cleaning.parse_price(row.get("Price ($/kg)")),
                    cleaning.clean_text(row.get("Part # (If applicable)")),
                ),
            )
            stats.staged_rows += 1

        stats.duplicate_sample_codes = {c: rs for c, rs in by_code.items() if len(rs) > 1}
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return stats


def main() -> None:
    try:
        config = load_lab_sheet_config()
    except ConfigError as exc:
        raise SystemExit(f"Config error: {exc}")

    stats = load_lab_samples(config)

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


if __name__ == "__main__":
    main()
