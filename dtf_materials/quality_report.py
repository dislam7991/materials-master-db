"""Data-quality report over the staged inventory sheet.

Runs against `staging_inventory_raw`, independent of whether the load into
materials/lots succeeded — the point is to show the mess the sheet-only
workflow creates, including rows too broken to load at all. Run the ETL
first (`python -m dtf_materials.etl`) so staging is populated.

`--out report.md` additionally writes the findings as Markdown, so the mess
can be linked from the README or handed to someone at work who is never
going to run a Python command.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from . import cleaning
from .db import DEFAULT_DB_PATH, connect


def report(db_path=DEFAULT_DB_PATH) -> dict:
    conn = connect(db_path)
    rows = conn.execute("SELECT * FROM staging_inventory_raw ORDER BY source_row").fetchall()
    conn.close()

    findings: dict[str, list] = defaultdict(list)

    parts_seen: dict[str, set[str]] = defaultdict(set)   # part_num -> set of normalized names
    parts_rows: dict[str, list[int]] = defaultdict(list)
    supplier_spellings: dict[str, set[str]] = defaultdict(set)  # normalized key -> raw spellings seen
    parts_suppliers: dict[str, set[str]] = defaultdict(set)     # part # -> supplier keys seen

    for row in rows:
        n = row["source_row"]

        part_num = cleaning.clean_text(row["dtf_part_num"])
        if part_num is None:
            findings["missing_part_num"].append(n)
        else:
            name = cleaning.clean_text(row["material_name"])
            if name:
                parts_seen[part_num].add(cleaning.normalize_key(name))
            parts_rows[part_num].append(n)

        if cleaning.parse_price(row["price_per_kilo"]) is None and cleaning.clean_text(row["price_per_kilo"]) is not None:
            findings["unparseable_price"].append((n, row["price_per_kilo"]))
        elif cleaning.clean_text(row["price_per_kilo"]) is None:
            findings["missing_price"].append(n)

        if cleaning.clean_text(row["receiving_date"]) is not None and cleaning.parse_date(row["receiving_date"]) is None:
            findings["unparseable_receiving_date"].append((n, row["receiving_date"]))
        if cleaning.clean_text(row["receiving_date"]) is None:
            findings["missing_receiving_date"].append(n)

        supplier = cleaning.clean_text(row["supplier_mfg"])
        if supplier:
            supplier_spellings[cleaning.normalize_key(supplier)].add(supplier)
            if part_num is not None:
                parts_suppliers[part_num].add(cleaning.normalize_key(supplier))

        price = cleaning.parse_price(row["price_per_kilo"])
        if price is not None and price <= 0:
            findings["nonpositive_price"].append((n, row["price_per_kilo"]))

        if cleaning.clean_text(row["locations"]) is None:
            findings["missing_location"].append(n)
        else:
            for loc in cleaning.split_locations(row["locations"]):
                if not cleaning.is_standard_location(loc):
                    findings["nonstandard_location"].append((n, loc))

        if cleaning.clean_text(row["category"]) is None:
            findings["missing_category"].append(n)

    for part_num, sups in parts_suppliers.items():
        if len(sups) > 1:
            findings["part_num_multiple_suppliers"].append((part_num, sorted(sups)))

    for part_num, names in parts_seen.items():
        if len(names) > 1:
            findings["conflicting_part_num"].append((part_num, sorted(names), parts_rows[part_num]))

    # Suppliers that differ only by formatting have already been collapsed by
    # normalize_key upstream; here we look for spellings that are DIFFERENT
    # keys but suspiciously similar strings — the kind a human should review
    # for merging (e.g. "NutraSci" vs "Nutra Sci").
    # min() rather than an arbitrary set element: set iteration order for
    # strings varies with the process hash seed, which made this report's
    # output differ between runs on identical data.
    canonical_spellings = sorted(min(s) for s in supplier_spellings.values())
    for a in canonical_spellings:
        for b in canonical_spellings:
            if a >= b:
                continue
            ratio = SequenceMatcher(None, a.lower(), b.lower()).ratio()
            if ratio > 0.6:
                findings["possible_duplicate_supplier"].append((a, b, round(ratio, 2)))

    return findings


def build_sections(findings: dict) -> list[tuple[str, list[str]]]:
    """The report's content as (heading, detail lines) pairs.

    Content is built once here and rendered twice below, so the terminal
    output and the Markdown file cannot drift apart — the whole value of the
    file is that it says exactly what the run said. Detail lines carry no
    indentation; each renderer indents or fences them itself.
    """
    sections: list[tuple[str, list[str]]] = []

    def add(heading: str, details: list[str] | None = None) -> None:
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
    """The terminal rendering: heading, then its details indented under it."""
    total_flags = sum(len(v) for v in findings.values())
    lines = [f"=== Data Quality Report ===  ({total_flags} findings)", ""]
    for heading, details in build_sections(findings):
        lines.append(heading)
        lines.extend(f"    {detail}" for detail in details)
        lines.append("")
    return "\n".join(lines) + "\n"


def format_markdown(findings: dict) -> str:
    """The same findings as `format_text`, as a linkable Markdown artifact.

    Detail lines stay inside fenced blocks instead of being reflowed as
    prose. They quote raw cell values verbatim (`'1L-28-'`, `'12.50 USD'`),
    and escaping those for Markdown would risk the file misrepresenting what
    is actually in the sheet — which is the one thing this report is for.
    """
    total_flags = sum(len(v) for v in findings.values())
    lines = ["# Data Quality Report", "", f"{total_flags} findings in the staged inventory sheet.", ""]
    for heading, details in build_sections(findings):
        lines.append(f"## {heading}")
        lines.append("")
        if details:
            lines.extend(["```", *details, "```", ""])
    return "\n".join(lines) + "\n"


def print_report(findings: dict) -> None:
    print(format_text(findings), end="")


def main() -> None:
    parser = argparse.ArgumentParser(description="Data-quality report on the staged inventory sheet.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--out", metavar="PATH",
                        help="also write the findings to PATH as Markdown (e.g. --out report.md)")
    args = parser.parse_args()
    findings = report(args.db)
    print_report(findings)
    if args.out:
        Path(args.out).write_text(format_markdown(findings), encoding="utf-8")
        print(f"Wrote Markdown report to {args.out}")


if __name__ == "__main__":
    main()
