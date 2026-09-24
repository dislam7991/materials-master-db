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
    conn.commit()
    return conn


if __name__ == "__main__":
    conn = init_db()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )]
    print(f"Initialized {DEFAULT_DB_PATH}")
    print("Tables:", ", ".join(tables))
