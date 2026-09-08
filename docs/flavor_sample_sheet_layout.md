# Flavor Sample Sheet — Layout & Decisions

Notes on the R&D lab's flavor sample catalog (Phase E), a second Google Sheet
entirely separate from the warehouse inventory — different account, different
shape, different purpose. Established the same way the main inventory's
`EXPECTED_HEADERS` were: from real exports, not assumption.

No flavor names, vendor names, sample codes, or prices are reproduced in
this document beyond the small number needed as concrete examples — only
shapes, counts, and vocabularies. The sheet itself stays out of the repo.

## Sheet identity

- **Sheet**: "DTF R&D Lab Inventory"
- **Tab**: "Flavor Sample Inventory"
- Owned on a personal (non-company) Google account, shared with R&D staff and
  with the same service account used for the main inventory.

## 1. Structural pass against the original export

Before any cleanup, the tab was not a clean table. Three structural
surprises, all of which break a naive "read the tab, first row is the
header" reader:

**The header sat on row 3, not row 1.** Row 1 was a title banner
(`Flavor Inventory Database` in column A, rest empty); row 2 was entirely
empty; data started on row 4.

**A second, unrelated table was parked in columns X and Y.** Rows 4–16 of
those two columns held a "Taste and Aroma Lexicon" — a two-column reference
list pairing 11 sensory dimensions with their suggested descriptors.
Documentation someone pasted beside the data, not inventory — sharing row
numbers with the first 13 flavor rows, so a reader that takes whole rows
would staple lexicon prose onto real records.

**Five trailing unnamed columns.** The header named 20 columns (A–T); the
sheet was 25 wide. Columns U–W were empty, X–Y held the lexicon.

**Both of these have since been fixed by the user**: the lexicon table was
moved to its own separate sheet, and the title/blank preamble rows were
removed. The current header sits on row 1. See section 3 for the sheet as
it exists now.

### Column fill rates (original export, 482 data rows)

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
| everything else (K–P, R, `Tested In:`) | 0–4 each | <1% |

**Four columns are a real inventory, sixteen are an aspiration.** The
sensory panel (columns I–P) is someone's intended future state, filled in
for a handful of rows — this is exactly why the app shows every field even
when blank (see "What's built so far" below), rather than waiting for full
data before it's useful.

### Notable per-column findings

- **`Sample Code` is not unique** — see section 2, the finding that matters
  most.
- **`Flavor Declaration Type`** is effectively a controlled vocabulary (7
  distinct values), but `Natural, WONF` and `WONF, Natural` appear as
  separate values — the same declaration written in two orders, which a
  strict equality check treats as two categories. It's really a *set* of
  tags stored as a comma-joined string. **Still unhandled** — no code
  normalizes tag order today.
- **`Location (Lab)`** used three formats (a code like `A-2-1`, a
  three-word prose description, or a different code shape like `A-CB2-D1`)
  — **none of which match the warehouse's `6L-27-D` format**, so the
  existing location parser does not apply here. **Still unhandled.**
- **`Category/Subcategory` and `Usage Level` were muddled**: 20 of 74
  Category/Subcategory values were actually usage-level percentages
  (`0.20 - 0.40%`) that belonged in the Usage Level column instead, which
  itself held only 4 values. The usage level a person would look for lived
  in the wrong column five times more often than the right one.
- **`Price`** carried both a currency symbol and a unit suffix in the same
  cell (`$NN.NN/kg`, a couple of `NN.N/lb`) — two things embedded in one
  string, not directly comparable until parsed apart. **Since resolved**:
  the column is now labeled `Price ($/kg)` and cells are unit-free (see
  section 3) — `cleaning.parse_price` already handles the current format
  correctly.

## 2. The identifier problem

Two distinct questions came up under "the identifier problem," worth
answering separately rather than conflating:

### (a) What identifies a row within this sheet's own table?

`Sample Code` looks like a primary key and is not one. In the original
export, of 482 rows:

| Key | Distinct | Colliding keys |
|---|---:|---:|
| `Sample Code` alone | 459 | 22 |
| `(Vendor, Sample Code)` | 461 | 20 |
| `(Vendor, Flavor Name, Sample Code)` | 470 | 12 |

No column or combination uniquely identifies a row. Splitting the 22
duplicated codes by what else differed: about half were exact repeats (same
vendor, same flavor, same code — likely a re-received sample or a data-entry
double-entry), and about half were genuine collisions — the same code
against a *different* flavor, in two cases against a different vendor too.
One code covered three rows and two different flavors.

