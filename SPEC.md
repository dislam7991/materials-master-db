# Materials Master — Project Spec & Plan

Single source of truth for what this project is, what is done, what remains,
and what is deliberately out of scope. Work through the checklists top to
bottom. **Every checked box must correspond to a real, tested change in the
same commit that checks it.** One task per commit is ideal.

---

## 1. Goal

A supplement contract manufacturer with no ERP keeps inventory in a shared
Google Sheet and sample requests in loose Excel files. Nothing talks to
anything; everything is manual copy-paste.

This project is the minimum system that fixes that:

1. **A SQLite materials master database** — one clean, typed record per
   material, keyed on DTF Part #, with lots (receivings) underneath it.
2. **An ETL pipeline** that pulls the inventory sheet, cleans it, validates
   it, and loads it — source-swappable (CSV today, Google Sheets API at the
   end).
3. **A data-quality report** that names every dirty row in the source, as
   evidence of the problem being solved.
4. **A Streamlit lookup app**: type a part # or name → location, price,
   supplier, lot history.
5. **Sample-request ingestion**: parse the Excel sample sheets, link
   materials ↔ samples, so material usage history finally exists in one place.

It doubles as a public portfolio project, so the repo contains **synthetic
data only**. The real-sheet connection lives in a gitignored local config and
is never committed.

## 2. Principles (read before adding anything)

- **Simplicity over complexity, always.** The minimum steps to a working
  tool. No feature earns its place by being impressive; it earns it by
  answering a question someone at the company actually asks.
- **Never fabricate data.** If the source doesn't record something (e.g. how
  one lot's quantity splits across three locations), the tool says so plainly
  instead of inventing a plausible number.
- **Flag, don't guess.** Ambiguity (conflicting part #s, similar supplier
  spellings) goes to the quality report for a human; the ETL never
  auto-resolves it.
- **Preserve the raw.** Every source row lands verbatim in staging before any
  cleaning. Parsing failures are countable, not silent.
- **Synthetic-first.** Everything is built and tested against generated fake
  data shaped exactly like the real sheet, including its dirtiness. Real data
  connects last, via local config.
- **Idempotent, atomic loads.** Re-running the ETL is always safe; a crash
  mid-run leaves the DB untouched.

## 3. Architecture (as built)

```
source (CSV now / Google Sheet later)
   │  InventorySource interface — swap the source, not the pipeline
   ▼
staging_inventory_raw          all text, verbatim, one row per sheet row
   │  cleaning.py — pure functions, never raise, None on failure
   ▼
materials (upsert, stable ids) ◄── suppliers + supplier_aliases
lots (full reload)             ◄── lot_locations (parsed codes)
   │
   ├── quality_report.py       reads staging → names every dirty row
   └── app.py + queries.py     Streamlit lookup (UI and data access separated)
```

Key decisions and their one-line justifications:

| Decision | Why |
|---|---|
| SQLite, not Postgres | Single-writer internal tool; zero admin; one file; schema ports to Postgres nearly verbatim if concurrency is ever needed. |
| Surrogate `material_id`, `dtf_part_num` UNIQUE-nullable | Vendor samples exist before they get a Part #; promoting one is a column update, not a row migration. |
| Lots in a child table | One material, many receivings — textbook one-to-many. |
| Price on the lot + derived current price on material | History for free; suppliers reprice between receivings. |
| Materials upserted, never deleted | `material_id` is referenced by sample history; delete-and-reinsert reassigns ids and silently corrupts links. |
| Lots fully reloaded each run | Nothing external references `lot_id`; reload is idempotent and simpler than change-tracking at this volume. |
| One atomic transaction per run | A crash mid-load must not leave a wiped DB. |
| Raw `locations_raw` + parsed `lot_locations` | Cell is free text, possibly several codes; keep truth, query the parse. |
| Supplier aliases, fuzzy matches only *flagged* | Auto-merging similar strings is how two real companies get combined. |
| Dates as ISO text | SQLite has no date type; ISO sorts correctly. |
| ETL is stdlib-only | Fewer deps, trivially portable; pandas/streamlit needed only by the app. |

