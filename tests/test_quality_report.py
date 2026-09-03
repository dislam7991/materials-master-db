"""Tests for the report's two renderings.

The Markdown file only has value if it says exactly what the terminal run
said — a file that quietly drops or reflows a finding is worse than no file,
because it gets linked and believed. So the interesting test is not that
Markdown is produced but that both renderings carry the same content.
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from dtf_materials import etl, quality_report
from dtf_materials.db import PROJECT_ROOT
from dtf_materials.sources import CsvInventorySource

SYNTHETIC_CSV = PROJECT_ROOT / "data" / "synthetic" / "raw_material_inventory.csv"


@pytest.fixture
def findings() -> dict:
    """One finding of several shapes: a plain count, a (row, value) pair, a
    section whose details are truncated, and one with no details at all."""
    f = defaultdict(list)
    f["missing_part_num"] = [4, 34, 36]
    f["conflicting_part_num"] = [("RM-1750", ["malic acid", "niacinamide"], [14, 41])]
    f["unparseable_price"] = [(12, "TBD"), (40, "call")]
    f["missing_category"] = [7, 9]
    return f


def test_markdown_carries_every_line_the_terminal_output_does(findings):
    text = quality_report.format_text(findings)
    markdown = quality_report.format_markdown(findings)

    sections = quality_report.build_sections(findings)
    assert sections, "the fixture should produce findings to render"
    for heading, details in sections:
        assert heading in text and heading in markdown
        for detail in details:
            assert detail in text and detail in markdown

    # The headline count is part of the content too: a file claiming a
    # different number of findings than the run reported is a bug.
    assert "(8 findings)" in text
    assert "8 findings" in markdown


def test_out_flag_writes_the_report(tmp_path, monkeypatch, capsys):
    """End to end through `main`, against a temp DB, so the flag itself is
    covered and not just the formatter behind it."""
    db_path = tmp_path / "test_materials.db"
    etl.run(CsvInventorySource(SYNTHETIC_CSV), db_path)
    out_path = tmp_path / "report.md"

    monkeypatch.setattr(
        "sys.argv",
        ["quality_report", "--db", str(db_path), "--out", str(out_path)],
    )
    quality_report.main()

    written = out_path.read_text(encoding="utf-8")
    assert written.startswith("# Data Quality Report")
    # stdout is unaffected by --out: the file is written *as well as*, not
    # instead of, the run's own output.
    assert "=== Data Quality Report ===" in capsys.readouterr().out
