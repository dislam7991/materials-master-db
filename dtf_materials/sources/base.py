"""The interface every inventory source implements.

RawRow is intentionally dumb: a dict of header -> raw string, exactly as the
source presented it (untyped, uncleaned, possibly missing keys). All
interpretation happens in dtf_materials.cleaning and dtf_materials.etl, so a
new source only ever has to answer "what are the rows", nothing more.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

RawRow = dict[str, str]

# The sheet's own header names, in order. Every source must yield rows keyed
# by these exact strings (CSV headers already match; a Sheets adapter reads
# the header row from the sheet and can assert it matches this list).
EXPECTED_HEADERS = [
    "Receiving Date", "Locations", "DTF Lot #", "DTF Part #", "Status",
    "Allergen", "Material Name", "Supplier/MFG", "Lot/Batch", "EXP. Date",
    "Start Day Stock", "Current Stock", "Filter Moving/Date",
    "Check/Cycle count", "Category (1 Raw)(2 Flavor)", "Price Per Kilo",
    "Total cost", "Ready To Archive",
]


class InventorySource(ABC):
    """Yields raw inventory rows. Implementations know nothing about SQLite,
    cleaning rules, or the schema — they only know how to fetch rows."""

    @abstractmethod
    def rows(self) -> Iterator[RawRow]:
        """Yield one RawRow per source row, in source order."""
        raise NotImplementedError
