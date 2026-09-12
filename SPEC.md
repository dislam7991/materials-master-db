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
   materials ↔ samples, so material usage history finally exists in one
   place. *Parked — see the Parked section; nobody is asking for this yet.*

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
- [x] **A2. Pytest for the ETL invariants.** Encode the three audit probes
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
- [x] **A4. Quality report to file.** `--out report.md` flag writing the
      findings as Markdown (same content as stdout).
      *Why: a linkable artifact for the README and for showing the mess at
      work; trivial scope.*
      DoD: flag works; sample output committed as `docs/quality_report_sample.md`.
- [x] **A5. Pytest for `queries.py`.** The read side had no tests at all,
      which put every "never fabricate data" promise in untested SQL: the
      split-lot flag, the unlocated-stock figure, the last-known-location
      fallback, NULL-date ordering, and the LIKE-wildcard escaping. Covered
      in `tests/test_queries.py` by hand-written source rows run through the
      real `etl.run()` — one small sheet where each material isolates one
      behaviour — so no fixture can describe a database state the ETL could
      not actually produce.
      *Why: A1 and A2 hardened the write side; a refactor that made
      `total_stock` sum location rows would have invented inventory that does
      not exist and left the suite green.*
      DoD: `python -m pytest` covers and passes all of them; verified to bite
      by mutating `total_stock` to sum `lot_locations` and confirming the
      split-lot and reconciliation tests fail.

### Phase B — Real inventory source (the point of the project)

- [x] **B1. Local config loader.** `config.local.toml` (already gitignored)
      read via stdlib `tomllib`: sheet ID, tab name, service-account key
      path. Committed `config.example.toml` documents the shape. No secrets
      in the repo, ever.
      DoD: loader returns typed config; missing file → clear error naming
      the example file.
- [x] **B2. `SheetsInventorySource`.** Implements `InventorySource.rows()`
      via the Sheets API (service account). Asserts the header row matches
      `EXPECTED_HEADERS`. Nothing downstream changes.
      *Why service account over OAuth: no browser flow, one JSON key shared
      read-only with the sheet — simplest thing that works unattended.*
      DoD: `python -m dtf_materials.etl --source sheets` loads the real tab
      on a configured machine; CSV path still the default and CI still
      synthetic-only.
- [x] **B3. First real run against the live sheet.** Confirmed working on a
      configured machine: `--source sheets` pulls a copy of the work
      inventory sheet, the load succeeds, and the app runs on the real
      materials.
      *Scope cut on the way through: this task originally also demanded a
      scrubbed findings summary in `docs/real_run_notes.md`. That file was
      only ever for a portfolio before/after story, and a working tool
      loaded with real data is stronger evidence than a count of dirty
      rows. Dropped rather than deferred.*
      DoD: real run confirmed on a configured machine; no real data in the
      repo.

Phase C (sample requests) is **parked** — see the Parked section near the
bottom of this file. Read section 5 top to bottom skipping it.

### Phase E — Flavor sample database (second live sheet)

A separate Google Sheet on a personal (non-company) account, shared with
other R&D staff: a catalog of flavor sample materials and their info. Not
the same thing as Phase C's sample *requests* — this is closer to a second
inventory source, and unlike the main sheet, most rows won't have a DTF
Part # yet (samples-in-waiting, not yet adopted into real inventory). The
schema already has a place for exactly this: `materials.is_sample_only` and
a nullable `dtf_part_num` exist for this case specifically.

Real, unresolved prep work sits in front of any code here — the sheet's
data needs to be understood and cleaned up before it can be loaded, the
same way the main sheet was.

