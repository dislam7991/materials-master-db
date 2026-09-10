# DTF Materials Master

[![CI](https://github.com/dislam7991/materials-master-db/actions/workflows/ci.yml/badge.svg)](https://github.com/dislam7991/materials-master-db/actions/workflows/ci.yml)

## The problem

A supplement contract manufacturer with no ERP keeps every raw material in one
shared Google Sheet, and the R&D lab's flavor samples in another. Answering
"where is this material, what did it cost, who supplies it?" means scrolling a
spreadsheet that carries the same part number on two different materials, five
spellings of one supplier, and prices that aren't numbers.

This reads those sheets into a queryable SQLite database with a search app on
top, and prints a report naming every dirty row instead of papering over it.

![The material lookup tab: a material's price, supplier, category and allergen, the locations holding it, its lot history, and its price per kilo over time](docs/app_screenshot.png)

*Running on the synthetic sheet in this repo — no real material names, prices
or suppliers anywhere in it. The warning under the location table is the design
principle in miniature: the sheet records one quantity per lot and never how it
splits across locations, so the app says so rather than dividing the number up.*

## What it is

A small internal data platform. Two separate sources feed it:

* **the warehouse raw-material inventory sheet** → `materials` / `lots` /
  `lot_locations`, the materials master proper;
* **the R&D lab's flavor sample catalog**, a second Google Sheet on a different
  account → the `lab_samples` table and its own search tab.

Plus a data-quality report naming every dirty row in the inventory source.

**No real data lives in this repository.** `scripts/generate_synthetic_sheet.py`
produces a fake inventory sheet with the same columns and the same kinds of
dirtiness as the real one, and that is what the tests and CI run against. The
real connections live in a gitignored `config.local.toml`.

## Quickstart

Five lines, nothing to configure — the generator writes the fake sheet, so the
pipeline has data from a cold start.

```
python dtf_materials/db.py                    # create db/materials.db from db/schema.sql
python scripts/generate_synthetic_sheet.py    # write data/synthetic/raw_material_inventory.csv
python -m dtf_materials.etl                    # stage + load the sheet into the DB
python -m dtf_materials.quality_report         # print the dirty-data findings
streamlit run app.py                           # launch the lookup app
```

Python 3.11+. The pipeline is stdlib-only; `pip install -r requirements.txt` is
needed only for the app. `python -m pytest` runs the tests (`pip install -r
requirements-dev.txt`).

Connecting the real sheets is [its own section](#connecting-the-real-sheets)
and needs credentials that aren't in this repository.

## Status

Full plan, definitions of done, and scope reasoning in [SPEC.md](SPEC.md).

**Built:** schema and idempotent DB init; seeded synthetic dirty-data
generator; the ETL (extract → stage → validate → load, atomic and idempotent);
the quality report with `--out` to Markdown; the Streamlit app; pytest for
`cleaning.py`, `queries.py` and the three ETL load invariants, with CI running
the whole pipeline on every push; the real inventory source
(`SheetsInventorySource` + config loader, confirmed against the live company
sheet); the lab sample catalog (sheet mapped, `lab_samples` table, loader,
search tab); and a Windows one-click launcher.

**Remaining:** D2 — repo hygiene: LICENSE (MIT), `.gitattributes`, a
CONTRIBUTING note.

**Parked — Phase C (sample-request ingestion).** Parsing the loose Excel
sample-request files into `samples` / `sample_materials`. Goal 5 of the
original plan; nobody is asking for it, so the tasks stay in SPEC.md in order
rather than being deleted. Different from Phase E: sample *requests* from
clients, not the lab's catalog of vendor samples.

**Known gaps, deliberately not built** (reasoning in SPEC.md):

- No combined warehouse + lab location view. The matching strategy is decided
  and documented; nothing uses it yet.
- No lab-specific quality report. The loader flags duplicate sample codes on
  stdout; there's no `quality_report.py` equivalent for the lab sheet.
- No parser for the lab's location codes (`A-2-1`-style, plus plain-English
  ones), and declaration tags whose order varies (`Natural, WONF` vs
  `WONF, Natural`) still read as two categories.

## Connecting the real sheets

Copy `config.example.toml` to `config.local.toml` and fill it in, then
`pip install -r requirements-sheets.txt`:

```
python -m dtf_materials.etl --source sheets
```

The service account must only ever be shared on the sheet as **Viewer** — this
project has no code path that writes back.

The lab catalog is a second sheet (see
[layout doc](docs/flavor_sample_sheet_layout.md)) — add a `[lab_sheet]` section
to the same config, then:

```
python -m dtf_materials.lab_samples
```

Loads into `lab_samples` and flags duplicate sample codes. Same Viewer-only rule.

### Windows: one-click launch

[`run_app.bat`](run_app.bat) is a double-click launcher for handing this to
someone who won't run commands. It behaves differently depending on whether
`config.local.toml` is present, because two very different people run it:

| | **Operator** (has config + key) | **Viewer** (a colleague) |
|---|---|---|
| Installs | app deps + `requirements-sheets.txt` | app deps only |
| Database | refreshed from both sheets each launch | left exactly as received |
| Needs Google access | yes | **no** |

First run creates the virtualenv and installs; after that it activates and
launches. A failed install deletes its own half-built environment, so the next
double-click retries rather than skipping past it.

Python 3.11+ from python.org — tick "Add python.exe to PATH". The launcher
verifies the version rather than just that some `python` answers, which catches
the Microsoft Store's placeholder `python.exe`.

**As the operator**, each launch re-runs both loaders before starting the app.
This is a re-run, not a rebuild — the database file is never deleted, because
the ETL is already idempotent and deleting it would discard the stable
`material_id`s it works to preserve. If a refresh fails (no network, sheet not
shared), the app still launches on existing data rather than refusing to start.
Skip it with `run_app.bat --no-refresh`.

### Handing the app to a colleague

**Do not copy the service account key.** It's a credential, not a setting:
whoever holds it can read both sheets as that identity, from any machine, until
it's revoked. Sheet IDs alone grant nothing, so "just paste in the IDs" doesn't
work either.

They don't need Google access. The app only reads `db/materials.db`; `gspread`
exists solely for the loaders that *fill* it, which is why
`requirements-sheets.txt` is separate. So:

1. Run the app here once, so the database is current.
2. Send them the repo folder **plus `db/materials.db`** (~1.4 MB). It's
   gitignored, so it won't arrive via `git clone`.
3. They double-click `run_app.bat`. Seeing no config, it installs app deps
   only, leaves the database alone, and launches.

Two caveats: the database is a **snapshot** and goes stale until you send a new
one, and it contains real material names, prices and suppliers — fine for a
colleague who already has sheet access, but it's a data handoff, not just a
program.

A shared database on a synced drive would replace step 2 with one central
refresh. See the SPEC.md Backlog for why that's worth doing only once the ETL
points at the live sheet.

## The lookup app

[app.py](app.py) is UI only; [dtf_materials/queries.py](dtf_materials/queries.py)
is all data access. Separate so the queries can be tested from a REPL or reused
by a future CLI without importing Streamlit.

Four tabs: look up a material by Part # or name; look up what's at a location
(a full code like `6L-27-D`, or an aisle prefix like `6L`); browse everything in
a sortable table; search the lab's flavor samples by vendor, flavor name, sample
code or Part #.

**Search is parameterized and wildcard-escaped.** User input never reaches SQL
as a string fragment, and `%`/`_` match literally — otherwise searching for
"Whey Protein Isolate 90%" would silently match everything. Exact and prefix
Part # matches rank first, because someone typing a part number wants that
material, not the alphabetically-first name containing the string.

**Stock figures are reported honestly**, which took three passes to get right.
The source records one quantity per lot plus the location(s) that lot occupies,
never how the quantity splits between them. So:

* A lot spanning several locations contributes its *full* quantity to each row.
  The app flags this and tells you not to sum the rows — dividing the quantity
  evenly would be fabricating a number the company doesn't have.
* Stock in lots with a blank Locations cell appears in the total but in no row.
  The app reports that quantity explicitly rather than letting two numbers
  disagree silently. Material the company owns and cannot locate is exactly
  what this tool exists to surface.
* A material with nothing in stock shows its last known location, labelled.
  "No stock on hand" is not "no idea where this lives".

## ETL design

See [etl.py](dtf_materials/etl.py), [cleaning.py](dtf_materials/cleaning.py),
[sources/](dtf_materials/sources/).

**Source is an interface, not a file format.** `InventorySource.rows()` yields
dicts keyed by the sheet's own header names. `CsvInventorySource` reads the
synthetic sheet; `SheetsInventorySource` reads the real one. Adding the second
changed nothing in `etl.py`, `cleaning.py` or the schema — swap the source, not
the pipeline.

`gspread`/`google-auth` are imported only inside the `--source sheets` branch,
so the default pipeline, the app and CI never need them.

**Real header rows have cosmetic whitespace; the reader normalizes it.** The
live sheet spelled the same columns differently from the synthetic one —
`Lot / Batch` vs `Lot/Batch`, trailing spaces. `normalize_header_name` collapses
whitespace *around punctuation only*, never inside words, so it can't make two
genuinely different columns collide. A column still missing after normalization
is a hard error: rows are matched by header name, not position, so a
silently-renamed column would otherwise load as all-blanks.

**Two-pass load: stage first, always.** Every source row lands in
`staging_inventory_raw` as untyped text before any cleaning. Cleaning functions
are pure — raw string in, clean value or `None` out, never an exception — so one
bad cell can't crash a 106-row load. The quality report queries staging
directly, so it can point at a broken cell even on a row that never made it into
`materials`/`lots`.

**Full reload of lots, upsert of materials.** Each run truncates staging, `lots`
and `lot_locations` and rebuilds them; `materials` and `suppliers` are upserted
on their business keys and never deleted. `material_id` is a stable identity
that `sample_materials` references, so delete-and-reinsert would reassign every
id each run, and merely re-sorting the source sheet would silently repoint
sample history at the wrong materials. Lots rebuild freely because nothing
outside `lot_locations` references `lot_id`. Consequence: a material that
disappears from the sheet stays in the database — correct for a master table —
and is counted as `stale_materials` in the run stats.

**The load is one atomic transaction.** The run starts by clearing lots and
staging, so a failure partway through — a malformed row, or a dropped connection
mid-fetch against the live sheet — would otherwise leave the database emptied
and not repopulated. Everything between reset and price-refresh commits together
or rolls back entirely.

At this size (hundreds of rows, run on demand) reloading beats tracking what's
new, and it makes every run idempotent. The next step once volume justifies it
is incremental loads keyed on Receiving Date + DTF Lot # — a deliberate scope
cut, not an oversight.

**Conflicting Part # handling.** When one DTF Part # appears with two different
material names, the first-seen row wins as the canonical `materials` row (Part #
is the identity that matters) and later conflicting rows still get their lots
attached — but the conflict is counted and the report names the exact source
rows. The ETL never guesses which name is right; a human resolves it.

**Supplier normalization is two-tiered.** Spellings differing only by case or
whitespace ("Sensapure Flavors" vs "sensapure flavors") are auto-merged via a
normalized key, since that's unambiguous. Genuinely different strings
("NutraSci" vs "Nutra Sci") are *not* — auto-merging fuzzy matches is how you
silently combine two different companies. The report flags likely duplicates
(via `difflib`) for a human to alias together.

**The quality report** reproducibly finds every kind of dirtiness the generator
injects: missing Part #s, Part #s reused across conflicting names, supplier
spellings that look like one company, unparseable and blank prices and dates —
each citing the exact source row.
[docs/quality_report_sample.md](docs/quality_report_sample.md) is one such run,
written by `--out PATH` for handing to someone who'll never run a Python command.

## The lab sample catalog

See [lab_samples.py](dtf_materials/lab_samples.py) and the
[layout doc](docs/flavor_sample_sheet_layout.md).

A second Google Sheet, on a personal account rather than the company one,
listing the flavor samples physically sitting in the R&D lab. Separate source,
separate loader, separate table, its own tab — deliberately independent of
warehouse inventory, because the two answer different questions and most lab
samples have no DTF Part # at all.

**One service account, two sheets.** A service account is an identity and
sharing is per file, so the same key reads both sheets once each is shared with
its email as Viewer. The config grows a `[lab_sheet]` section rather than a
second credential, and that section's key path falls back to `[sheets]` when
omitted.

**Mapping the sheet came before loading it.** The first export wasn't a table:
the header sat on row 3 under a title banner, and an unrelated two-column "Taste
and Aroma Lexicon" was parked in columns X–Y sharing row numbers with the first
13 real records, so any reader taking whole rows would have stapled reference
prose onto inventory. Both were fixed in the sheet itself rather than worked
around in code — cleaning up a source you control beats teaching the parser to
tolerate it.

**Two identifier questions, kept separate.** What identifies a row *within*
`lab_samples` is a surrogate id, because Sample Code collides. How a lab sample
*links to* a warehouse material is Part # first, falling back to Sample Code
appearing inside the warehouse material name. Never fuzzy name matching — two
vendors' "Vanilla" must not be silently merged. The link is designed; the
combined view that would use it isn't built.

## Schema design

See [db/schema.sql](db/schema.sql).

**Surrogate key on materials, not DTF Part #.** Part # is the business key and
is `UNIQUE`, but nullable: new vendor sample materials exist (with a vendor code
decoded off the bottle) before the company assigns a Part # or holds stock.
Keying on an internal `material_id` means a sample material becomes a stocked
material by filling in one column — no row migration, and any sample history
attached to it survives. A `CHECK` guarantees every material has at least one
identifier.

**Lots are a separate one-to-many table.** One material is received many times.
Everything that changes per receiving — lot #, dates, stock, location, price —
lives on `lots`. The material row holds only what's true of the material itself.

**Price lives on the lot; the material carries a derived current price.**
Suppliers reprice between receivings, so `price_per_kilo` is per lot, which
gives price history for free. The ETL refreshes
`materials.current_price_per_kilo` from the most recent lot for convenient
lookups.

**Locations: raw text preserved, parsed rows beside it.** A location is a
hyphen-joined code (`6L-27-D`), one cell can hold several
(`6R-09-E, 6R-10-C`), some are named words (`cooler`), and the cell can be
blank. The lot keeps the verbatim cell (`locations_raw`) so nothing is lost;
`lot_locations` holds the parsed individual codes, which is what makes "what
else is in rack 6L?" a simple query.

Splitting on whitespace is conditional on purpose: `6R-09-E 6R-10-C` is two
locations, but `back cooler` is one. A token splits only when every resulting
piece matches the code pattern; otherwise it's kept intact rather than guessed
at. The report then flags parsed locations matching neither the code format nor
a known named location — and because it counts repeats, a value appearing many
times reads as a real named location to whitelist, while a one-off (`1L-28-`)
reads as a typo.

**Suppliers normalized with an alias table.** `suppliers` holds one canonical
row per company; `supplier_aliases` maps each raw spelling to it. The ETL
resolves through the alias table and flags unmapped spellings.

**A raw staging table.** `staging_inventory_raw` mirrors the source sheet, all
columns text, one row per sheet row. This is what lets the quality report cite
exact source rows — including rows too broken to load at all.

**SQLite, dates as ISO-8601 text.** SQLite because this is a single-writer
internal tool with no server to administer; the schema is plain SQL and ports to
Postgres nearly verbatim if it ever needs concurrent writers. SQLite has no date
type, so dates are `yyyy-mm-dd` text, which sorts and compares correctly.

**Category mirrors the source (`raw`/`flavor`) with a CHECK.** The sheet knows
two categories; the database stays honest to that rather than inventing data.
Finer categories can come later as a lookup table.

**`lab_samples` is flat, and not a foreign key into `materials`.** One table,
not the staging + typed pair, because there's no one-to-many to model — each
source row already *is* one physical sample on a shelf. It keeps `source_row`
so a future lab quality report can cite exact sheet rows. `dtf_part_num` is
nullable and deliberately not a foreign key: most lab samples have no Part # yet,
and even once one is filled in, linking a sample to a warehouse material is a
display-time join, not a constraint this table should enforce. `lab_sample_id`
is a surrogate because Sample Code is not unique — it collided 22 ways in the
first export, and the loader flags those rather than picking a winner.