This is exactly the ambiguity the project's "flag, don't guess" principle
exists for: the loader must not silently merge two flavors sharing a code,
and must not invent a distinguishing suffix.

**Decided and built**: `lab_samples` uses a surrogate `lab_sample_id`
(auto-increment) as its real row identity — Sample Code is stored as a
plain column, never the key. The loader (`dtf_materials/lab_samples.py`)
flags every sample code seen on more than one row as a warning rather than
deduplicating, merging, or picking one.

By the most recent real export (467 rows, after the user's cleanup — blank
rows removed, several collisions manually fixed), 8 sample codes still
collide. One is a **cross-vendor** collision (Prinova "Lime Key Type" vs.
Virginia Dare "Lime", both `42936`) — the same category of issue as a
Prinova/Virginia Dare Pistachio collision (`43582`) found and fixed earlier,
confirming this pattern recurs and is worth watching for, not a one-off.

### (b) How does a lab sample link to a warehouse material?

For the planned feature that shows a material's location across *both*
sheets (warehouse + lab) — not yet built. A lab sample links to a warehouse
`materials` row by, in order:

1. **DTF Part #** — once the lab sheet's Part # column (added after this
   structural pass; 0 of 467 rows filled in as of the most recent export)
   is filled in for a row, match it directly against `materials.dtf_part_num`.
2. **Sample Code as a substring of the warehouse Material Name** — the
   observed real-world pattern (e.g. Sensapure "Mango 7182011" contains lab
   sample code `7182011`). Used as a fallback when there's no Part # yet.

A lab sample matching neither is simply not shown in that combined view —
not an error, just not linked yet. No fuzzy name-matching, ever: two
different vendors' "Vanilla" must never be silently treated as the same
material.

## 3. Real header, current state

One clean header row, no preamble, as of the most recent real export:

```
Vendor | Flavor Name | Sample Code | Flavor Declaration Type (Natural, N&A,
Artificial, WONF) | Location (Lab) | Flavor Family | Category/Subcategory |
Usage Level (recommended) | Dry Aroma Descriptors | Aroma Intensity 0-5 |
Top Note | Mid Palate Character | Finish Note | Off Note Tendency | Matrix
Performance | Best Pairings | Tested In: | Allergens | Date Received |
Price ($/kg) | Part # (If applicable)
```

The exact list lives in code as `dtf_materials.lab_samples.EXPECTED_LAB_HEADERS`
— that's the source of truth the loader actually checks against; this doc is
the human-readable version.

**This header has already changed twice**: the original structural pass
(section 1) predates the lexicon-table/preamble cleanup entirely, and
between the two post-cleanup exports, "Usage Level (recom)" became "Usage
Level (recommended)" and a Part # column was added. Treat it as something
that can drift again, the same caution that applies to the main sheet.

Note also: `Tested In:` ends in a colon, and the Flavor Declaration Type
header embeds its own value list in parentheses — both are handled
correctly by the existing whitespace-normalization logic (they aren't
whitespace issues), just worth knowing if the exact string ever needs to be
typed by hand.

## What's built so far

- `lab_samples` table (`db/schema.sql`) — flat, one row per sample, not
  split into staging + typed tables like materials/lots (no one-to-many
  relationship here to model). Uses a surrogate `lab_sample_id`, per the
  identifier decision above.
- `dtf_materials/lab_samples.py` — loader: connects via the `[lab_sheet]`
  config section, validates the header (reusing the same whitespace
  normalization proven necessary for the main sheet), full-reloads the
  table each run, and flags duplicate sample codes rather than resolving
  them silently.
- `search_lab_samples` / `get_lab_sample` in `queries.py`, and a **Lab
  Samples** tab in the app — search by vendor, flavor name, sample code, or
  Part #, mirroring the main "Material lookup" tab's live-search UX. Every
  field is shown in the detail view even when blank, since most of the
  sensory columns are a work in progress, not broken.

## Not built yet

- The combined warehouse+lab location view using the section 2(b) matching
  strategy
- A lab-specific quality report, or an in-app warnings tab surfacing
  flagged issues (duplicate codes, cross-vendor collisions) the way the
  main inventory's quality report does
- Cleaning for prose/differently-shaped lab locations (section 1) and
  declaration-type tag order (section 1) — both still unhandled
