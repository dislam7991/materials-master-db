"""Google Sheets inventory source — reads the real company sheet.

Used only by `etl --source sheets`; the default CSV path never imports it, so
gspread is optional. Read-only: the service account has Viewer access and
nothing here calls a write endpoint.
"""

from __future__ import annotations

from typing import Iterator

from ..config import SheetConfig
from .base import EXPECTED_HEADERS, InventorySource, RawRow, rows_from_values
from .gsheets_common import SheetAccessError, open_worksheet

__all__ = ["SheetAccessError", "SheetHeaderError", "SheetsInventorySource"]


class SheetHeaderError(Exception):
    """The sheet's header row is missing one or more EXPECTED_HEADERS."""


class SheetsInventorySource(InventorySource):
    """Reads inventory rows from the configured Google Sheet tab."""

    def __init__(self, config: SheetConfig):
        """Remember the sheet config; nothing is fetched until rows() is called."""
        self.config = config

    def rows(self) -> Iterator[RawRow]:
        """Fetch the tab and yield one dict per data row, keyed by header."""
        yield from self._rows_from_values(self._fetch_values())

    def _fetch_values(self) -> list[list[str]]:
        """Return the tab's raw cell values, header row included."""
        worksheet = open_worksheet(
            self.config.sheet_id, self.config.tab_name, self.config.service_account_key_path
        )
        return worksheet.get_all_values()

    @staticmethod
    def _rows_from_values(values: list[list[str]]) -> Iterator[RawRow]:
        """Check the header and shape rows from raw values (split out so tests need no Sheets connection)."""
        return rows_from_values(values, EXPECTED_HEADERS, SheetHeaderError)