## 4. Done (commits on `master`)

- [x] Schema + idempotent DB init — `03c80ac`
- [x] Synthetic dirty-sheet generator, seeded, real column structure —
      `03c80ac`, realistic location codes `945268a`
- [x] ETL: source adapter interface, CSV source, staging, cleaning, load —
      `19b9079`
- [x] Data-quality report citing exact source rows — `19b9079`
- [x] Audit fixes: stable material ids, atomic loads, deterministic report,
      decimal-separator price bug — `dfd0190`
- [x] Location parsing for real code format (`6L-27-D`), conditional
      whitespace split, nonstandard-location check — `945268a`
- [x] Streamlit app: material lookup, location lookup, honest stock
      reporting (split lots flagged, unlocated stock surfaced, last-known
      location fallback) — `1a7d499`
- [x] This spec — the commit that adds it

## 5. Remaining work

Ordered. Each task is one sitting, one commit, and states its definition of
done (DoD). Do them in order; later tasks assume earlier ones.

### Phase A — Harden what exists (makes daily automation safe)

- [x] **A1. Pytest suite for `cleaning.py`.** Move the `__main__`
      self-checks into `tests/test_cleaning.py`, keep every existing case,
      add the known edge cases (dates: `1/5/25`, `Jan 5, 2026`; prices:
      `(15.00)`, `12.50 USD`; locations: trailing separators).
      *Why: the self-checks already exist; making them a real suite is the
      cheapest possible CI foundation.*
      DoD: `python -m pytest` passes; self-check block in `cleaning.py`
      replaced by a pointer to the tests.
- [ ] **A2. Pytest for the ETL invariants.** Encode the three audit probes
      as tests: (1) material ids stable under a re-sorted source, (2) crash
      mid-load leaves prior data intact, (3) two consecutive runs produce
      identical tables. Use a temp DB and the synthetic CSV.
      *Why: these are the bugs that actually happened; tests stop them
      regressing.*
      DoD: `python -m pytest` covers and passes all three.
- [x] **A3. GitHub Actions CI.** One workflow: on push, install nothing (ETL
      is stdlib-only), run the generator, the ETL, the quality report, and
      pytest. Badge in README.
      *Why: with a bot committing daily, an automated "did it break"
      check is not optional. One job, no matrix — minimum useful CI.*
      DoD: green run on `master`; badge renders.
- [ ] **A4. Quality report to file.** `--out report.md` flag writing the
      findings as Markdown (same content as stdout).
      *Why: a linkable artifact for the README and for showing the mess at
      work; trivial scope.*
      DoD: flag works; sample output committed as `docs/quality_report_sample.md`.

### Phase B — Real inventory source (the point of the project)

- [ ] **B1. Local config loader.** `config.local.toml` (already gitignored)
      read via stdlib `tomllib`: sheet ID, tab name, service-account key
      path. Committed `config.example.toml` documents the shape. No secrets
      in the repo, ever.
      DoD: loader returns typed config; missing file → clear error naming
      the example file.
- [ ] **B2. `SheetsInventorySource`.** Implements `InventorySource.rows()`
      via the Sheets API (service account). Asserts the header row matches
      `EXPECTED_HEADERS`. Nothing downstream changes.
      *Why service account over OAuth: no browser flow, one JSON key shared
      read-only with the sheet — simplest thing that works unattended.*
      DoD: `python -m dtf_materials.etl --source sheets` loads the real tab
      on a configured machine; CSV path still the default and CI still
      synthetic-only.
