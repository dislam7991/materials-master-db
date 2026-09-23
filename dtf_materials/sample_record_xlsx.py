"""Fill the company Sample Record Sheet template from a flavor's data.

The second Phase F renderer (F2c), built the same way as the Flavor Sheet one
(flavor_sheet_xlsx.py) and reusing the same flavor-profile data: the Record
Sheet is one formula per sample, so one flavor profile renders one sheet. Its
Label Claim column *is* the profile's mg-per-serving; Part Number and Price/kg
are resolved from the row each line references (queries.record_line_catalog),
never copied — a corrected price reaches the sheet the next time it's rendered.

Fill, never regenerate (SPEC Phase F): copy the template, write inputs into
known cells, keep every border, formula and page setting. Cell map in
docs/phase_f_templates_layout.md §1. What gets written, and only this:

- Header block: Brand, Product, Flavor, Sample code, Notes, Scoop Size,
  Servings/Unit, Quote ID, Jar/Lid, Servings(base).
- Per line: Part Number (A), Raw Material (B), Label Claim mg (C),
  Activity (D), Overage (E), Price/kg (I).

Everything else the template computes and this module must NOT touch: Actual
Input, g/run, Formula %, Cost/Unit, kg/run and the whole totals + cost panel
are the sheet's own formulas (layout §1 findings 1-3 are template defects the
owner fixes, not something a renderer papers over).

Three constraints the template imposes, from the layout findings:

- **Fixed capacity** (finding 5): 23 active lines, 12 excipient lines. Rows
  can't be inserted without breaking the SUM ranges, so a formula that doesn't
  fit is *refused* (RecordSheetOverflow) — the opposite of the Flavor Sheet,
  which grows. Better a clear error than a silently truncated recipe.
- **A blank Price/kg stays blank** (finding 4): the sheet treats an empty
  price as $0 and the total still looks complete, so a missing price must be
  left empty and flagged elsewhere, never written as 0.
- **The "blank" template isn't blank** (finding 6): it ships with an example
  formula, so every input cell in the two line sections and the header is
  cleared before writing.

openpyxl writes formulas without cached results, so the workbook is flagged to
recalculate on open; a preview that doesn't calculate shows the computed
columns blank until Excel opens it. Needs openpyxl; the ETL never imports it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook

from .db import PROJECT_ROOT

DEFAULT_TEMPLATE_PATH = (
    PROJECT_ROOT / "data" / "real" / "templates" / "Sample Record Sheet Blank Template.xlsx"
)

# Template geometry (Sheet1), from docs/phase_f_templates_layout.md §1.
ACTIVE_FIRST_ROW, ACTIVE_CAPACITY = 12, 23        # rows 12-34, subtotal row 35
EXCIPIENT_FIRST_ROW, EXCIPIENT_CAPACITY = 37, 12  # rows 37-48, subtotal row 49

# The input columns this renderer owns. F/G/H/J/K are the sheet's formulas and
# are left untouched; clearing an example only touches these.
_LINE_INPUT_COLUMNS = ("A", "B", "C", "D", "E", "I")

# Header input cell -> attribute on RecordSheet.
_HEADER_CELLS = {
    "B3": "brand",
    "B4": "product",
    "B5": "flavor",
    "B6": "sample_code",
    "B7": "notes",
    "E3": "scoop_size",
    "E4": "servings_per_unit",
    "E5": "quote_id",
    "E6": "jar_lid",
    "H9": "servings_base",
}


class RecordSheetOverflow(ValueError):
    """Raised when a formula has more active or excipient lines than the
    template's fixed sections hold. The template's SUM/banding ranges are wired
    to fixed rows, so growing the sheet would corrupt them (layout §1 finding
    5); refusing is the safe choice."""


@dataclass
class RecordLine:
    """One ingredient row as the sheet prints it. `is_active` picks the section
    (Actives vs. Flavor System and Excipients). Part Number and Price/kg come
    from the referenced catalog row and may be None (a lab-only sample has no
    Part #; a missing price is left blank, never 0 — finding 4)."""

    name: str
    label_claim_mg: float | None = None
    activity: float | None = None
    overage: float | None = None
    part_num: str | None = None
    price_per_kilo: float | None = None
    is_active: bool = True


@dataclass
class RecordSheet:
    """A rendered Record Sheet: the header inputs plus the ingredient lines.
    Header fields the flavor profile doesn't hold (scoop size, servings/unit,
    notes, jar/lid, servings base) are authored per sample and passed in."""

    brand: str | None = None
    product: str | None = None
    flavor: str | None = None
    sample_code: str | None = None
    notes: str | None = None
    scoop_size: str | None = None
    servings_per_unit: float | None = None
    quote_id: str | None = None
    jar_lid: str | None = None
    servings_base: float | None = None
    lines: list[RecordLine] = field(default_factory=list)


def render(record: RecordSheet, template_path: Path | str = DEFAULT_TEMPLATE_PATH) -> bytes:
    """Return the filled workbook as .xlsx bytes. Raises RecordSheetOverflow if
    a section has more lines than the template holds (checked before any cell
    is written, so a rejected render leaves nothing half-filled)."""
    actives = [l for l in record.lines if l.is_active]
    excipients = [l for l in record.lines if not l.is_active]
    if len(actives) > ACTIVE_CAPACITY:
        raise RecordSheetOverflow(
            f"{len(actives)} active lines exceed the template's {ACTIVE_CAPACITY} active rows"
        )
    if len(excipients) > EXCIPIENT_CAPACITY:
        raise RecordSheetOverflow(
            f"{len(excipients)} excipient lines exceed the template's "
            f"{EXCIPIENT_CAPACITY} excipient rows"
        )

    wb = load_workbook(template_path)
    ws = wb.worksheets[0]

    for cell, attr in _HEADER_CELLS.items():
        ws[cell] = getattr(record, attr)

    _fill_section(ws, ACTIVE_FIRST_ROW, ACTIVE_CAPACITY, actives)
    _fill_section(ws, EXCIPIENT_FIRST_ROW, EXCIPIENT_CAPACITY, excipients)

    wb.calculation.fullCalcOnLoad = True
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def _fill_section(ws, first_row: int, capacity: int, lines: list[RecordLine]) -> None:
    # Clear the template's example inputs across the whole section first, so a
    # shorter formula leaves no stale rows behind (finding 6).
    for r in range(first_row, first_row + capacity):
        for col in _LINE_INPUT_COLUMNS:
            ws[f"{col}{r}"] = None

    for offset, line in enumerate(lines):
        r = first_row + offset
        ws[f"A{r}"] = line.part_num
        ws[f"B{r}"] = line.name
        ws[f"C{r}"] = line.label_claim_mg
        ws[f"D{r}"] = line.activity
        ws[f"E{r}"] = line.overage
        # A blank price stays blank — writing 0 hides a missing cost (finding 4).
        if line.price_per_kilo is not None:
            ws[f"I{r}"] = line.price_per_kilo
