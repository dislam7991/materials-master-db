"""The interface every inventory source implements.

RawRow is intentionally dumb: a dict of header -> raw string, exactly as the
source presented it (untyped, uncleaned, possibly missing keys). All
interpretation happens in dtf_materials.cleaning and dtf_materials.etl, so a
new source only ever has to answer "what are the rows", nothing more.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Iterator

RawRow = dict[str, str]

_WS_AROUND_SLASH = re.compile(r"\s*/\s*")
_WS_BETWEEN_PARENS = re.compile(r"\)\s+\(")


def normalize_header_name(name: str) -> str:
    """Return a header with cosmetic whitespace removed ("Lot / Batch" -> "Lot/Batch"); words are never changed."""
    name = name.strip()
    name = _WS_AROUND_SLASH.sub("/", name)
    name = _WS_BETWEEN_PARENS.sub(")(", name)
    return name


def rows_from_values(
    values: list[list[str]], expected_headers: list[str], header_error: type[Exception]
) -> Iterator[RawRow]:
    """Yield one {header: cell} dict per data row of a Sheets tab, after checking its header row.

    Raises `header_error` if an expected header is missing or appears twice: a
    duplicate would silently pick whichever column comes last. Extra and
    unnamed columns are fine.
    """
    if not values:
        return
    raw_header, *data_rows = values
    header = [normalize_header_name(h) for h in raw_header]
    missing = [h for h in expected_headers if h not in header]
    if missing:
        raise header_error(
            f"Sheet's header row is missing expected column(s): {missing}. "
            f"Has the sheet's structure changed since the expected headers "
            f"were written?"
        )
    duplicated = sorted({h for h in expected_headers if header.count(h) > 1})
    if duplicated:
        raise header_error(
            f"Sheet's header row names the same column more than once: "
            f"{duplicated}. Only the rightmost of each would be read and the "
            f"others silently dropped, so which values load would depend on "
            f"column order. Rename or remove the duplicate column."
        )
    for row in data_rows:
        yield dict(zip(header, row))


# The main inventory sheet's own header names, in order. Every CSV/Sheets
# source for it must yield rows keyed by these exact strings.
EXPECTED_HEADERS = [
    "Receiving Date", "Locations", "DTF Lot #", "DTF Part #", "Status",
    "Allergen", "Material Name", "Supplier/MFG", "Lot/Batch", "EXP. Date",
    "Start Day Stock", "Current Stock", "Filter Moving/Date",
    "Check/Cycle count", "Category (1 Raw)(2 Flavor)", "Price Per Kilo",
    "Total cost", "Ready To Archive",
]


class InventorySource(ABC):
    """A source of raw inventory rows; it knows nothing about cleaning or the schema."""

    @abstractmethod
    def rows(self) -> Iterator[RawRow]:
        """Yield one RawRow per source row, in source order."""
        raise NotImplementedError
