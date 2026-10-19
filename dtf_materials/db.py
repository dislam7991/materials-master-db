"""Database connection and initialization helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = PROJECT_ROOT / "db" / "schema.sql"
DEFAULT_DB_PATH = PROJECT_ROOT / "db" / "materials.db"


def connect(db_path: Path | str = DEFAULT_DB_PATH, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open the database with foreign keys on and rows addressable by column name."""
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | str = DEFAULT_DB_PATH, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open the database and apply the schema (idempotent: every statement is IF NOT EXISTS)."""
    conn = connect(db_path, check_same_thread=check_same_thread)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _add_typed_record_lines(conn)
    conn.commit()
    return conn


def _add_typed_record_lines(conn: sqlite3.Connection) -> None:
    """Rebuild a record_lines table made before typed lines existed (SPEC F11).

    CREATE TABLE IF NOT EXISTS leaves an existing table alone, and SQLite
    can't change a CHECK in place, so an older table is renamed, the schema
    creates the new one, and the rows are copied across. Record sheets are
    authored data no loader can rebuild, so they are moved, never dropped.
    """
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(record_lines)")}
    if "typed_name" in columns:
        return
    old = ", ".join(sorted(columns))
    with conn:
        conn.execute("ALTER TABLE record_lines RENAME TO record_lines_before_typed")
        conn.execute("DROP INDEX IF EXISTS idx_record_lines_sheet")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    with conn:
        conn.execute(f"INSERT INTO record_lines ({old}) SELECT {old} FROM record_lines_before_typed")
        conn.execute("DROP TABLE record_lines_before_typed")


if __name__ == "__main__":
    conn = init_db()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )]
    print(f"Initialized {DEFAULT_DB_PATH}")
    print("Tables:", ", ".join(tables))
