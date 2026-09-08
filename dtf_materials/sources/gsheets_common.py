"""Shared plumbing for opening a Google Sheet tab via a service account.

Used by both Sheets-backed readers (the main inventory and the lab sample
catalog) so the connection and error-handling logic exists exactly once.
Only ever imported by modules that already require gspread — never by the
default CSV path, so it costs nothing when Sheets access isn't in use.
"""

from __future__ import annotations

from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


class SheetAccessError(Exception):
    """Could not open the configured sheet or tab — bad sheet_id, wrong tab
    name, or the sheet isn't shared with the service account."""


def open_worksheet(sheet_id: str, tab_name: str, service_account_key_path: Path):
    """Authenticate and return the gspread Worksheet for one tab, or raise
    SheetAccessError with a message naming the likely fix."""
    creds = Credentials.from_service_account_file(
        str(service_account_key_path), scopes=_SCOPES
    )
    client = gspread.authorize(creds)
    try:
        spreadsheet = client.open_by_key(sheet_id)
        return spreadsheet.worksheet(tab_name)
    except gspread.exceptions.SpreadsheetNotFound as exc:
        raise SheetAccessError(
            f"No spreadsheet found for sheet_id {sheet_id!r}. Check it "
            f"against the sheet's URL."
        ) from exc
    except gspread.exceptions.WorksheetNotFound as exc:
        raise SheetAccessError(
            f"Spreadsheet has no tab named {tab_name!r}. Check the exact "
            f"tab name at the bottom of the sheet."
        ) from exc
    except gspread.exceptions.APIError as exc:
        raise SheetAccessError(
            f"Google API error opening the sheet: {exc}. If this is a "
            f"permission error, make sure the sheet is shared — as at "
            f"least Viewer — with the service account's email (the "
            f"'client_email' field in {service_account_key_path})."
        ) from exc
