# DTF Materials Master

[![CI](https://github.com/dislam7991/materials-master-db/actions/workflows/ci.yml/badge.svg)](https://github.com/dislam7991/materials-master-db/actions/workflows/ci.yml)

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
- [x] Streamlit lookup app
- [x] Google Sheets source adapter (real data, local config only)
- [ ] Sample-request ingestion (Excel) and material↔sample history

## Quickstart

```
python dtf_materials/db.py                    # create db/materials.db from db/schema.sql
python scripts/generate_synthetic_sheet.py    # write data/synthetic/raw_material_inventory.csv
python -m dtf_materials.etl                    # stage + load the sheet into the DB
python -m dtf_materials.quality_report         # print the dirty-data findings
streamlit run app.py                           # launch the lookup app
```

The pipeline is stdlib-only; `pip install -r requirements.txt` is needed only
for the app.

To load from the real Google Sheet instead of the synthetic CSV, set up
`config.local.toml` (copy `config.example.toml` — see that file for the
shape), `pip install -r requirements-sheets.txt`, then:

```
python -m dtf_materials.etl --source sheets
```

The service account backing this must only ever be shared on the sheet as
**Viewer** — this project has no code path that writes back to it.

## The lookup app

See [app.py](app.py) (UI only) and [dtf_materials/queries.py](dtf_materials/queries.py)
(all data access). They are separate so the queries can be tested from a REPL
or reused by a future CLI/API without importing Streamlit.

Two tabs: look up a material by Part # or name, and look up what is stored at
a location (a full code like `6L-27-D`, or an aisle prefix like `6L`).

**Search is parameterized and wildcard-escaped.** User input never reaches SQL
as a string fragment, and `%`/`_` in a search term match literally — otherwise
searching for a material named "Whey Protein Isolate 90%" would silently match
everything. Results rank exact and prefix Part # matches first, because
someone typing a part number wants that material, not the alphabetically-first
name containing the string.

**Stock figures are reported honestly, which took three passes to get right.**
The source records one quantity per lot plus the location(s) that lot
occupies, and never how the quantity splits between them. So:

* A lot spanning several locations contributes its *full* quantity to each of
  its rows. The app flags this and tells you not to sum the rows — dividing
  the quantity evenly would be fabricating a number the company doesn't have.
* Stock in lots with a blank Locations cell appears in the total but in no
  row. The app reports that quantity explicitly rather than letting the two
  numbers disagree silently — material the company owns and cannot locate
  from the record is exactly what this tool exists to surface.
* A material with nothing in stock shows its last known location, clearly
  labelled. "No stock on hand" is not the same as "no idea where this lives".

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

**Full reload of lots, upsert of materials.** Each run truncates staging,
`lots`, and `lot_locations` and rebuilds them from the source; `materials`
and `suppliers` are upserted on their business keys and never deleted. The
distinction matters: `material_id` is a stable identity that
`sample_materials` references, so delete-and-reinsert would reassign every id
on each run and merely re-sorting the source sheet would silently repoint
sample history at the wrong materials. Lots can be rebuilt freely because
nothing outside `lot_locations` references `lot_id`. Consequence: a material
that disappears from the sheet stays in the database (correct for a master
table — it keeps history) and is counted as `stale_materials` in the run stats.

**The load is one atomic transaction.** The run starts by clearing lots and
staging, so a failure partway through — a malformed row, or a dropped
connection mid-fetch once the source is the live Google Sheet — would
otherwise leave the database emptied and not repopulated. Everything between
reset and price-refresh commits together or rolls back entirely.

For a sheet this size (hundreds of rows, run on demand) reloading beats
tracking "what's new since last run," and it makes every run idempotent. The
natural next step, once volume justifies it, is incremental loads keyed on
Receiving Date + DTF Lot #; that's a deliberate scope cut, not an oversight.

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
[docs/quality_report_sample.md](docs/quality_report_sample.md) is one such
run, written by `python -m dtf_materials.quality_report --out PATH`, which
saves the same findings the command prints as a Markdown file you can link
or hand to someone who will never run a Python command.

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

**Locations: raw text preserved, parsed rows beside it.** A location is a
hyphen-joined alphanumeric code (`6L-27-D`), and one cell can hold several,
usually comma-separated (`6R-09-E, 6R-10-C, 6R-13-C`). Some locations are
named words instead (`cooler`), and the cell can be blank. The lot keeps the
verbatim cell (`locations_raw`) so nothing is lost; `lot_locations` holds the
parsed, upper-cased individual codes, which is what makes "what else is in
rack 6L?" a simple query.

Splitting on whitespace is conditional on purpose: `6R-09-E 6R-10-C` is two
locations, but `back cooler` is one. A token is only split on whitespace when
every resulting piece matches the code pattern; otherwise it is kept intact
as free text rather than guessed at. The quality report then flags parsed
locations that match neither the code format nor a known named location —
and because it counts repeats, a value appearing many times reads as a real
named location to whitelist, while a one-off (`1L-28-`) reads as a typo.

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