- [x] **E1. Get read access and map the sheet's real structure.** Documented
      in `docs/flavor_sample_sheet_layout.md`, combining a detailed
      structural/statistical pass against the sheet's original export (column
      fill rates, sample-code-shape distribution, the price-unit and
      declaration-order gaps `cleaning.py` doesn't cover) with later real
      exports taken after the user cleaned the sheet up (lexicon table moved
      off, blank/preamble rows removed, a Part # column added).
      DoD: layout doc committed — done.
- [x] **E2. Decide the identifier strategy and cleanup plan.** Documented in
      the same file: DTF Part # first, falling back to Sample Code found as
      a substring of the warehouse Material Name. A lab sample matching
      neither is out of scope for the (not-yet-built) combined-location
      view, not an error. No fuzzy name-matching, ever — two vendors'
      "Vanilla" must never be silently merged.
      DoD: design note committed — done.
- [x] **E3. Build the source + load path.** `lab_samples` table
      (`db/schema.sql`) + `dtf_materials/lab_samples.py`, loading via the
      `[lab_sheet]` config section with the same header-whitespace
      normalization proven necessary for the main sheet. Flags duplicate
      sample codes rather than silently picking one.
      DoD: `python -m dtf_materials.lab_samples` loads the real tab on a
      configured machine — tested against real exported data in this
      session; a live run against the actual sheet is still needed on a
      configured machine to fully confirm (same caveat B2 had before its
      first real run).
- [x] **E4. Lab Samples search tab in the app.** `search_lab_samples` /
      `get_lab_sample` in `queries.py`, and a new "Lab Samples" tab
      mirroring "Material lookup"'s live-search UX — search by vendor,
      flavor name, sample code, or Part #, then a detail view showing every
      field (including the sensory ones that are mostly blank right now —
      shown anyway, since blank means "not recorded yet," not broken).
      DoD: verified in a live browser run against real sample data.
- [x] **E5. Combined lookup tab (warehouse + lab).** The two catalogs live in
      one database but still answer separately, so "do we have this — in the
      warehouse, in the lab, or both?" is two searches in two tabs, and you
      have to already know which one to try. A new **Warehouse + Lab** tab
      answers it from a cold start: one search box over both tables, results
      labelled `Warehouse` / `Lab` / `Both`, and a summary of whichever sides
      exist.
      *Why: it is the reason both sheets are in one database. Until it
      exists, the lab table is a second silo that merely happens to share a
      file.*

      **The two existing lookup tabs are not touched.** They stay
      single-source and keep the deep detail — lot history, price chart,
      stock reconciliation on one side; vendor and sensory fields on the
      other. The new tab is the way in, not a replacement: it shows a summary
      of each side and says which tab holds the rest. Putting cross-source
      blocks inside the single-source tabs was considered and rejected — it
      cannot answer the question from a cold start (a lab-only sample has no
      material row, so searching Material lookup for it finds nothing), and
      it would put the match rule in two places.

      **Match rule** (already decided in E2, `docs/flavor_sample_sheet_layout.md`
      §2b — implement it, don't redesign it). A lab sample links to a
      material by, in order:
      1. `lab_samples.dtf_part_num` = `materials.dtf_part_num`, both
         non-blank.
      2. `lab_samples.sample_code` found as a case-insensitive substring of
         `materials.material_name` (the real pattern: Sensapure "Mango
         7182011" contains code `7182011`).

      A linked pair collapses into one `Both` result. Everything else stands
      alone as `Warehouse` or `Lab` — unlinked is the normal case, not an
      error. **No fuzzy name matching, ever** (section 6): two vendors'
      "Vanilla" must never be merged.

      Three rules that keep the match from inventing a link:
      - **Require `LENGTH(sample_code) >= 4` for rule 2.** A two-character
        code is a substring of half the warehouse, and a wrong link is
        fabricated data, which outranks YAGNI here. Real codes are 5-7
        digits, so this excludes nothing that exists.
      - **Show every match, never pick one.** A code can sit inside several
        material names; that is two real materials, so it is two rows.
      - **Say which rule matched** ("Part #" / "Sample code in name") on a
        `Both` row, so a surprising link is explainable instead of magic.

      **Deliberately not in this task**, so it stays one sitting:
      - No new table, no schema change, no link resolution at load time.
        Match at read time in `queries.py`. Both loaders full-reload, so a
        stored link would need invalidating on every run to buy nothing at
        this row count.
      - No parsing of `location_lab` — show it verbatim. The lab's three
        location formats have no parser and getting one is its own task
        (see "Not yet built" below); depending on it would stall this.
      - No deep detail in the new tab, and no cross-tab navigation
        machinery. A caption naming the tab to open is enough; Streamlit
        makes programmatic tab switching more trouble than the problem.
      - No merging of the two tabs into it, and no third copy of the
        material stock panel. The combined tab shows Part #, name, stock
        total and stocked locations comma-joined on the warehouse side, and
        vendor, code, lab location and declaration type on the lab side.
        Joining locations is not summing them, so no split-lot caveat can be
        misread there.

      **Must not break anything:** no existing query, loader, table or tab
      changes — this is additive. `lab_samples` is empty in a synthetic-only
      database, which is every CI run, so the tab must degrade to
      warehouse-only results rather than erroring or looking broken.

      DoD: one search function in `queries.py` returning both sides per row
      (reusing the existing `get_material` / `get_stocked_locations` /
      `total_stock` / `get_lab_sample` for the detail, not reimplementing
      them); the new tab in `app.py`; tests in `tests/test_queries.py`
      covering a Part # link, a sample-code-in-name link, a warehouse-only
      hit, a lab-only hit, a code short enough to be rejected by the length
      guard, and a code matching two materials — built the way that file
      already builds fixtures (warehouse rows through the real `etl.run()`,
      lab rows through `load_lab_samples` with `_fetch_values` monkeypatched,
      as `tests/test_lab_samples.py` does). `python -m pytest` passes whole.

Two distinct identifier questions turned up under "E2," worth naming
separately so they don't get conflated: (a) what identifies a row *within*
`lab_samples` itself, since Sample Code collides 22 ways in the original
export — answered by a surrogate `lab_sample_id` plus a flagged-duplicates
report, never silent merging; (b) how a lab sample *links to* a warehouse
`materials` row for the combined-location view — answered by DTF Part #
first, falling back to Sample Code found as a substring of the warehouse
Material Name. Both are documented in `docs/flavor_sample_sheet_layout.md`.

**Not yet built**, left for later (the combined location view left this list
and became E5 above):
- A lab-specific quality report, or an in-app warnings tab surfacing
  flagged issues (duplicate codes, cross-vendor collisions) the way the
  main inventory's quality report does
- Cleaning gaps still open: prose lab locations alongside coded ones (no
  parser handles the `A-2-1`-style codes yet, let alone the plain-English
  ones), and declaration-type tags whose order varies (`Natural, WONF` vs
  `WONF, Natural` read as two categories today). The original structural
  pass also found prices with an embedded unit suffix — since resolved: the
  column is now labeled `Price ($/kg)` and cells are unit-free, which
  `cleaning.parse_price` already handles correctly.

### Phase D — Portfolio polish (last, small)

- [x] **D1. README top section rewrite**: 3-sentence problem statement, a
      screenshot of the app, the quality-report sample, quickstart. The
      design-decision prose already written stays.
      DoD: a stranger can understand and run the project from the README
      alone.
      *Screenshot is `docs/app_screenshot.png` — the material lookup tab on the
      synthetic sheet, chosen because that one view shows the metrics, the
      location table, the split-lot warning, the lot history and the price
      chart at once. Synthetic by necessity as well as principle: rule 5 of
      section 7 forbids committing a real material name, and a screenshot of
      the real sheet's data is exactly that.*
- [x] **D2. Repo hygiene pass**: LICENSE (MIT), `.gitattributes` for line
      endings (kills the CRLF warning noise), short CONTRIBUTING note that
      this is a personal portfolio project.
      DoD: files present; `git status` clean on both machines.
      *`.gitattributes` pins the working-tree ending (`eol=lf`, `*.bat`
      `eol=crlf`) rather than only declaring what's text: the warnings come
      from `core.autocrlf` converting on the Windows machine, and only an
      explicit `eol` overrides it. The synthetic CSV is marked `-text` and
      keeps its CRLF, because `csv.writer` emits CRLF on every platform —
      normalize it and every generator run, CI's included, would leave a
      whole-file diff in `git status`, which is the opposite of the DoD.*

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

Move section 5 forward by one task, and leave a record of what you did.

This section is the whole instruction set — the scheduled prompt defers to it,
so changing the automation means editing this section in a reviewed PR, not a
stored prompt nobody can diff.

### 7.1 Check GitHub first

Your checkout is a fresh clone of `master`, so unmerged work looks like it
never happened. Before anything else, get the open PRs, their branches and
their CI state from GitHub, and decide from that.

`docs/runs/` is a record, not state. If it disagrees with GitHub, it's stale.

### 7.2 Order of work

Do the **first** of these that applies.

1. **An open automation PR has failing CI or a merge conflict.** Fix it on that
   PR's branch — it blocks everything stacked behind it. Re-run a job only to
   confirm a failure naming something the diff never touched; "flake" is not a
   diagnosis. Never skip, disable or delete a test to get green.
2. **Fewer than 3 open automation PRs.** Take the next task (7.3, 7.4).
3. **3 open automation PRs.** Stop taking new work. Write the log, Slack, say
   which PR to merge first. Review is the bottleneck here, so stopping on a
   full queue is correct, not a failure.

### 7.3 Pick the task

First unchecked `- [ ]` box in section 5, top to bottom, phase A before B
before E before D, skipping the Parked section. **A task already covered by an
open PR is done** — skip it, take the next.

A task is **blocked** only when it needs something nobody in a container can
get: real credentials, real files only the user holds, live sheet access.
**Everything else is not blocked** — tests, code, synthetic data, refactoring
and documentation are all doable unattended. "Looks hard" and "needs a
decision" are the work.

When it genuinely is blocked, don't skip ahead. Add a Backlog entry naming the
task, what's needed and why; commit only that; open a PR titled `Note blocked
task: <task id>`; Slack it; stop.

### 7.4 Pick what to branch from

- Task **depends on work in an open PR** → branch from **that PR's branch**, so
  the stack merges bottom-up.
- Task is **independent** → branch from **`master`**.

Every task ticks a box in this file, so parallel branches off `master` collide
in section 5. When unsure, stack.

What you branch *from* matters; the name doesn't. Keep whatever name the
session started on, and let the PR title and run log say which task it carries.

### 7.5 Do the task, then ship it

1. Implement the smallest change satisfying the DoD, and nothing more. Read
   sections 2 and 6 first.
2. **Match the code already here.** Read `dtf_materials/cleaning.py` and
   `etl.py` first: the package layout, docstrings explaining *why* rather than
   restating the code, pure functions returning `None` or a flag instead of
   raising, ETL separate from UI.
3. **Actually run the DoD's verification** — pytest, the generator, the ETL,
   the quality report. Never assume it works.
4. Only once it passes, tick the box in the **same commit** as the change. One
   task, one commit. Never half-check.
5. Commit message: one-line summary, then a short paragraph on **why** — match
   `git log`, which explains the problem solved, not which lines moved. End
   with `Co-Authored-By: Claude <noreply@anthropic.com>`.
6. Push, then **open a PR against `master`**. Title is the summary line. Body:
   the task, what changed, what verification ran and its actual result, and any
   judgment call a reviewer would otherwise dig out of the diff.
7. If the task needs splitting, split it into sub-boxes here in that commit.
8. If verification fails after a real attempt to fix it: **don't commit, push
   or open a PR with broken work.** Log it, Slack it, leave the tree alone.
9. Anything tempting that isn't already in this file goes to `## Backlog`.

### 7.6 The run log

Every run writes `docs/runs/YYYY-MM-DD.md` in the format in
`docs/runs/README.md` and pushes it **straight to `master`** — no PR, so it
lands on days nothing gets reviewed. Two limits: that push may touch
**`docs/runs/**` and nothing else**, and `master` moves, so fetch and rebase
onto `origin/master` immediately before pushing and retry if rejected.

Then Slack once: what you did, the PR link, what needs merging. Every run,
including quiet ones — silence is indistinguishable from a run that never
fired. No Slack tool is a missing tool, not a failed run; say so in the summary.

### 7.7 Never commit

`db/*.db`, `data/real/`, `config.local.toml`, service account keys, or any real
material name, price, supplier or client. `.gitignore` blocks these; never
override it.

### 7.8 When section 5 is finished

Touch no code. Write the log, Slack that the spec is complete, stop. **Do not
invent new tasks** — section 6 exists for exactly this moment.

## Parked — Phase C (sample requests)

Sample-request ingestion is goal 5 in section 1, but nobody is asking for it
and it isn't part of the materials database as it stands. Parked rather than
deleted: if material usage history is ever actually wanted, these are still
the right tasks in the right order. The daily automation skips this section
— treat it as out of scope until someone moves it back into section 5.

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
  This was waiting on counts from B3, which B3 no longer collects. New
  trigger: decide it when someone using the app reports a material that
  shows a blank date or price it should have. That is the same evidence,
  arriving for free, from the person who actually cares.

- **The committed sample report can go stale.** `docs/quality_report_sample.md`
  is a snapshot: change the report's wording or the generator's seed and the
  committed file silently stops matching what the tool now prints. A CI step
  regenerating it and failing on a diff would fix that in about four lines,
  but it also makes every wording tweak a two-file change, and the file is
  illustrative rather than load-bearing. Left alone; revisit only if it is
  ever found to be wrong in a way that misled someone.

- **Should the config loader check that the key file exists?** B1 validates the
  three keys but not the file `service_account_key_path` points at, so a typo
  there surfaces as a Google auth error rather than a config error. Left alone
  because B2 has to handle an unusable key anyway (revoked, wrong sheet shared,
  no network), and one clear failure path there beats two half-checks.

- ~~**The committed synthetic CSV is stale.**~~ Resolved in A3: the file was
  regenerated from the current `scripts/generate_synthetic_sheet.py`, so a
  local generator run no longer produces a spurious diff.

- ~~**B3 is blocked on the automation.**~~ Resolved: the run had already
  happened on the configured machine, so B3 was checked off and its
  `docs/real_run_notes.md` requirement dropped. The general lesson stands —
  the automation's checkout has no `config.local.toml` and no service
  account key, so any task defined by a live-sheet run has to be confirmed
  by a human and recorded here.

- **The second sheet needs a second config section, not a second key.** A
  service account is an identity, and sharing is per file: share the flavor
  sheet with the same service account email as Viewer and one key reads both
  sheets, even though that sheet lives on a personal Google account rather
  than the company one. `config.local.toml` currently describes exactly one
  sheet, so it grows a second section (or a list) when E3 lands. Noted here
  rather than built now, because E1/E2 may change what that section holds.

- ~~**`run_app.bat` has five setup gaps — see issue #15.**~~ Resolved: all five
  fixed. The venv is now gated on `venv\Scripts\streamlit.exe` rather than on
  the folder existing, and a failed install deletes itself so the next run
  retries; the sheets extras install when `config.local.toml` is present; the
  Lab Samples tab explains an empty catalog instead of silently finding
  nothing; the database is refreshed by re-running the loaders, never by
  deleting it; and Python 3.11+ is verified rather than assumed.

  The two design questions were answered by separating the two people who run
  the launcher. An **operator** has the config and key, gets the sheets extras,
  and refreshes on every launch. A **viewer** has neither, gets app
  dependencies only, and has their database left strictly alone — because a
  viewer's `db/materials.db` is a real-data snapshot handed to them, and
  regenerating synthetic data over it would destroy their only copy. That also
  answered "how does a colleague see real data" without distributing a service
  account key: they don't need Google access, they need a database file.

  The synthetic lab-sample generator was dropped. It was justified in the issue
  partly as filling a test-coverage gap, which was wrong —
  `tests/test_lab_samples.py` already covers header validation, duplicate-code
  flagging and reload-clearing with fabricated rows. The only remaining argument
  is that a stranger cloning this public repo sees an empty tab, which the new
  message now explains.

- **Shared database on a synced drive, once there is a live sheet behind it.**
  The colleague handoff is a snapshot today: the operator refreshes, then sends
  `db/materials.db` (~1.4 MB) by hand, and it is stale from the moment it
  arrives. Putting the database in a shared OneDrive folder would replace that
  with one central refresh everyone picks up.

  Two things sit in front of it, and the second is the real one:

  (a) `DEFAULT_DB_PATH` is hardcoded (`dtf_materials/db.py:10`). The app and
  both loaders would need to take a configured path — small, and it belongs in
  `config.local.toml` next to the sheet settings.

  (b) The ETL currently reads a personal *copy* of the inventory sheet, not the
  company's live one, and that copy does not update. A shared database built on
  it would distribute stale data faster rather than fresher data — motion
  without movement. This is worth building the day the real sheet is shared,
  and not before.

  When it is built: write the database locally and copy the finished file to the
  synced folder, never point a loader's write path at a folder a sync client is
  actively touching, and keep exactly one writer. SQLite is single-writer by
  design and a sync client racing a live transaction is how the file gets
  corrupted or silently forked into a "conflicted copy".

  Note this collides with section 6's "no scheduled ETL daemon". Daily
  auto-refresh for a shared database is a genuine reason to revisit that
  decision — but revisit it explicitly, as its own entry here, rather than
  drifting into a scheduler because it seemed convenient.

- ~~**`search_by_location` doesn't filter `ready_to_archive`.**~~ Resolved:
  keep archived lots visible when browsing a rack, and label them. Surfaced
  while writing A5 — every other stock query excludes archived lots
  (`get_stocked_locations`, `total_stock`, `unlocated_stock`,
  `list_materials`) and this one does not.

  Answered by separating the two questions. A material's page answers "can I
  use this?", where archived means no. The rack view answers "what is
  physically on this shelf?", where the drum really is there and hiding it
  would make the app disagree with the warehouse. So the row stays, and
  `search_by_location` now also returns `ready_to_archive` so the app can
  show a "Status" column reading `Archived`, with a caption explaining why a
  row appears here but not in the material's own stock figures.

  No new data and no inference: the flag is the sheet's own Ready To Archive
  column, parsed by `cleaning.parse_bool_flag` exactly as it already was.
  Pinned by `test_rack_browsing_shows_archived_lots_and_flags_them`.

- **Aisle search is a substring match, not an anchored prefix.** Also from
  A5. `search_by_location` builds its pattern as
  `_like(prefix).rstrip("%") + "%"`, and since `_like` wraps the term in
  `%…%` the leading wildcard survives — so searching `6L` matches `A6L-99-Z`
  as well as the 6L aisle. Two smaller consequences ride along: a term ending
  in a literal `%` has its trailing wildcards stripped in a way that changes
  what it means, and the `.rstrip("%")` is doing nothing the caller asked for.

  Anchoring it is a one-line change, but it is a behavior change for anyone
  who has learned to type a fragment, and no real location code currently
  embeds another aisle's prefix — so the bug is latent rather than felt.
  Revisit when a location search returns something visibly wrong. Current
  behavior is pinned by
  `test_location_search_currently_matches_a_prefix_anywhere_in_the_code`.
