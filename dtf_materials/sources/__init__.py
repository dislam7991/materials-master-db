"""Source adapters for the inventory pipeline.

An InventorySource just has to yield rows shaped like the sheet's columns.
Swapping the source (CSV today, Google Sheets later, something else after
that) never touches the cleaning/loading code in etl.py — everything
downstream works off the same RawRow shape.
"""

from .base import InventorySource, RawRow
from .csv_source import CsvInventorySource

__all__ = ["InventorySource", "RawRow", "CsvInventorySource"]
