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
    """Collapse cosmetic whitespace a human typing a header cell adds
    without meaning anything different: "Lot / Batch" and "Lot/Batch" are
    the same column, so are "Category (1 Raw) (2 Flavor)" and "Category (1
    Raw)(2 Flavor)", or a plain trailing space. Confirmed against a real
    company sheet, whose header row had all three variants. Only touches
    whitespace around punctuation — never alters the words themselves, so
    it can't make two genuinely different columns collide.

    Shared by every Sheets-backed source (the main inventory and the lab
    sample catalog), pure stdlib so it costs the CSV-only default path
    nothing to have available."""
    name = name.strip()
    name = _WS_AROUND_SLASH.sub("/", name)
    name = _WS_BETWEEN_PARENS.sub(")(", name)
    return name


def rows_from_values(
    values: list[list[str]], expected_headers: list[str], header_error: type[Exception]
) -> Iterator[RawRow]:
    """Normalize a Sheets tab's header row and yield one dict per data row.

    Header cells are whitespace-normalized (see normalize_header_name)
    before anything else, so the dict keys built here — and therefore every
    downstream `row.get("...")` — line up with `expected_headers` regardless
    of which cosmetic spacing variant the real sheet happens to use.

    Raises `header_error(...)` if any name in `expected_headers` is missing
    after normalization — order and extra columns are fine, since rows are
    matched by header name, not position. A row shorter than the header
    (gspread already pads these, but this contract shouldn't depend on
    that) just yields fewer keys; callers already treat a missing key the
    same as a blank cell.

    Shared by every Sheets-backed source so this logic is written and
    tested exactly once, each caller only supplies its own header list and
    its own exception type."""
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
    """Yields raw inventory rows. Implementations know nothing about SQLite,
    cleaning rules, or the schema — they only know how to fetch rows."""

    @abstractmethod
    def rows(self) -> Iterator[RawRow]:
        """Yield one RawRow per source row, in source order."""
        raise NotImplementedError
