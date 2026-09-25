"""Helpers shared by more than one tab."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from dtf_materials.db import connect, init_db

# Shown when the lab catalog hasn't been loaded. Flush-left because Streamlit
# renders it as markdown, where an indented line becomes a code block.
NO_LAB_SAMPLES_MESSAGE = """
**No lab samples have been loaded into this database.**

The catalog lives in its own Google Sheet and is loaded by its own command,
separately from the warehouse inventory — so this tab can be empty while
everything above is full.

If a colleague gave you this database, ask them for an updated copy: the lab
samples travel inside the database file.

To load it yourself, add a `[lab_sheet]` section to `config.local.toml` (see
`config.example.toml`), then run:

```
python -m dtf_materials.lab_samples
```

There is no synthetic stand-in for this sheet, so a database built only from
the sample data will always show this message.
"""


@st.cache_resource
def _apply_schema(db_path: Path) -> None:
    """Create or update the schema once per app process, so an older database gains new tables."""
    init_db(db_path).close()


def get_conn(db_path: Path) -> sqlite3.Connection:
    """Open a connection for this page run; runs never share one.

    Streamlit reruns overlap on different threads, and one sqlite3 connection
    used by two threads at once hands back wrong or missing rows. The flag
    only allows this run's fragment reruns and callbacks to use it.
    """
    _apply_schema(db_path)
    return connect(db_path, check_same_thread=False)


def money(value) -> str:
    """Format a price as $1,234.56, or a dash when there is none."""
    return "—" if value is None else f"${value:,.2f}"


def rows_to_df(rows, columns: dict[str, str]) -> pd.DataFrame:
    """Turn sqlite rows into a DataFrame with only `columns`, renamed to their labels."""
    data = [{label: r[key] for key, label in columns.items()} for r in rows]
    return pd.DataFrame(data, columns=list(columns.values()))
