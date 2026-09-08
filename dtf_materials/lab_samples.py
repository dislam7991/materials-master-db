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
    "Vendor", "Flavor Name", "Sample Code",
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
    # sample_code -> the source rows it appears on, for every code seen more
    # than once. Not fatal (unlike a missing header) — flagged for a human
    # to reconcile, same posture as the main ETL's data-quality findings.
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
    """Full-reload load into `lab_samples`. Same tradeoff as the main ETL —
    simpler and idempotent at this volume — and safe here for an even
    simpler reason: nothing else references a lab_sample_id, so there's no
    stable-id requirement forcing an upsert like materials.material_id has."""
    rows = fetch_rows(config)
    stats = LabLoadStats()
    by_code: dict[str, list[int]] = defaultdict(list)

    conn = init_db(db_path)
    try:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM lab_samples")

        for source_row, row in enumerate(rows, start=2):  # row 1 is the header
            code = cleaning.clean_text(row.get("Sample Code"))
            if code:
                by_code[code].append(source_row)

            conn.execute(
                """INSERT INTO lab_samples
                   (source_row, vendor, flavor_name, sample_code, declaration_type,
                    location_lab, flavor_family, category_subcategory, usage_level,
                    dry_aroma_descriptors, aroma_intensity, top_note,
                    mid_palate_character, finish_note, off_note_tendency,
                    matrix_performance, best_pairings, tested_in, allergens,
                    date_received, price_per_kilo, dtf_part_num)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
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
