"""Tests for the Sample Labels renderer (labels_docx.py) and the per-product
scoop count behind its serving line (flavor_sheets.py).

Pinned here:

  1. One label per flavor profile, in the sheet's order, values only — the
     template's captions never print; ten a document, so 11 flavors make two.
  2. Fill, never regenerate: copied paragraphs keep label 1's formatting, the
     exact row heights and the outline shapes come through untouched, and the
     root keeps the namespace declarations mc:Ignorable depends on.
  3. Serving size is BASE + lines in grams, half-up to 1 decimal; scoops
     default to 1 and a changed count is remembered per product.

The real template is company property and never committed, so the renderer
fills a synthetic .docx of the same shape, built below from the layout map in
docs/phase_f_templates_layout.md §3.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from decimal import Decimal
from io import BytesIO

import pytest

from dtf_materials import etl
from dtf_materials import flavor_sheets as fs
from dtf_materials.db import PROJECT_ROOT, init_db
from dtf_materials.labels_docx import (
    Label, labels_for_sheet, render, serving_grams, serving_size,
)
from dtf_materials.sources import CsvInventorySource

SYNTHETIC_CSV = PROJECT_ROOT / "data" / "synthetic" / "raw_material_inventory.csv"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
CAPTIONS = ["CUSTOMER", "PRODUCT", "FLAVOR", "SAMPLE ID", "SERVING SIZE"]

NAMESPACES = (
    'xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'mc:Ignorable="w14 wp14"'
)


@pytest.fixture
def conn(tmp_path):
    """A connection to a fresh, empty database."""
    c = init_db(tmp_path / "test.db")
    yield c
    c.close()


def _caption_paragraph(text: str, bookmark: bool = False) -> str:
    """One centred, bold 14 pt caption paragraph like label 1's."""
    mark = '<w:bookmarkStart w:id="0" w:name="Blank_MP1_panel1"/>' if bookmark else ""
    end = '<w:bookmarkEnd w:id="0"/>' if bookmark else ""
    return (
        f'<w:p w14:paraId="1A2B3C4D" w14:textId="77777777">'
        f'<w:pPr><w:jc w:val="center"/><w:spacing w:after="0"/></w:pPr>{mark}'
        f'<w:r><w:rPr><w:b/><w:sz w:val="28"/></w:rPr><w:t>{text}</w:t></w:r>{end}</w:p>'
    )


def _cell(width: int, body: str) -> str:
    """One table cell of the given width, vertically centred."""
    return f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/><w:vAlign w:val="center"/></w:tcPr>{body}</w:tc>'


def _shape(n: int) -> str:
    """One rounded-rectangle outline anchored to the page: wps shape + VML fallback, no text."""
    return (
        '<w:p><w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing>'
        f'<wp:anchor distT="0" distB="0" distL="0" distR="0" simplePos="0" relativeHeight="{n}" '
        'behindDoc="1" locked="0" layoutInCell="1" allowOverlap="1">'
        f'<wp:docPr id="{n}" name="Rounded Rectangle {n}"/>'
        '<wps:wsp><wps:bodyPr/></wps:wsp></wp:anchor></w:drawing></mc:Choice>'
        f'<mc:Fallback><w:pict><v:roundrect id="shape{n}" arcsize="10923f" filled="f" '
        'strokecolor="#7f7f7f" strokeweight=".25pt"/></w:pict></mc:Fallback>'
        '</mc:AlternateContent></w:r></w:p>'
    )


