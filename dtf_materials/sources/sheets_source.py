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

import gspread
from google.oauth2.service_account import Credentials

from ..config import SheetsConfig
from .base import EXPECTED_HEADERS, InventorySource, RawRow

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


class SheetAccessError(Exception):
    """Could not open the configured sheet or tab — bad sheet_id, wrong tab
    name, or the sheet isn't shared with the service account."""


class SheetHeaderError(Exception):
    """The sheet's header row is missing one or more EXPECTED_HEADERS."""


class SheetsInventorySource(InventorySource):
    def __init__(self, config: SheetsConfig):
        self.config = config

    def rows(self) -> Iterator[RawRow]:
        yield from self._rows_from_values(self._fetch_values())

    def _fetch_values(self) -> list[list[str]]:
        creds = Credentials.from_service_account_file(
            str(self.config.service_account_key_path), scopes=_SCOPES
        )
        client = gspread.authorize(creds)
        try:
            spreadsheet = client.open_by_key(self.config.sheet_id)
            worksheet = spreadsheet.worksheet(self.config.tab_name)
        except gspread.exceptions.SpreadsheetNotFound as exc:
            raise SheetAccessError(
                f"No spreadsheet found for sheet_id {self.config.sheet_id!r}. "
                f"Check it against the sheet's URL."
            ) from exc
        except gspread.exceptions.WorksheetNotFound as exc:
            raise SheetAccessError(
                f"Spreadsheet has no tab named {self.config.tab_name!r}. "
                f"Check the exact tab name at the bottom of the sheet."
            ) from exc
        except gspread.exceptions.APIError as exc:
            raise SheetAccessError(
                f"Google API error opening the sheet: {exc}. If this is a "
                f"permission error, make sure the sheet is shared — as at "
                f"least Viewer — with the service account's email (the "
                f"'client_email' field in {self.config.service_account_key_path})."
            ) from exc
        return worksheet.get_all_values()

    @staticmethod
    def _rows_from_values(values: list[list[str]]) -> Iterator[RawRow]:
        """Split out from `rows()` so the header check and row-shaping logic
        can be unit-tested without a real Sheets connection.

        Checks that every EXPECTED_HEADERS name is present — order and extra
        columns are fine, since rows are matched by header name, not
        position — and raises immediately rather than silently loading a
        sheet whose structure has drifted. A row shorter than the header
        (gspread already pads these, but the source's contract shouldn't
        depend on that) just yields fewer keys; downstream code already
        treats a missing key the same as a blank cell.
        """
        if not values:
            return
        header, *data_rows = values
        missing = [h for h in EXPECTED_HEADERS if h not in header]
        if missing:
            raise SheetHeaderError(
                f"Sheet's header row is missing expected column(s): {missing}. "
                f"Has the sheet's structure changed since EXPECTED_HEADERS "
                f"was written?"
            )
        for row in data_rows:
            yield dict(zip(header, row))
