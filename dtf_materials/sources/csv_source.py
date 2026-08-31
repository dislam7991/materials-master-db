"""CSV inventory source — the synthetic-data adapter, and the fallback if
the Google Sheets API is ever a pain (e.g. a manual CSV export)."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterator

from .base import InventorySource, RawRow


class CsvInventorySource(InventorySource):
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def rows(self) -> Iterator[RawRow]:
        with self.path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            yield from reader
