"""Google Sheets inventory source — reads the real company sheet.

Only used when the ETL is run with `--source sheets` on a machine that has
`config.local.toml` set up (see dtf_materials.config). Nothing in the default
CSV path imports this module, so gspread/google-auth are never required just
to run the synthetic pipeline or the test suite — see requirements-sheets.txt.

Read-only end to end: the service account backing this is granted Viewer
access on the sheet (never Editor), and nothing below calls a write endpoint,
so there is no code path here that could modify the source sheet.
"""

from __future__ import annotations

from typing import Iterator

from ..config import SheetsConfig
from .base import EXPECTED_HEADERS, InventorySource, RawRow, rows_from_values
from .gsheets_common import SheetAccessError, open_worksheet

__all__ = ["SheetAccessError", "SheetHeaderError", "SheetsInventorySource"]


class SheetHeaderError(Exception):
    """The sheet's header row is missing one or more EXPECTED_HEADERS."""


class SheetsInventorySource(InventorySource):
    def __init__(self, config: SheetsConfig):
        self.config = config

    def rows(self) -> Iterator[RawRow]:
        yield from self._rows_from_values(self._fetch_values())

    def _fetch_values(self) -> list[list[str]]:
        worksheet = open_worksheet(
            self.config.sheet_id, self.config.tab_name, self.config.service_account_key_path
        )
        return worksheet.get_all_values()

    @staticmethod
    def _rows_from_values(values: list[list[str]]) -> Iterator[RawRow]:
        """Split out from `rows()` so the header check and row-shaping logic
        (shared with the lab-sample loader, see sources.base.rows_from_values)
        can be unit-tested without a real Sheets connection."""
        return rows_from_values(values, EXPECTED_HEADERS, SheetHeaderError)
