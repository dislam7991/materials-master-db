"""Generate a fake "Raw Material Inventory" sheet as CSV.

Matches the real sheet's column structure exactly, and deliberately injects the
same kinds of dirtiness the real sheet has:
  * duplicate DTF Part #s (same part on multiple rows with conflicting info)
  * missing DTF Part #s
  * supplier names spelled several different ways
  * prices stored as text ("$12.50", "12,40", " 18.00 ", "TBD", "call")
  * inconsistent date formats (m/d/yyyy, yyyy-mm-dd, "Jan 5 2026", blank)
  * location cells in the real formats: a code ("6L-27-D"), several codes
    separated by commas/slashes/spaces, named locations ("cooler"), truncated
    codes ("1L-28-"), wrong case, and blanks
  * stray whitespace and casing noise in names

Seeded, so the output is reproducible. Stdlib only.

Usage:  python scripts/generate_synthetic_sheet.py [out.csv]
"""

from __future__ import annotations

import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

SEED = 42
N_MATERIALS = 60          # distinct materials
EXTRA_LOT_ROWS = 45       # additional receivings of existing materials
DUP_PART_ROWS = 6         # rows that reuse a part # already on another material's row
MISSING_PART_ROWS = 5     # rows with a blank part #

HEADERS = [
    "Receiving Date", "Locations", "DTF Lot #", "DTF Part #", "Status",
    "Allergen", "Material Name", "Supplier/MFG", "Lot/Batch", "EXP. Date",
    "Start Day Stock", "Current Stock", "Filter Moving/Date",
    "Check/Cycle count", "Category (1 Raw)(2 Flavor)", "Price Per Kilo",
    "Total cost", "Ready To Archive",
]

# canonical supplier -> list of spellings that appear in the sheet
SUPPLIERS = {
    "Sensapure Flavors": ["Sensapure Flavors", "Sensapure", "SensaPure Flavors Inc", "sensapure flavors"],
    "NutraSci": ["NutraSci", "Nutra Sci", "NutraSci LLC"],
    "Prinova": ["Prinova", "Prinova USA", "PRINOVA"],
    "Glanbia Nutritionals": ["Glanbia Nutritionals", "Glanbia", "Glanbia Nutr."],
    "Ingredion": ["Ingredion", "Ingredion Inc"],
    "Blue California": ["Blue California", "Blue CA", "Blue California Inc."],
    "Anderson Advanced Ingredients": ["Anderson Advanced Ingredients", "Anderson Adv Ing", "Anderson"],
    "FlavorSum": ["FlavorSum", "Flavor Sum", "Flavorsum LLC"],
}

RAW_MATERIALS = [
    "Caffeine Anhydrous", "L-Theanine", "Creatine Monohydrate", "Beta-Alanine",
    "Citrulline Malate 2:1", "Ascorbic Acid (Vitamin C)", "Zinc Gluconate",
    "Magnesium Citrate", "Taurine", "L-Carnitine Tartrate", "Green Tea Extract 50% EGCG",
    "Ashwagandha Extract KSM-66", "Melatonin", "Niacinamide", "Pyridoxine HCl (B6)",
    "Methylcobalamin (B12) 1%", "Potassium Chloride", "Sodium Citrate", "Inulin (Chicory)",
    "Whey Protein Isolate 90%", "Pea Protein 80%", "Collagen Peptides Type I&III",
    "Stevia Extract Reb-A 97%", "Sucralose", "Monk Fruit Extract 25% MV",
    "Citric Acid Anhydrous", "Malic Acid", "Silicon Dioxide", "Maltodextrin DE10",
    "MCT Oil Powder 70%",
]

FLAVOR_MATERIALS = [
    "Blueberry Flavor Nat WONF", "Strawberry Flavor Nat", "Fruit Punch Flavor N&A",
    "Blue Raspberry Flavor Art", "Watermelon Flavor Nat WONF", "Vanilla Flavor Nat",
    "Chocolate Flavor Nat", "Peach Mango Flavor N&A", "Lemonade Flavor Nat",
    "Green Apple Flavor Art", "Orange Cream Flavor N&A", "Pineapple Flavor Nat WONF",
    "Grape Flavor Art", "Cherry Limeade Flavor N&A", "Salted Caramel Flavor Nat",
    "Masking Agent MSK-201", "Bitter Blocker BB-40", "Sweetness Enhancer SE-11",
    "Cotton Candy Flavor Art", "Mocha Flavor Nat",
]

ALLERGENS = ["None", "None", "None", "None", "Milk", "Soy", "Tree Nut (Coconut)", ""]
STATUSES = ["Active", "Active", "Active", "QUARANTINE", "Hold", "active", ""]
# Real locations are hyphen-joined alphanumeric codes like "6L-27-D", plus a
# named "cooler". Generated rather than listed so the synthetic sheet has a
# realistic spread of codes across racks/bays/levels.
def _location_code(rng: random.Random) -> str:
    return f"{rng.randint(1, 8)}{rng.choice('LR')}-{rng.randint(1, 40):02d}-{rng.choice('ABCDEF')}"

NAMED_LOCATIONS = ["cooler", "cooler", "back cooler", "QC hold"]


def messy_date(d: date, rng: random.Random) -> str:
    style = rng.random()
    if style < 0.55:
        return f"{d.month}/{d.day}/{d.year}"
    if style < 0.80:
        return d.isoformat()
    if style < 0.92:
        return d.strftime("%b %d %Y")
    return ""  # missing


def messy_price(base: float, rng: random.Random) -> str:
    style = rng.random()
    if style < 0.45:
        return f"{base:.2f}"
    if style < 0.65:
        return f"${base:.2f}"
    if style < 0.75:
        return f" {base:.2f} "
    if style < 0.83:
        return f"{base:.2f}".replace(".", ",")   # European decimal comma
    if style < 0.90:
        return f"${base:,.2f}/kg"
    if style < 0.96:
        return ""
    return rng.choice(["TBD", "call", "see PO"])


