"""Fill the company Sample Labels template (.docx) from a flavor sheet.

Fill, never regenerate (docs/phase_f_templates_layout.md §3): the text lives
in a 5x3 table with exact row heights, labels in columns 1 and 3 read
left-right, top-down. Label 1's five paragraphs are copied into each label
used and only their run text is set, so fonts, alignment and the grid stay
the template's own; the outline shapes after the table are never touched.
Ten labels a document, one per flavor. Stdlib only (zipfile + ElementTree):
no docx library.
"""

from __future__ import annotations

import re
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from io import BytesIO
from pathlib import Path

from . import flavor_sheets as fs
from .db import PROJECT_ROOT

DEFAULT_TEMPLATE_PATH = PROJECT_ROOT / "data" / "real" / "templates" / "Sample Labels Blank Template.docx"

LABELS_PER_DOCUMENT = 10
LABEL_COLUMNS = (0, 2)  # the middle column is the gutter
DOCUMENT_PART = "word/document.xml"

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


@dataclass
class Label:
    """One printed label: five values, in the template's line order, no captions."""
    customer: str | None = None
    product: str | None = None
    flavor: str | None = None
    sample_id: str | None = None
    serving_size: str | None = None

    def lines(self) -> list[str]:
        """Return the five printed lines; a blank field prints empty, never "None"."""
        values = (self.customer, self.product, self.flavor, self.sample_id, self.serving_size)
        # Collapse tabs and newlines: a label line is one run of plain text.
        return [" ".join(str(v).split()) if v is not None else "" for v in values]


def serving_grams(base_mg: float | None, line_mgs: list[float | None]) -> Decimal | None:
    """Return BASE + flavor lines in grams, rounded half-up to 1 decimal, or None if any amount is blank.

    A blank amount would understate the serving, and a label is a promise.
    Half-up, not Python's round (0.05 rounds to even); Decimal(str()) avoids
    float error like 12.35 being stored as 12.3499….
    """
    amounts = [base_mg, *line_mgs]
    if any(a is None for a in amounts):
        return None
    total_mg = sum((Decimal(str(a)) for a in amounts), Decimal(0))
    return (total_mg / 1000).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def serving_size(scoops: float | None, grams: Decimal | None) -> str | None:
    """Return "<n> scoop serving (<g> g)", e.g. "2 scoop serving (12.3 g)", or None if either is unknown."""
    if scoops is None or grams is None:
        return None
    n = f"{scoops:g}" if scoops != int(scoops) else str(int(scoops))
    return f"{n} scoop serving ({grams} g)"


def labels_for_sheet(conn: sqlite3.Connection, flavor_sheet_id: int) -> list[Label]:
    """Return one Label per flavor profile, in the sheet's order, with the product's scoop count."""
    sheet = fs.get_sheet(conn, flavor_sheet_id)
    if sheet is None:
        return []
    scoops = fs.scoops_per_serving(conn, sheet["product"])
    return [
        Label(
            customer=sheet["customer"],
            product=sheet["product"],
            flavor=p["flavor_name"],
            sample_id=p["sample_id"],
            serving_size=serving_size(scoops, serving_grams(
                p["base_mg"],
                [l["mg_per_serving"] for l in fs.get_lines(conn, p["flavor_profile_id"])],
            )),
        )
        for p in fs.get_profiles(conn, flavor_sheet_id)
    ]


def render(labels: list[Label], template_path: Path | str = DEFAULT_TEMPLATE_PATH) -> list[bytes]:
    """Return one filled .docx per ten labels, in order; no labels, no documents."""
    with zipfile.ZipFile(template_path) as z:
        parts = [(info, z.read(info)) for info in z.infolist()]
    document_xml = next(data for info, data in parts if info.filename == DOCUMENT_PART)
    _register_namespaces(document_xml)

    documents = []
    for start in range(0, len(labels), LABELS_PER_DOCUMENT):
        filled = _fill(document_xml, labels[start:start + LABELS_PER_DOCUMENT])
        out = BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            for info, data in parts:
                z.writestr(info, filled if info.filename == DOCUMENT_PART else data)
        documents.append(out.getvalue())
    return documents


def _register_namespaces(xml: bytes) -> None:
    """Register the document's own prefixes, so ElementTree writes w:, wp:, mc: … and not ns0:."""
    for _, (prefix, uri) in ET.iterparse(BytesIO(xml), events=("start-ns",)):
        if prefix and not re.fullmatch(r"ns\d+", prefix):
            ET.register_namespace(prefix, uri)


def _fill(document_xml: bytes, labels: list[Label]) -> bytes:
    """Return document.xml with label i's cell holding a copy of label 1's paragraphs, text set."""
    root = ET.fromstring(document_xml)
    table = root.find(f"{W}body/{W}tbl")
    assert table is not None, "labels template has no table"
    cells = [
        row.findall(f"{W}tc")[col]
        for row in table.findall(f"{W}tr")
        for col in LABEL_COLUMNS
    ]
    prototype = [deepcopy(p) for p in cells[0].findall(f"{W}p")]

    for index, (cell, label) in enumerate(zip(cells, labels)):
        for p in cell.findall(f"{W}p"):
            cell.remove(p)
        for template_p, text in zip(prototype, label.lines()):
            p = deepcopy(template_p)
            if index:
                _drop_unique_markers(p)
            _set_text(p, text)
            cell.append(p)

    return _keep_root_declarations(document_xml, ET.tostring(root, encoding="UTF-8", xml_declaration=True))


def _drop_unique_markers(p: ET.Element) -> None:
    """Remove what may appear only once per document from a copied paragraph: bookmarks and Word's paragraph ids."""
    for parent in list(p.iter()):
        for child in list(parent):
            if child.tag in (f"{W}bookmarkStart", f"{W}bookmarkEnd"):
                parent.remove(child)
    p.attrib.pop(f"{W14}paraId", None)
    p.attrib.pop(f"{W14}textId", None)


def _set_text(p: ET.Element, text: str) -> None:
    """Make the paragraph's first run hold `text` alone, keeping its w:rPr; drop any other runs."""
    runs = p.findall(f"{W}r")
    if not runs:
        runs = [ET.SubElement(p, f"{W}r")]
    for extra in runs[1:]:
        p.remove(extra)
    run = runs[0]
    for child in list(run):
        if child.tag != f"{W}rPr":
            run.remove(child)
    t = ET.SubElement(run, f"{W}t")
    t.set(XML_SPACE, "preserve")
    t.text = text


def _keep_root_declarations(original: bytes, written: bytes) -> bytes:
    """Put the template's own root tag back, plus any declaration ElementTree hoisted to the root.

    ElementTree drops namespace declarations no element uses, but mc:Ignorable
    names prefixes (w14, wp14 …) by declaration alone; without them Word
    refuses the file.
    """
    root_tag = re.compile(rb"<w:document\b[^>]*>")
    original_tag = root_tag.search(original).group()
    written_tag = root_tag.search(written).group()
    declared = set(re.findall(rb"xmlns:([\w.-]+)=", original_tag))
    missing = [
        m.group() for m in re.finditer(rb'xmlns:([\w.-]+)="[^"]*"', written_tag)
        if m.group(1) not in declared
    ]
    merged = original_tag[:-1] + b"".join(b" " + d for d in missing) + b">"
    return written.replace(written_tag, merged, 1)
