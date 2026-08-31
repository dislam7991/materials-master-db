# DTF Materials Master

A small internal data platform for a supplement contract manufacturer with no ERP:
a SQLite materials master database, a Python ETL pipeline that feeds it from the
company's inventory spreadsheet, a data-quality report that surfaces the mess the
spreadsheet-only workflow creates, and a Streamlit lookup app.

**All data in this repository is synthetic.** The generator in
`scripts/generate_synthetic_sheet.py` produces a fake inventory sheet with the same
column structure and the same kinds of dirtiness as the real one. The connection to
the real source lives in a local, gitignored config and is never committed.

## Status

- [x] Schema + database initialization
- [x] Synthetic dirty-data generator
- [x] ETL: extract → stage → validate → load
- [x] Data-quality report
- [ ] Streamlit lookup app
- [ ] Google Sheets source adapter (real data, local config only)
- [ ] Sample-request ingestion (Excel) and material↔sample history

## Quickstart

```
python dtf_materials/db.py                    # create db/materials.db from db/schema.sql
python scripts/generate_synthetic_sheet.py    # write data/synthetic/raw_material_inventory.csv
python -m dtf_materials.etl                    # stage + load the sheet into the DB
python -m dtf_materials.quality_report         # print the dirty-data findings
```

## ETL design

See [dtf_materials/etl.py](dtf_materials/etl.py), [dtf_materials/cleaning.py](dtf_materials/cleaning.py),
and [dtf_materials/sources/](dtf_materials/sources/).

**Source is an interface, not a file format.** `InventorySource.rows()` yields
dicts keyed by the sheet's own header names. `CsvInventorySource` is the only
implementation today; a future `SheetsInventorySource` reading from the real
Google Sheet (via a gitignored local config) plugs in without changing
`etl.py`, `cleaning.py`, or the schema at all. This is the portability
requirement: swap the source, not the pipeline.

**Two-pass load: stage first, always.** Every source row lands in
`staging_inventory_raw` as untyped text before any cleaning happens. Cleaning
functions (`dtf_materials/cleaning.py`) are pure — a raw string in, a clean
value or `None` out, never an exception — so a single bad cell can't crash a
106-row load. The data-quality report queries staging directly, so it can
point at a broken cell even on a row that never made it into `materials`/`lots`.

**Full reload, not incremental, on purpose.** Each run truncates
`staging_inventory_raw` and everything derived from it, then rebuilds from
the source. For a sheet this size (hundreds of rows, run on demand) that's
simpler and safer than tracking "what's new since last run," and it makes
every run idempotent — running it twice in a row produces identical output,
which is worth being able to say plainly in an interview. The natural
next step, once volume or history requirements justify it, is incremental
loads keyed on Receiving Date + DTF Lot #; that's a deliberate scope cut,
not an oversight.

**Conflicting Part # handling.** When the same DTF Part # appears with two
different material names, the first-seen row wins as the canonical
`materials` row (Part # is the identity that matters), and every later
conflicting row still gets its lot attached to that material — but the
conflict is counted and the quality report names the exact source rows. The
ETL never silently guesses which name is "right"; a human resolves it.

**Supplier normalization is two-tiered.** Spellings that differ only by case
or whitespace ("Sensapure Flavors" vs "sensapure flavors") are auto-merged
via a normalized key, since that's unambiguous. Spellings that are genuinely
different strings ("NutraSci" vs "Nutra Sci") are *not* auto-merged — merging
fuzzy string matches automatically is how you silently combine two different
companies. Instead the quality report flags likely duplicates (via
`difflib` similarity) for a human to review and alias together.

**Data-quality report output**, run against the synthetic data, reproducibly
finds every kind of dirtiness injected by the generator: rows with no Part #,
Part #s reused across conflicting material names, supplier spellings that
look like the same company, and unparseable/blank prices and dates — each
finding cites the exact source row number.

## Schema design

See [db/schema.sql](db/schema.sql). The core decisions, and why:

**Surrogate key on materials, not DTF Part #.** DTF Part # is the business key and
is enforced `UNIQUE`, but it is nullable: new vendor sample materials exist (with a
vendor code, decoded off the bottle) before the company ever assigns them a Part #
or holds stock. Keying on an internal `material_id` means a sample material becomes
a stocked material by filling in one column — no row migration, and any sample
history already attached to it survives. A `CHECK` guarantees every material has at
least one identifier (Part # or vendor code).

**Lots are a separate one-to-many table.** One material is received many times.
Everything that changes per receiving — DTF Lot #, supplier lot/batch, receiving and
expiration dates, stock levels, location, price — lives on `lots`. The material row
holds only what is true of the material itself (name, supplier, category, allergen).

**Price lives on the lot; the material carries a derived current price.** Suppliers
reprice between receivings, so `price_per_kilo` is recorded per lot, which gives
price history for free. For convenient lookups, the ETL refreshes
`materials.current_price_per_kilo` from the most recent lot.

**Locations: raw text preserved, parsed rows beside it.** The source sheet's
Locations cell is free text and can hold several locations ("A3 / B12"). The lot
keeps the verbatim cell (`locations_raw`) so nothing is lost, and `lot_locations`
holds individually parsed locations where parsing succeeds. Unparseable cells are
flagged by the quality report instead of silently dropped.

**Suppliers normalized with an alias table.** The sheet spells the same supplier
several ways. `suppliers` holds one canonical row per company;
`supplier_aliases` maps each raw spelling to it. The ETL resolves spellings through
the alias table and flags unmapped ones.

**A raw staging table.** `staging_inventory_raw` mirrors the source sheet, all
columns as text, one row per sheet row. The ETL lands data there first, then
transforms into the typed tables. This means the data-quality report can point at
exact source rows — including rows too broken to load at all.

**SQLite, dates as ISO-8601 text.** SQLite because this is a single-writer internal
tool with no server to administer; the schema is plain SQL and ports to Postgres
nearly verbatim if it ever needs concurrent writers. SQLite has no date type, so
dates are stored as `yyyy-mm-dd` text, which sorts and compares correctly.

**Category mirrors the source (`raw`/`flavor`) with a CHECK constraint.** The sheet
only knows two categories; the database stays honest to that rather than inventing
data. Finer categories (colorant, masking agent) can come later as a lookup table.