@pytest.fixture
def template(tmp_path):
    """A synthetic labels .docx of the real shape: 5x3 table, exact 2" rows, 10 outline shapes."""
    rows = []
    for r in range(5):
        if r == 0:
            first = "".join(_caption_paragraph(c, bookmark=(i == 0)) for i, c in enumerate(CAPTIONS))
        else:
            first = "<w:p/>"
        rows.append(
            '<w:tr><w:trPr><w:trHeight w:val="2880" w:hRule="exact"/></w:trPr>'
            + _cell(5760, first) + _cell(270, "") + _cell(5760, "<w:p/>") + "</w:tr>"
        )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f"<w:document {NAMESPACES}><w:body>"
        '<w:tbl><w:tblPr><w:tblLayout w:type="fixed"/></w:tblPr>'
        '<w:tblGrid><w:gridCol w:w="5760"/><w:gridCol w:w="270"/><w:gridCol w:w="5760"/></w:tblGrid>'
        + "".join(rows) + "</w:tbl>"
        + "".join(_shape(n) for n in range(1, 11))
        + '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr></w:body></w:document>'
    )
    path = tmp_path / "labels_template.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", "<w:styles/>")
    return path


def _document(data: bytes) -> bytes:
    """Return word/document.xml from a rendered .docx."""
    with zipfile.ZipFile(BytesIO(data)) as z:
        return z.read("word/document.xml")


def _label_cells(xml: bytes) -> list[ET.Element]:
    """Return the ten label cells, labels 1-10 in reading order."""
    table = ET.fromstring(xml).find(f"{W}body/{W}tbl")
    return [tr.findall(f"{W}tc")[col] for tr in table.findall(f"{W}tr") for col in (0, 2)]


def _texts(cell: ET.Element) -> list[str]:
    """Return a label cell's paragraph texts."""
    return ["".join(t.text or "" for t in p.iter(f"{W}t")) for p in cell.findall(f"{W}p")]


def _label(n: int) -> Label:
    """A distinguishable label for flavor n."""
    return Label("Acme", "Pre-Workout", f"Flavor {n}", f"X260926-{n:02d}", "1 scoop serving (12.3 g)")


# --- serving size ------------------------------------------------------------

def test_grams_round_half_up_to_one_decimal():
    assert serving_grams(12250, []) == Decimal("12.3")      # round() would give 12.2
    assert serving_grams(12000, [300, 50]) == Decimal("12.4")  # 12.35 g, not 12.3499…
    assert serving_grams(12040, []) == Decimal("12.0")


def test_a_blank_amount_leaves_the_grams_unknown():
    assert serving_grams(None, [300]) is None
    assert serving_grams(12000, [300, None]) is None


def test_serving_size_text():
    assert serving_size(2, Decimal("12.3")) == "2 scoop serving (12.3 g)"
    assert serving_size(1.0, Decimal("8.0")) == "1 scoop serving (8.0 g)"
    assert serving_size(1.5, Decimal("8.0")) == "1.5 scoop serving (8.0 g)"
    assert serving_size(1, None) is None


def test_a_new_product_defaults_to_one_scoop_and_a_change_is_remembered(conn):
    assert fs.scoops_per_serving(conn, "Pre-Workout") == 1
    fs.set_scoops_per_serving(conn, "Pre-Workout", 2)
    assert fs.scoops_per_serving(conn, " pre-workout ") == 2
    assert fs.scoops_per_serving(conn, "Greens") == 1
    fs.set_scoops_per_serving(conn, "", 3)  # a blank product has nothing to key on
    assert fs.scoops_per_serving(conn, "") == 1


def test_labels_for_sheet_follow_the_profiles(conn):
    sheet = fs.create_sheet(conn, customer="Acme", product="Pre-Workout", servings=30)
    peach = fs.add_profile(conn, sheet, "Peach", "X-01", base_mg=12000)
    fs.add_line(conn, peach, typed_name="Peach flavor", mg_per_serving=350)
    fs.add_profile(conn, sheet, "Mango", "X-02", base_mg=None)
    fs.set_scoops_per_serving(conn, "Pre-Workout", 2)

    labels = labels_for_sheet(conn, sheet)
    assert [l.lines() for l in labels] == [
        ["Acme", "Pre-Workout", "Peach", "X-01", "2 scoop serving (12.4 g)"],
        ["Acme", "Pre-Workout", "Mango", "X-02", ""],
    ]


