"""CSV inventory source — the synthetic-data adapter, and the fallback if
the Google Sheets API is ever a pain (e.g. a manual CSV export)."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterator

from .base import InventorySource, RawRow


class CsvInventorySource(InventorySource):
    """Reads inventory rows from a CSV file with the sheet's header row."""

    def __init__(self, path: Path | str):
        """Remember the CSV path; nothing is read until rows() is called."""
        self.path = Path(path)

    def rows(self) -> Iterator[RawRow]:
        """Yield one dict per CSV row, keyed by header."""
        with self.path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            yield from reader
