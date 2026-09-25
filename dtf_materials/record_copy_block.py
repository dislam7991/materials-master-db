"""A flavor profile's lines as a tab-separated block to paste into a
manager-built Sample Record Sheet (SPEC F2i).

Why a copy block and not a renderer: the manager builds the record sheet in
Excel from the OneDrive template, pastes the actives from the PL Cost Sheet and
types the excipients. The flavor lines are the part worth saving typing on. The
app never opens or writes that file — openpyxl alters a workbook it saves
(layout §4) and can't grow the section safely (finding 5), while Excel does
both correctly when the user inserts rows. So the app hands over text and the
user pastes it into column A of the first empty row below the last excipient.

One row per profile line, columns A:I of the sheet (layout §1, "A filled
manager sheet"):

  A Part #   B name   C label claim mg   D 1   E 0   F = C   G -   H -   I Price/kg

- BASE is not in the block: it is the actives already on the sheet, and it is
  stored on the profile (base_mg), never as a line.
- D/E default to Activity 1 / Overage 0 — every flavor line on a real sheet is
  that. F is C as a value (Actual Input = C·(1+0)/1). G and H stay empty; the
  user fills them down from the excipient row. J/K are outside the block, so
  existing rows keep calculating.
- Part # and Price/kg resolve from the referenced catalog row
  (queries.record_line_catalog), never from the profile, so a reprice shows up
  the next time the block is built. A missing price is an empty field — the
  sheet counts an empty price as $0 but a written 0 hides it (finding 4) — and
  the line is listed in `unpriced` so the UI can flag it.

Pure text; no openpyxl. Nothing here raises on odd data: a blank value becomes
an empty field, never "None".
"""

from __future__ import annotations

import re
import sqlite3

from . import flavor_sheets as fs
from . import queries

# Tabs and line breaks inside a value would shift every column after it (or
# start a new row) when Excel splits the paste, so they are flattened to a space.
_BREAKS = re.compile(r"[\t\r\n]+")

# All digits with a leading zero: Excel parses "00123" as the number 123 on
# paste and the zero is gone. Written as ="00123" Excel keeps it as text.
_LEADING_ZERO_DIGITS = re.compile(r"^0\d*$")


def build_block(conn: sqlite3.Connection, flavor_profile_id: int) -> dict:
    """The copy block for one flavor profile. Returns {'rows': list of 9-field
    lists (A..I), 'text': the rows tab-separated, one per line, no header,
    'unpriced': names of lines whose Price/kg is empty}. len(rows) is how many
    rows the user needs free in the section before pasting."""
    rows, unpriced = [], []
    for line in fs.get_lines(conn, flavor_profile_id):
        cat = queries.record_line_catalog(conn, material_id=line["material_id"], rd_id=line["rd_id"])
        mg = line["mg_per_serving"]
        rows.append([
            _part_field(cat["part_num"]),
            _text_field(line["name"]),
            _number_field(mg),
            "1",
            "0",
            _number_field(mg),
            "",
            "",
            _number_field(cat["price_per_kilo"]),
        ])
        if cat["price_per_kilo"] is None:
            unpriced.append(line["name"] or "")
    return {
        "rows": rows,
        "text": "\n".join("\t".join(r) for r in rows),
        "unpriced": unpriced,
    }


def _text_field(value) -> str:
    """A text value safe for one cell: None -> '', breaks flattened to a space."""
    if value is None:
        return ""
    return _BREAKS.sub(" ", str(value)).strip()


def _part_field(value) -> str:
    """Part # as a field Excel won't reformat: a digits-only code with a leading
    zero is wrapped as ="…" so the zero survives; anything else is plain text."""
    text = _text_field(value)
    return f'="{text}"' if _LEADING_ZERO_DIGITS.match(text) else text


def _number_field(value: float | None) -> str:
    """A number as Excel reads it back exactly: whole numbers without '.0',
    others at full precision; None -> '' (blank, never 0)."""
    if value is None:
        return ""
    value = float(value)
    return str(int(value)) if value.is_integer() else repr(value)
