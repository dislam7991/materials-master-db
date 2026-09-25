"""Data-quality report over the staged inventory sheet.

Reads `staging_inventory_raw`, so it covers rows too broken to load at all.
Run the ETL first so staging is populated.

Run with:  python -m dtf_materials.quality_report [--db PATH] [--out report.md]
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from . import cleaning
from .db import DEFAULT_DB_PATH, connect


# Two supplier spellings more similar than this are flagged as a possible duplicate.
SUPPLIER_SIMILARITY_THRESHOLD = 0.6


def report(db_path=DEFAULT_DB_PATH) -> dict:
    """Return every data-quality finding in staging, as {finding type: list of findings}."""
    rows = _load_staging(db_path)
    findings: dict[str, list] = defaultdict(list)
    for row in rows:
        for key, value in _row_findings(row):
            findings[key].append(value)
    findings["part_num_multiple_suppliers"] += _part_nums_with_several_suppliers(rows)
    findings["conflicting_part_num"] += _conflicting_part_nums(rows)
    findings["possible_duplicate_supplier"] += _similar_suppliers(rows)
    return findings


def _load_staging(db_path) -> list:
    """Return every staged source row, in sheet order."""
    conn = connect(db_path)
    rows = conn.execute("SELECT * FROM staging_inventory_raw ORDER BY source_row").fetchall()
    conn.close()
    return rows


def _row_findings(row) -> list[tuple[str, object]]:
    """Return (finding type, detail) pairs for the problems within one staged row."""
    n = row["source_row"]
    found: list[tuple[str, object]] = []

    if cleaning.clean_text(row["dtf_part_num"]) is None:
        found.append(("missing_part_num", n))

    price_text = cleaning.clean_text(row["price_per_kilo"])
    price = cleaning.parse_price(row["price_per_kilo"])
    if price_text is None:
        found.append(("missing_price", n))
    elif price is None:
        found.append(("unparseable_price", (n, row["price_per_kilo"])))
    elif price <= 0:
        found.append(("nonpositive_price", (n, row["price_per_kilo"])))

    if cleaning.clean_text(row["receiving_date"]) is None:
        found.append(("missing_receiving_date", n))
    elif cleaning.parse_date(row["receiving_date"]) is None:
        found.append(("unparseable_receiving_date", (n, row["receiving_date"])))

    if cleaning.clean_text(row["locations"]) is None:
        found.append(("missing_location", n))
    for loc in cleaning.split_locations(row["locations"]):
        if not cleaning.is_standard_location(loc):
            found.append(("nonstandard_location", (n, loc)))

    if cleaning.clean_text(row["category"]) is None:
        found.append(("missing_category", n))
    return found


def _part_nums_with_several_suppliers(rows) -> list[tuple[str, list[str]]]:
    """Return (part #, supplier keys) for every part number listed under more than one supplier."""
    suppliers: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        part_num = cleaning.clean_text(row["dtf_part_num"])
        supplier = cleaning.clean_text(row["supplier_mfg"])
        if part_num is not None and supplier:
            suppliers[part_num].add(cleaning.normalize_key(supplier))
    return [(part_num, sorted(keys)) for part_num, keys in suppliers.items() if len(keys) > 1]


def _conflicting_part_nums(rows) -> list[tuple[str, list[str], list[int]]]:
    """Return (part #, names, source rows) for every part number used for different material names."""
    names: dict[str, set[str]] = defaultdict(set)
    source_rows: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        part_num = cleaning.clean_text(row["dtf_part_num"])
        if part_num is None:
            continue
        name = cleaning.clean_text(row["material_name"])
        if name:
            names[part_num].add(cleaning.normalize_key(name))
        source_rows[part_num].append(row["source_row"])
    return [
        (part_num, sorted(part_names), source_rows[part_num])
        for part_num, part_names in names.items() if len(part_names) > 1
    ]


def _similar_suppliers(rows) -> list[tuple[str, str, float]]:
    """Return (spelling, spelling, similarity) for supplier names that look like the same company.

    Spellings differing only in case/spacing are already one supplier; this
    flags different keys with similar text ("NutraSci" vs "Nutra Sci") for a
    human to review. min() picks each key's representative spelling
    deterministically, so the report is the same on every run.
    """
    spellings: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        supplier = cleaning.clean_text(row["supplier_mfg"])
        if supplier:
            spellings[cleaning.normalize_key(supplier)].add(supplier)

    canonical = sorted(min(s) for s in spellings.values())
    similar = []
    for a in canonical:
        for b in canonical:
            if a >= b:
                continue
            ratio = SequenceMatcher(None, a.lower(), b.lower()).ratio()
            if ratio > SUPPLIER_SIMILARITY_THRESHOLD:
                similar.append((a, b, round(ratio, 2)))
    return similar


def build_sections(findings: dict) -> list[tuple[str, list[str]]]:
    """Return the report's content as (heading, detail lines) pairs.

    Built once and rendered twice (text and Markdown), so the two can't drift apart.
    """
    sections: list[tuple[str, list[str]]] = []

    def add(heading: str, details: list[str] | None = None) -> None:
        """Append one section."""
        sections.append((heading, details or []))

    if findings["missing_part_num"]:
        rows = findings["missing_part_num"]
        add(f"[{len(rows)}] Rows with no DTF Part # (can't be linked to a material):",
            [f"source rows: {rows}"])

    if findings["conflicting_part_num"]:
        add(f"[{len(findings['conflicting_part_num'])}] DTF Part #s reused across different material names:",
            [f"{part_num}: {names}  (source rows: {source_rows})"
             for part_num, names, source_rows in findings["conflicting_part_num"]])

    if findings["part_num_multiple_suppliers"]:
        add(f"[{len(findings['part_num_multiple_suppliers'])}] Part #s listed under more than one supplier "
            f"(only the first is kept on the material row):",
            [f"{part_num}: {sups}"
             for part_num, sups in findings["part_num_multiple_suppliers"][:10]])

    if findings["nonstandard_location"]:
        counts = Counter(loc for _, loc in findings["nonstandard_location"])
        details = []
        for loc, count in counts.most_common(10):
            rows_for = [n for n, l in findings["nonstandard_location"] if l == loc][:4]
            details.append(f"{loc!r}  x{count}  (rows {rows_for}{'...' if count > 4 else ''})")
        details.append("-> a repeated value here is usually a real named location to whitelist;")
        details.append("   a one-off is usually a typo.")
        add(f"[{len(findings['nonstandard_location'])}] Locations not matching the expected code "
            f"format (e.g. 6L-27-D) or a known named location:", details)

    if findings["missing_location"]:
        add(f"[{len(findings['missing_location'])}] Rows with no location.")

    if findings["nonpositive_price"]:
        add(f"[{len(findings['nonpositive_price'])}] Rows with a zero or negative price:",
            [f"row {n}: {val!r}" for n, val in findings["nonpositive_price"][:10]])

    if findings["possible_duplicate_supplier"]:
        add(f"[{len(findings['possible_duplicate_supplier'])}] Supplier spellings that look like the same company:",
            [f"'{a}'  ~  '{b}'   (similarity {ratio})"
             for a, b, ratio in findings["possible_duplicate_supplier"]])

    if findings["unparseable_price"]:
        details = [f"row {n}: {val!r}" for n, val in findings["unparseable_price"][:15]]
        if len(findings["unparseable_price"]) > 15:
            details.append(f"... and {len(findings['unparseable_price']) - 15} more")
        add(f"[{len(findings['unparseable_price'])}] Prices that couldn't be parsed as numbers:", details)

    if findings["missing_price"]:
        add(f"[{len(findings['missing_price'])}] Rows with a blank price.")

    if findings["unparseable_receiving_date"]:
        add(f"[{len(findings['unparseable_receiving_date'])}] Receiving dates that couldn't be parsed:",
            [f"row {n}: {val!r}" for n, val in findings["unparseable_receiving_date"][:15]])

    if findings["missing_receiving_date"]:
        add(f"[{len(findings['missing_receiving_date'])}] Rows with a blank receiving date.")

    if findings["missing_category"]:
        add(f"[{len(findings['missing_category'])}] Rows with a blank category.")

    return sections


def format_text(findings: dict) -> str:
    """Render the report for the terminal: each heading with its details indented under it."""
    total_flags = sum(len(v) for v in findings.values())
    lines = [f"=== Data Quality Report ===  ({total_flags} findings)", ""]
    for heading, details in build_sections(findings):
        lines.append(heading)
        lines.extend(f"    {detail}" for detail in details)
        lines.append("")
    return "\n".join(lines) + "\n"


def format_markdown(findings: dict) -> str:
    """Render the same report as Markdown.

    Details stay in fenced blocks so raw cell values are quoted verbatim, never
    escaped or reflowed.
    """
    total_flags = sum(len(v) for v in findings.values())
    lines = ["# Data Quality Report", "", f"{total_flags} findings in the staged inventory sheet.", ""]
    for heading, details in build_sections(findings):
        lines.append(f"## {heading}")
        lines.append("")
        if details:
            lines.extend(["```", *details, "```", ""])
    return "\n".join(lines) + "\n"


def main() -> None:
    """Command-line entry point: print the report, and write it as Markdown with --out."""
    parser = argparse.ArgumentParser(description="Data-quality report on the staged inventory sheet.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--out", metavar="PATH",
                        help="also write the findings to PATH as Markdown (e.g. --out report.md)")
    args = parser.parse_args()
    findings = report(args.db)
    print(format_text(findings), end="")
    if args.out:
        Path(args.out).write_text(format_markdown(findings), encoding="utf-8")
        print(f"Wrote Markdown report to {args.out}")


if __name__ == "__main__":
    main()