- [ ] **B3. First real run + real quality report.** Run against the actual
      sheet at work. Save the (scrubbed) findings summary — counts only, no
      material names or prices — to `docs/real_run_notes.md`.
      *Why: the before/after story ("report found N conflicting part #s in
      the live sheet") is the whole pitch, at work and in interviews.*
      DoD: notes file committed; no real data in the repo.

### Phase C — Sample requests (closes the original gap)

- [ ] **C1. Get 2–3 real sample-request Excel files, map their layout.**
      Document the cell positions/ranges that matter in
      `docs/sample_sheet_layout.md`. No code.
      DoD: layout doc committed.
- [ ] **C2. Synthetic sample-request generator.** Same philosophy as the
      inventory generator: fake `.xlsx` files matching the real layout,
      including the dirt (part #s that don't exist in inventory, blank
      cells).
      DoD: generator writes N fake files; seeded.
- [ ] **C3. Sample ingestion.** Parse the files (openpyxl), fill `samples` +
      `sample_materials`, matching materials by Part #. Unmatched part #s
      become quality findings, never guessed rows.
      DoD: ETL run loads synthetic samples; quality report gains an
      "unmatched sample materials" section; tests cover the match/no-match
      paths.
- [ ] **C4. Sample history in the app.** On a material's page: which samples
      used it. New "Samples" tab: look up a sample, see its materials.
      Queries in `queries.py`, UI in `app.py`, same separation as now.
      DoD: both directions visible in the app against synthetic data.

### Phase D — Portfolio polish (last, small)

- [ ] **D1. README top section rewrite**: 3-sentence problem statement, a
      screenshot of the app, the quality-report sample, quickstart. The
      design-decision prose already written stays.
      DoD: a stranger can understand and run the project from the README
      alone.
- [ ] **D2. Repo hygiene pass**: LICENSE (MIT), `.gitattributes` for line
      endings (kills the CRLF warning noise), short CONTRIBUTING note that
      this is a personal portfolio project.
      DoD: files present; `git status` clean on both machines.

## 6. Explicitly out of scope (do not build these)

Listed so the daily automation never "helpfully" adds them:

- **No Postgres / MySQL migration.** SQLite is correct at this scale.
  Revisit only if there are concurrent writers, which there aren't.
- **No auth, no hosting, no Docker.** The app runs locally on demand.
- **No write-back to the Google Sheet.** One-way ingestion only; the sheet
  stays the operational system of record until the company decides otherwise.
- **No incremental/CDC loading.** Full reload is idempotent and fast at
  hundreds of rows. Revisit at ~50k rows, not before.
- **No auto-merge of fuzzy supplier matches** and no auto-resolution of
  part-# conflicts. Humans resolve; the report flags.
- **No invented stock splits** across locations. The source doesn't record
  it; neither do we.
- **No ORM, no web framework beyond Streamlit, no dashboarding suite.**
- **No scheduled ETL daemon.** Run on demand; a cron line is one sentence in
  the README if ever wanted.

## 7. Working agreement for the daily automation

1. Pick the **first unchecked box**, top to bottom. One box per day is fine;
   never more than one phase-B/C box per day.
2. A box may only be checked in a commit that contains the change satisfying
   its DoD, with tests passing locally (`python -m pytest`) and in CI.
3. If a task turns out to need splitting, split it into sub-boxes in this
   file in the same commit — don't half-check.
4. Anything tempting that appears mid-task and isn't in this file goes to a
   `## Backlog` section at the bottom of this file, not into code.
5. Never commit: `db/*.db`, `data/real/`, `config.local.toml`, service
   account keys, or any real material name, price, supplier, or client.

## Backlog

Things that came up mid-task and are deliberately not built yet (rule 4).

- **Should `cleaning.py` learn more formats?** A1 added tests for the four
  listed edge inputs; all four currently return None and so land in the
  quality report rather than being parsed: `1/5/25` (two-digit year),
  `Jan 5, 2026` (comma after the day), `12.50 USD` (trailing currency code),
  `(15.00)` (accounting negative). Teaching the parser the first three is a
  few lines each; `(15.00)` should probably stay unparsed, since neither
  `15.00` nor `-15.00` is a defensible price to invent. Left alone because
  widening what the parser accepts is a behavior change, not A1's DoD.
  Decide it with evidence from **B3** — the first real run tells us how
  often these actually occur — not from guesswork now.

- ~~**The committed synthetic CSV is stale.**~~ Resolved in A3: the file was
  regenerated from the current `scripts/generate_synthetic_sheet.py`, so a
  local generator run no longer produces a spurious diff.
