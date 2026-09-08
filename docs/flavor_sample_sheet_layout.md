# Flavor Sample Sheet — Layout & Decisions

Notes on the R&D lab's flavor sample catalog (Phase E), a second Google Sheet
entirely separate from the warehouse inventory — different account, different
shape, different purpose. Established from a real CSV export of the live
sheet, the same way the main inventory's `EXPECTED_HEADERS` were established
from a real run rather than assumption.

## Sheet identity

- **Sheet**: "DTF R&D Lab Inventory"
- **Tab**: "Flavor Sample Inventory"
- Owned on a personal (non-company) Google account, shared with R&D staff and
  with the same service account used for the main inventory.

## Real header (as of this writing)

One clean header row, no preamble (an earlier version of the sheet had a
2-row title/blank preamble before the header; that's since been removed):

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

**This header has already changed once** between two real exports taken days
apart ("Usage Level (recom)" → "Usage Level (recommended)", and a Part #
column was added). Treat it as something that can drift again, the same
caution that applies to the main sheet.

## Data-quality findings from real exports

- **Duplicate Sample Codes**: the first real export had 22 sample codes
  appearing on more than one row; a later cleaned export was down to 8. Kinds
  observed, from most to least common:
  - Same vendor, exact duplicate row (data entry re-typed the same sample)
  - Same vendor, same flavor, inconsistent naming ("Nat X" vs "X", or a
    spelling variant)
  - Same vendor, same code, two genuinely **different** flavors — a real
    data error, not a naming quirk (found and fixed once already: Peanut
    Butter / Fried Doughnut sharing a code)
  - **Different vendors sharing the same code by coincidence** — the
    important one structurally: Sample Code alone is not a safe unique
    identifier. (Found and fixed once already: Prinova / Virginia Dare both
    using `43582` for Pistachio. A second instance, `42936` — Prinova "Lime
    Key Type" vs. Virginia Dare "Lime" — was still present as of the most
    recent export and is unresolved.)
- **Pricing is usually absent, not messy.** Price info typically only comes
  in when a vendor is contacted again post-sampling — a blank price cell is
  the normal case, not a data-quality problem. As of the most recent export,
  17 of 467 rows have a price.
- **Part # is currently unpopulated** (0 of 467 rows) — the column was just
  added and will fill in over time as samples get adopted into real
  inventory.

## Identifier / linking strategy (decided, not yet built)

For the planned feature that shows a material's location across *both*
sheets (warehouse + lab), a lab sample links to a warehouse material by,
in order:

1. **DTF Part #** — once the lab sheet's Part # column is filled in for a
   row, match it directly against `materials.dtf_part_num`.
2. **Sample Code as a substring of the warehouse Material Name** — the
   observed real-world pattern (e.g. Sensapure "Mango 7182011" contains lab
   sample code `7182011`). Used as a fallback when there's no Part # yet.

A lab sample matching neither is simply not shown in that combined view —
not an error, just not linked yet. No fuzzy name-matching: two different
vendors' "Vanilla" should never be silently treated as the same material.

This matching logic is **not implemented yet** — this section records the
decision so building it later doesn't require re-deriving it.

## What's built so far

- `lab_samples` table (`db/schema.sql`) — flat, one row per sample, not
  split into staging + typed tables like materials/lots (no one-to-many
  relationship here to model).
- `dtf_materials/lab_samples.py` — loader: connects via the `[lab_sheet]`
  config section, validates the header (reusing the same whitespace
  normalization proven necessary for the main sheet), full-reloads the
  table each run, and flags duplicate sample codes.
- `search_lab_samples` / `get_lab_sample` in `queries.py`, and a **Lab
  Samples** tab in the app — search by vendor, flavor name, sample code, or
  Part #, mirroring the main "Material lookup" tab's live-search UX.

## Not built yet

- The combined warehouse+lab location view (needs the linking strategy above)
- A lab-specific quality report / in-app warnings tab surfacing flagged
  issues (duplicate codes, cross-vendor collisions) the way the main
  inventory's quality report does
