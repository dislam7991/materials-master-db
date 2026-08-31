"""Data-quality report over the staged inventory sheet.

Runs against `staging_inventory_raw`, independent of whether the load into
materials/lots succeeded — the point is to show the mess the sheet-only
workflow creates, including rows too broken to load at all. Run the ETL
first (`python -m dtf_materials.etl`) so staging is populated.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from difflib import SequenceMatcher

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


def print_report(findings: dict) -> None:
    total_flags = sum(len(v) for v in findings.values())
    print(f"=== Data Quality Report ===  ({total_flags} findings)\n")

    if findings["missing_part_num"]:
        rows = findings["missing_part_num"]
        print(f"[{len(rows)}] Rows with no DTF Part # (can't be linked to a material):")
        print(f"    source rows: {rows}\n")

    if findings["conflicting_part_num"]:
        print(f"[{len(findings['conflicting_part_num'])}] DTF Part #s reused across different material names:")
        for part_num, names, source_rows in findings["conflicting_part_num"]:
            print(f"    {part_num}: {names}  (source rows: {source_rows})")
        print()

    if findings["part_num_multiple_suppliers"]:
        print(f"[{len(findings['part_num_multiple_suppliers'])}] Part #s listed under more than one supplier "
              f"(only the first is kept on the material row):")
        for part_num, sups in findings["part_num_multiple_suppliers"][:10]:
            print(f"    {part_num}: {sups}")
        print()

    if findings["nonstandard_location"]:
        from collections import Counter
        counts = Counter(loc for _, loc in findings["nonstandard_location"])
        print(f"[{len(findings['nonstandard_location'])}] Locations not matching the expected code "
              f"format (e.g. 6L-27-D) or a known named location:")
        for loc, count in counts.most_common(10):
            rows_for = [n for n, l in findings["nonstandard_location"] if l == loc][:4]
            print(f"    {loc!r}  x{count}  (rows {rows_for}{'...' if count > 4 else ''})")
        print("    -> a repeated value here is usually a real named location to whitelist;")
        print("       a one-off is usually a typo.\n")

    if findings["missing_location"]:
        print(f"[{len(findings['missing_location'])}] Rows with no location.\n")

    if findings["nonpositive_price"]:
        print(f"[{len(findings['nonpositive_price'])}] Rows with a zero or negative price:")
        for n, val in findings["nonpositive_price"][:10]:
            print(f"    row {n}: {val!r}")
        print()

    if findings["possible_duplicate_supplier"]:
        print(f"[{len(findings['possible_duplicate_supplier'])}] Supplier spellings that look like the same company:")
        for a, b, ratio in findings["possible_duplicate_supplier"]:
            print(f"    '{a}'  ~  '{b}'   (similarity {ratio})")
        print()

    if findings["unparseable_price"]:
        print(f"[{len(findings['unparseable_price'])}] Prices that couldn't be parsed as numbers:")
        for n, val in findings["unparseable_price"][:15]:
            print(f"    row {n}: {val!r}")
        if len(findings["unparseable_price"]) > 15:
            print(f"    ... and {len(findings['unparseable_price']) - 15} more")
        print()

    if findings["missing_price"]:
        print(f"[{len(findings['missing_price'])}] Rows with a blank price.\n")

    if findings["unparseable_receiving_date"]:
        print(f"[{len(findings['unparseable_receiving_date'])}] Receiving dates that couldn't be parsed:")
        for n, val in findings["unparseable_receiving_date"][:15]:
            print(f"    row {n}: {val!r}")
        print()

    if findings["missing_receiving_date"]:
        print(f"[{len(findings['missing_receiving_date'])}] Rows with a blank receiving date.\n")

    if findings["missing_category"]:
        print(f"[{len(findings['missing_category'])}] Rows with a blank category.\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Data-quality report on the staged inventory sheet.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = parser.parse_args()
    print_report(report(args.db))


if __name__ == "__main__":
    main()