def test_the_etl_leaves_product_scoops_untouched(tmp_path):
    db_path = tmp_path / "materials.db"
    conn = init_db(db_path)
    fs.set_scoops_per_serving(conn, "Pre-Workout", 2)
    conn.close()

    etl.run(CsvInventorySource(SYNTHETIC_CSV), db_path)

    conn = init_db(db_path)
    assert fs.scoops_per_serving(conn, "Pre-Workout") == 2
    conn.close()


# --- the renderer ------------------------------------------------------------

def test_one_label_per_flavor_in_order_values_only(template):
    [doc] = render([_label(1), _label(2), _label(3)], template)
    cells = _label_cells(_document(doc))

    assert _texts(cells[0]) == ["Acme", "Pre-Workout", "Flavor 1", "X260926-01", "1 scoop serving (12.3 g)"]
    assert _texts(cells[1])[2] == "Flavor 2"   # label 2 is row 1, right column
    assert _texts(cells[2])[2] == "Flavor 3"   # label 3 is row 2, left column
    assert all(_texts(c) == [""] for c in cells[3:])  # unused labels stay empty
    assert not any(caption in _document(doc).decode() for caption in CAPTIONS)


def test_eleven_flavors_make_two_documents(template):
    docs = render([_label(n) for n in range(1, 12)], template)
    assert len(docs) == 2
    first, second = (_label_cells(_document(d)) for d in docs)
    assert [_texts(c)[2] for c in first] == [f"Flavor {n}" for n in range(1, 11)]
    assert _texts(second[0])[2] == "Flavor 11"
    assert all(_texts(c) == [""] for c in second[1:])


def test_no_flavors_no_documents(template):
    assert render([], template) == []


def test_a_blank_field_prints_empty_never_none(template):
    [doc] = render([Label(customer=None, product="P", flavor="F", sample_id=None)], template)
    assert _texts(_label_cells(_document(doc))[0]) == ["", "P", "F", "", ""]
    assert b"None" not in _document(doc)


def test_formatting_row_heights_and_shapes_are_unchanged(template):
    with zipfile.ZipFile(template) as z:
        source = z.read("word/document.xml")
    [doc] = render([_label(n) for n in range(1, 11)], template)
    xml = _document(doc)
    root = ET.fromstring(xml)

    label_one = _label_cells(source)[0].findall(f"{W}p")[0]
    for cell in _label_cells(xml):
        for p in cell.findall(f"{W}p"):
            assert ET.tostring(p.find(f"{W}pPr")) == ET.tostring(label_one.find(f"{W}pPr"))
            assert ET.tostring(p.find(f"{W}r/{W}rPr")) == ET.tostring(label_one.find(f"{W}r/{W}rPr"))

    heights = [h.attrib for h in root.iter(f"{W}trHeight")]
    assert heights == [{f"{W}val": "2880", f"{W}hRule": "exact"}] * 5

    def shapes(document: bytes) -> list[str]:
        """Canonical XML of every body paragraph after the table (the outlines)."""
        body = ET.fromstring(document).find(f"{W}body")
        return [ET.canonicalize(ET.tostring(el)) for el in list(body)[1:]]
    assert shapes(xml) == shapes(source)


def test_copies_drop_bookmarks_and_paragraph_ids_label_one_keeps_its_own(template):
    [doc] = render([_label(1), _label(2)], template)
    xml = _document(doc)
    assert xml.count(b"Blank_MP1_panel1") == 1
    assert len(re.findall(rb'w14:paraId="1A2B3C4D"', xml)) == 5


def test_root_keeps_the_declarations_mc_ignorable_names(template):
    [doc] = render([_label(1)], template)
    root_tag = re.search(rb"<w:document\b[^>]*>", _document(doc)).group()
    assert b'mc:Ignorable="w14 wp14"' in root_tag
    assert b"xmlns:wp14=" in root_tag and b"xmlns:w14=" in root_tag
    assert b"ns0:" not in _document(doc)


def test_other_parts_are_copied_verbatim(template):
    [doc] = render([_label(1)], template)
    with zipfile.ZipFile(BytesIO(doc)) as z, zipfile.ZipFile(template) as t:
        assert z.namelist() == t.namelist()
        assert z.read("word/styles.xml") == t.read("word/styles.xml")
