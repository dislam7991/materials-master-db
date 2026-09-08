# Flavor sample sheet — real layout (E1)

What the **DTF R&D Lab Inventory** sheet actually looks like, established by
reading an export of its `Flavor Sample Inventory` tab rather than by
guessing. This is the same groundwork that was done for the main inventory
sheet before `SheetsInventorySource` was written (B2), and for the same
reason: that sheet's header row turned out to have three whitespace quirks
nobody predicted.

No flavor names, vendor names, sample codes, or prices appear here — only
shapes, counts, and vocabularies. The sheet itself stays out of the repo.

**Snapshot:** 485 rows, 25 columns, 482 data rows.

---

## 1. The tab is not a clean table

Three structural surprises, all of which break a naive "read the tab, first
row is the header" reader:

**The header is on row 3, not row 1.** Row 1 is a title banner
(`Flavor Inventory Database` in column A, rest empty). Row 2 is entirely
empty. Data starts on row 4.

**A second, unrelated table is parked in columns X and Y.** Rows 4–16 of
those two columns hold a "Taste and Aroma Lexicon" — a two-column reference
list pairing 11 sensory dimensions with their suggested descriptors. It is
documentation someone pasted beside the data,
not inventory. It shares row numbers with the first 13 flavor rows, so any
reader that takes whole rows will staple lexicon prose onto real records.

**Five trailing unnamed columns.** The header names 20 columns (A–T); the
sheet is 25 wide. Columns U–W are entirely empty, X–Y hold the lexicon.

Consequences for whatever reads this tab: skip 2 rows, take a fixed 20-column
slice, and ignore everything past column T.

## 2. Header row, verbatim

Exactly as they appear, in order. Unlike the main sheet, these carry no
leading, trailing, or doubled whitespace — the awkwardness here is in the
wording, not the spacing.

| Col | Header |
|---|---|
| A | `Vendor` |
| B | `Flavor Name` |
| C | `Sample Code` |
| D | `Flavor Declaration Type (Natural, N&A, Artificial, WONF)` |
| E | `Location (Lab)` |
| F | `Flavor Family` |
| G | `Category/Subcategory` |
| H | `Usage Level (recom)` |
| I | `Dry Aroma Descriptors` |
| J | `Aroma Intensity 0-5` |
| K | `Top Note` |
| L | `Mid Palate Character` |
| M | `Finish Note` |
| N | `Off Note Tendency` |
| O | `Matrix Performance` |
| P | `Best Pairings` |
| Q | `Tested In:` |
| R | `Allergens` |
| S | `Date Received` |
| T | `Price` |

Two to watch when these become constants: `Tested In:` ends in a colon, and
the column D header embeds its own value list in parentheses.

## 3. How full each column actually is

Out of 482 data rows:

| Column | Filled | Share |
|---|---:|---:|
| `Vendor` | 482 | 100% |
| `Flavor Name` | 482 | 100% |
| `Sample Code` | 482 | 100% |
| `Flavor Declaration Type` | 482 | 100% |
| `Location (Lab)` | 330 | 68% |
| `Category/Subcategory` | 74 | 15% |
| `Date Received` | 48 | 10% |
| `Flavor Family` | 23 | 5% |
| `Dry Aroma Descriptors` | 21 | 4% |
| `Aroma Intensity 0-5` | 20 | 4% |
| `Price` | 17 | 4% |
| `Tested In:` | 3 | <1% |
| `Top Note` | 3 | <1% |
| `Finish Note` | 3 | <1% |
| `Usage Level (recom)` | 4 | <1% |
| `Mid Palate Character` | 4 | <1% |
| `Off Note Tendency` | 2 | <1% |
| `Allergens` | 2 | <1% |
| `Matrix Performance` | 1 | <1% |
| `Best Pairings` | 0 | 0% |

The shape of this is worth naming plainly: **four columns are a real
inventory, and sixteen are an aspiration.** The sensory panel (I–P) is
someone's intended future state, filled in for a handful of rows. Columns
K–P together hold 13 values across 482 rows.

## 4. Column-by-column detail

### `Vendor` (A)
14 distinct values, all consistently spelled — no case or spacing variants
that normalize to the same company. This is cleaner than the main sheet,
where supplier aliasing was needed.

### `Flavor Name` (B)
Free text, always present. Plays the role `Material Name` plays in the main
sheet.

### `Sample Code` (C) — the identifier candidate
Always present, and **not unique**. See section 5; this is the finding that
matters most.

12 distinct shapes (digits shown as `#`, letters as `A`):

| Count | Shape |
|---:|---|
| 118 | `######` |
| 84 | `A########` |
| 82 | `A##-######` |
| 39 | `####A` |
| 34 | `#######` |
| 28 | `#####` |
| 24 | `#-####-####` |
| 19 | `AA######` |
| 13 | `AA-#####` |
| 13 | `AA#####` |
| 8 | `AAA####` |
| 5 | `########` |