def messy_location(rng: random.Random) -> str:
    """Build a Locations cell in the real formats, including the dirty ones:
    inconsistent separators/spacing, wrong case, a truncated code, a
    free-text named location, and the occasional blank cell."""
    r = rng.random()
    if r < 0.06:
        return ""                                   # blank / NULL
    if r < 0.14:
        return rng.choice(NAMED_LOCATIONS)          # free-text named location
    if r < 0.18:
        code = _location_code(rng)
        return code[:-1] if rng.random() < 0.5 else code.lower()   # truncated or lowercased

    n = rng.choices([1, 1, 1, 2, 3], k=1)[0]
    codes = [_location_code(rng) for _ in range(n)]
    if n == 1:
        return codes[0]
    sep = rng.choices([", ", ",", " / ", " "], weights=[70, 10, 10, 10], k=1)[0]
    return sep.join(codes)


def messy_name(name: str, rng: random.Random) -> str:
    r = rng.random()
    if r < 0.08:
        return " " + name
    if r < 0.12:
        return name.upper()
    if r < 0.15:
        return name + "  "
    return name


def make_row(part_num: str, name: str, category: int, supplier_variants: list[str],
             base_price: float, seq: int, rng: random.Random) -> list[str]:
    received = date(2024, 1, 1) + timedelta(days=rng.randint(0, 900))
    exp = received + timedelta(days=rng.choice([365, 540, 730, 1095]))
    start_stock = round(rng.uniform(0.5, 120.0), 2)
    current = round(start_stock * rng.uniform(0.05, 1.0), 2)
    price = base_price * rng.uniform(0.92, 1.12)  # drifts per receiving
    price_txt = messy_price(price, rng)
    # Total cost is sometimes a stale/incorrect manual calc, sometimes blank
    if rng.random() < 0.7:
        total = f"{current * price:.2f}"
    elif rng.random() < 0.5:
        total = f"{start_stock * price:.2f}"   # stale: uses starting stock
    else:
        total = ""
    return [
        messy_date(received, rng),
        messy_location(rng),
        f"DTF-{received.year % 100:02d}{rng.randint(1, 9999):04d}",
        part_num,
        rng.choice(STATUSES),
        rng.choice(ALLERGENS),
        messy_name(name, rng),
        rng.choice(supplier_variants),
        f"{rng.choice(['LOT', 'B', ''])}{rng.randint(10000, 999999)}",
        messy_date(exp, rng),
        f"{start_stock}",
        f"{current}" if rng.random() > 0.06 else "",
        rng.choice(["", "", "Yes 3/2025", "moved 1/12/25"]),
        rng.choice(["", "", "OK", "ok 5/2025", "recount"]),
        str(category) if rng.random() > 0.05 else "",
        price_txt,
        total,
        # Archived lots are a minority in reality; an even split made most
        # materials look like they had no current location at all.
        rng.choices(["", "Yes", "y", "TRUE"], weights=[82, 8, 5, 5], k=1)[0],
    ]


def main(out_path: Path) -> None:
    rng = random.Random(SEED)

    # Build the material catalog: (part_num, name, category, supplier_variants, base_price)
    catalog = []
    used_parts = set()
    for name in RAW_MATERIALS:
        cat = 1
        supplier = rng.choice(list(SUPPLIERS.values()))
        part = f"RM-{rng.randint(1000, 9999)}"
        while part in used_parts:
            part = f"RM-{rng.randint(1000, 9999)}"
        used_parts.add(part)
        catalog.append((part, name, cat, supplier, rng.uniform(4, 90)))
    for name in FLAVOR_MATERIALS:
        supplier = SUPPLIERS["Sensapure Flavors"] if rng.random() < 0.5 else rng.choice(list(SUPPLIERS.values()))
        part = f"FL-{rng.randint(1000, 9999)}"
        while part in used_parts:
            part = f"FL-{rng.randint(1000, 9999)}"
        used_parts.add(part)
        catalog.append((part, name, 2, supplier, rng.uniform(15, 160)))
    catalog = catalog[:N_MATERIALS]

    rows = []
    # one row per material
    for i, (part, name, cat, sup, price) in enumerate(catalog):
        rows.append(make_row(part, name, cat, sup, price, i, rng))
    # extra receivings (legit duplicates of part # = multiple lots, the good kind)
    for i in range(EXTRA_LOT_ROWS):
        part, name, cat, sup, price = rng.choice(catalog)
        rows.append(make_row(part, name, cat, sup, price, i, rng))
    # the bad kind of duplicate: same part #, *different* material name
    for i in range(DUP_PART_ROWS):
        victim = rng.choice(catalog)
        other = rng.choice(catalog)
        while other[0] == victim[0]:
            other = rng.choice(catalog)
        rows.append(make_row(victim[0], other[1], other[2], other[3], other[4], i, rng))
    # missing part #s
    for i in range(MISSING_PART_ROWS):
        _, name, cat, sup, price = rng.choice(catalog)
        rows.append(make_row("", name, cat, sup, price, i, rng))

    rng.shuffle(rows)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(HEADERS)
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows -> {out_path}")
    print(f"  distinct materials: {len(catalog)}")
    print(f"  extra lot rows (legit part# repeats): {EXTRA_LOT_ROWS}")
    print(f"  conflicting duplicate part# rows: {DUP_PART_ROWS}")
    print(f"  missing part# rows: {MISSING_PART_ROWS}")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/synthetic/raw_material_inventory.csv")
    main(out)
