"""Source adapters for the inventory pipeline: each yields rows shaped like the sheet's columns."""

from .base import InventorySource, RawRow
from .csv_source import CsvInventorySource

__all__ = ["InventorySource", "RawRow", "CsvInventorySource"]