The format is per-vendor, not house-wide — each vendor stamps its own
catalog number. No code has leading, trailing, or internal whitespace, so
no trimming is needed here.

### `Flavor Declaration Type` (D)
Always present, and effectively a controlled vocabulary — 7 distinct values
across 482 rows:

| Count | Value |
|---:|---|
| 333 | `Natural` |
| 77 | `N&A` |
| 68 | `Natural, WONF` |
| 1 | `Natural, Halal` |
| 1 | `WONF, Natural` |
| 1 | `WONF, Organic` |
| 1 | `Organic` |

Note `Natural, WONF` and `WONF, Natural` — the same declaration written in
two orders, which a strict equality check treats as two categories. The
field is really a *set* of tags stored as a comma-joined string. The header
advertises `Artificial` as a possible value; no row uses it.

### `Location (Lab)` (E)
10 distinct values, in three formats — and **none of them match the main
sheet's `6L-27-D` warehouse format**. Existing location parsing does not
apply here.

| Count | Shape | Example |
|---:|---|---|
| 169 | `A-#-#` | `A-2-1` |
| 83 | `AAAAAA AAAAAA AAA` | a three-word prose location |
| 78 | `A-AA#-A#` | `A-CB2-D1` |

The 83 prose entries are a plain-English description of where something
sits, not a code. 152 rows (32%) have no location at all.

### `Flavor Family` (F) and `Category/Subcategory` (G)
Both sparse, and the pair is muddled:

- `Flavor Family` is 23 values, all ALL-CAPS, drawn from a small taxonomy of
  8 broad families (fruit, dessert, savory and the like). Some family terms
  are also flavor names in their own right, so they are not enumerated here.
- `Category/Subcategory` is 74 values with no fixed vocabulary — some are
  single words, some are comma-joined descriptor lists, and casing is
  inconsistent (21 ALL-CAPS, 53 mixed or lower).
- **20 of those 74 are not categories at all — they are usage-level
  percentages** (`0.20 - 0.40%`, `0.1-0.3%`, `0.05 - 0.35 %`) that belong in
  column H. Column H itself holds only 4 values.

So the usage level a person would look for lives in the wrong column five
times more often than the right one.

### `Date Received` (S)
48 values, all `MM/DD/YYYY`. One clean format, unlike the main sheet.

### `Price` (T)
17 values, all carrying a unit and most carrying a currency symbol:
`$NN.NN/kg` (14), `NN.NN/kg` (2), `NN.N/lb` (1). Two things are embedded in
one cell — the amount and the unit of measure — and the unit is not constant,
so these are not comparable numbers until parsed apart. The main sheet's
price column has no unit suffix, so `cleaning.parse_price` will not handle
these as-is.

## 5. The identifier problem

`Sample Code` looks like a primary key and is not one. Of 482 rows:

| Key | Distinct | Colliding keys |
|---|---:|---:|
| `Sample Code` | 459 | 22 |
| `(Vendor, Sample Code)` | 461 | 20 |
| `(Vendor, Flavor Name, Sample Code)` | 470 | 12 |

Splitting the 22 duplicated sample codes by what else differs:

- **11 are exact repeats** — same vendor, same flavor name, same code, on
  more than one row. Probably a re-received sample, or the same sample
  entered twice.
- **11 are genuine collisions** — the same code against a *different* flavor
  name, and in two cases against a different vendor as well. One code covers
  three rows and two different flavors.

No column or combination of columns in this sheet uniquely identifies a row.
Adding `Flavor Name` narrows it but still leaves 12 colliding keys.

This is exactly the ambiguity the project's "flag, don't guess" principle
exists for: the ETL must not silently merge two flavors that share a code,
and it must not invent a distinguishing suffix. **Settling this is E2's job**
— this document only establishes that the problem is real and sizes it at
22 codes across 33 rows.

## 6. What this means for E2 and E3

Carried forward, not decided here:

1. **No natural key exists.** E2 has to choose: a surrogate id plus a
   flagged-conflict report, a human-assigned sample id added to the sheet,
   or a composite key that accepts the 12 remaining collisions as true
   duplicates to be merged.
2. **`materials.is_sample_only` and the nullable `dtf_part_num` fit.** No row
   in this sheet has a DTF Part #, which is what those columns were added
   for. Nothing in the schema needs to change to hold these rows.
3. **The sensory columns are not worth modelling yet.** Sixteen columns hold
   under 5% fill and one holds nothing. Loading four reliable columns plus
   location, date, and price is the whole value here today.
4. **Three cleaning gaps** the existing `cleaning.py` does not cover: prices
   with a unit suffix, prose locations alongside coded ones, and declaration
   tags whose order varies.
5. **The lexicon block must be excluded by column, not by content.** It sits
   in X/Y beside real rows; a row-level filter would drop real records.
