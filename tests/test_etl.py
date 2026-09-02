"""Tests for the three load invariants the ETL promises.

Each of these is a bug that actually happened and was fixed in `dfd0190`;
they are here so the fixes cannot silently regress:

  1. `material_id` is stable. It is a real identity that `sample_materials`
     will reference, so re-sorting the source sheet must not repoint it.
  2. A run that dies partway leaves the database exactly as it was. The run
     *starts* by clearing lots and staging, so without the transaction a
     mid-run failure would leave the DB emptied and not repopulated.
  3. Two consecutive runs produce the same tables. Re-running the ETL is
     meant to be safe at any time; anything that grows or reorders on a
     second run is a defect.

Every test runs against a temp DB and the committed synthetic CSV, so
nothing here touches `db/materials.db` or needs real data.
"""

from __future__ import annotations

from typing import Iterator

import pytest

from dtf_materials import etl
from dtf_materials.db import PROJECT_ROOT, connect
from dtf_materials.sources import CsvInventorySource
from dtf_materials.sources.base import InventorySource, RawRow

SYNTHETIC_CSV = PROJECT_ROOT / "data" / "synthetic" / "raw_material_inventory.csv"

# Tables the ETL writes, in dependency order. Compared wholesale by the
# idempotency and rollback tests.
LOADED_TABLES = [
    "suppliers", "supplier_aliases", "materials", "lots", "lot_locations",
    "staging_inventory_raw",
]

# Columns that record *when* a row was written, not what it says. Two runs a
# second apart differ here by design, so they are excluded from comparisons.
TIMESTAMP_COLUMNS = {"ingested_at", "created_at"}


class ListSource(InventorySource):
    """An in-memory source, so a test can re-order the rows the CSV gave us."""

    def __init__(self, rows: list[RawRow]):
        self._rows = rows

    def rows(self) -> Iterator[RawRow]:
        yield from self._rows


class FailingSource(InventorySource):
    """A source that dies partway through yielding rows.

    Stands in for a malformed sheet or, once the source is the live Google
    Sheet, the network dropping mid-fetch: the failure lands after the run
    has already cleared the derived tables.
    """

    def __init__(self, rows: list[RawRow], fail_after: int):
        self._rows = rows
        self._fail_after = fail_after

    def rows(self) -> Iterator[RawRow]:
        for i, row in enumerate(self._rows):
            if i == self._fail_after:
                raise RuntimeError("source died mid-fetch")
            yield row


@pytest.fixture
def source_rows() -> list[RawRow]:
    return list(CsvInventorySource(SYNTHETIC_CSV).rows())


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test_materials.db"


def dump_table(db_path, table: str) -> list[tuple]:
    """Every row of `table`, minus ingestion timestamps, in a deterministic
    order (sorting on every column, since not all tables have a single-column
    key)."""
    conn = connect(db_path)
    try:
        cols = [
            r["name"] for r in conn.execute(f"PRAGMA table_info({table})")
            if r["name"] not in TIMESTAMP_COLUMNS
        ]
        select = ", ".join(cols)
        rows = conn.execute(f"SELECT {select} FROM {table} ORDER BY {select}").fetchall()
        return [tuple(r) for r in rows]
    finally:
        conn.close()


def dump_all(db_path) -> dict[str, list[tuple]]:
    return {table: dump_table(db_path, table) for table in LOADED_TABLES}


def material_ids_by_part(db_path) -> dict[str, int]:
    conn = connect(db_path)
    try:
        return {
            r["dtf_part_num"]: r["material_id"]
            for r in conn.execute("SELECT material_id, dtf_part_num FROM materials")
        }
    finally:
        conn.close()


def test_material_ids_are_stable_when_the_source_is_resorted(db_path, source_rows):
    """Somebody sorting the sheet by supplier is a normal Tuesday. It must
    not renumber materials, or sample history would point at the wrong ones."""
    etl.run(ListSource(source_rows), db_path)
    before = material_ids_by_part(db_path)
    assert before, "synthetic source should load at least one material"

    etl.run(ListSource(list(reversed(source_rows))), db_path)

    assert material_ids_by_part(db_path) == before


def test_a_failed_run_leaves_the_previous_load_intact(db_path, source_rows):
    """The run clears lots and staging before it repopulates them, so a
    crash in between is precisely the case where a non-atomic load would
    leave an empty database behind."""
    etl.run(ListSource(source_rows), db_path)
    before = dump_all(db_path)
    assert before["lots"], "first load should have produced lots to lose"

    with pytest.raises(RuntimeError):
        etl.run(FailingSource(source_rows, fail_after=len(source_rows) // 2), db_path)

    assert dump_all(db_path) == before


def test_two_consecutive_runs_produce_identical_tables(db_path, source_rows):
    """Idempotency: the second run re-derives the same rows rather than
    appending duplicate lots or creating a second copy of each material."""
    first_stats = etl.run(ListSource(source_rows), db_path)
    first = dump_all(db_path)

    second_stats = etl.run(ListSource(source_rows), db_path)

    assert dump_all(db_path) == first
    # The stats should say the same thing the tables do: nothing new the
    # second time, every material found again rather than re-created.
    assert second_stats.materials_created == 0
    assert second_stats.materials_updated == first_stats.materials_created
    assert second_stats.lots_created == first_stats.lots_created
