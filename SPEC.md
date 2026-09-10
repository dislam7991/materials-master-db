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

Two distinct identifier questions turned up under "E2," worth naming
separately so they don't get conflated: (a) what identifies a row *within*
`lab_samples` itself, since Sample Code collides 22 ways in the original
export — answered by a surrogate `lab_sample_id` plus a flagged-duplicates
report, never silent merging; (b) how a lab sample *links to* a warehouse
`materials` row for the combined-location view — answered by DTF Part #
first, falling back to Sample Code found as a substring of the warehouse
Material Name. Both are documented in `docs/flavor_sample_sheet_layout.md`.

**Not yet built**, left for later:
- The combined warehouse+lab location view using the E2 matching strategy
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

The daily run has one job: move section 5 forward by one task and leave a
record of what it did.

**This section is the whole instruction set.** The scheduled prompt defers to
it, so changing how the automation behaves means editing this section in a
reviewed PR — not editing a stored prompt that nobody else can read, review,
or diff.

### 7.1 GitHub is the source of truth, not the files in front of you

A run works in a throwaway container cloned from `master`. Unmerged work is
invisible there: a branch pushed yesterday and a PR opened last week both look
like they never happened. **Before doing anything, ask GitHub** what branches
and open PRs exist, whose they are, and what state their CI is in. Decide from
that answer.

`docs/runs/` is a record written for a human. It is never an input to a
decision. If the log and GitHub disagree, GitHub is right and the log is
stale.

This rule exists because the alternative was tried: a run that read only its
checkout saw every box in section 5 unchecked, could not tell finished work
from unstarted work, and stopped for the day rather than risk repeating
itself.

### 7.2 Order of work

Do the **first** of these that applies. Do not skip ahead.

1. **An open automation PR has failing CI, or a merge conflict.** Fix it, on
   that PR's own branch. This outranks new work every time: a PR that cannot
   merge blocks everything stacked behind it, and shipping a second feature on
   top of a broken one just makes a bigger thing to unpick. Re-run a job only
   to confirm a failure that names something the diff never touched — "flake"
   is not a diagnosis. Never skip, disable, or delete a test to get green.
2. **Fewer than 3 open automation PRs.** Take the next task — 7.3 and 7.4.
3. **3 open automation PRs.** Stop taking new work. Write the log entry, send
   the Slack ping, and say plainly that the queue is full and which PR to
   merge first.

The cap is 3 because review is the bottleneck this whole arrangement is built
around, and a five-deep stack is harder to review than one PR. Work stopping
is the correct outcome when the queue is full — it is not a failure to
report as one.

### 7.3 Pick the task

The first unchecked `- [ ]` box in section 5, top to bottom, phase A before B
before E before D, skipping the Parked section entirely.

**A task already covered by an open PR is done.** Skip it and take the next
one. That is what the GitHub query in 7.1 is for.

A task is **blocked** only when it needs something nobody inside a container
can get: real credentials (a service-account key, `config.local.toml`), real
files only the user holds, or access to a live Google Sheet.

**Everything else is not blocked.** Writing tests, writing code, generating
synthetic data, refactoring and documentation are all doable unattended.
"Looks hard", "needs a decision", and "would be better with the real data"
are not blockers — the first two are the work, and the third is what the
synthetic-first principle exists to route around.

When the top task genuinely is blocked, do not skip ahead to a later one.
Add a Backlog entry naming the task, exactly what is needed from the user,
and one sentence on why; commit only that SPEC.md change; open a PR titled
`Note blocked task: <task id>`; Slack it; stop for the day.

### 7.4 Pick what to branch from

- The task **depends on work sitting in an open PR** → branch from **that
  PR's branch**. The stack then merges bottom-up cleanly.
- The task is **independent** → branch from **`master`**.

Every completed task checks a box in this file, so two branches cut from
`master` in parallel will collide in section 5. When unsure, stack.

What you branch *from* is the part that matters. The name matters less: call
it `claude/e2-identifier-strategy` if you are free to choose, but a scheduled
session usually starts pinned to a branch name it did not pick. Keep that
name rather than fighting the harness for it — just make sure the PR title
and the run log say which task the branch carries, so the branch list is
still readable on Monday.

### 7.5 Do the task, then ship it

1. Implement the smallest change that satisfies the task's stated DoD, and
   nothing beyond what the DoD asks for. Read section 2 (Principles) and
   section 6 (Out of scope) before writing anything.
2. **Match the code that is already here.** Read a couple of neighbouring
   files first — `dtf_materials/cleaning.py` and `dtf_materials/etl.py` are
   the reference. What matters: the `dtf_materials/` package layout,
   docstrings that explain *why* rather than restate what the code does,
   pure functions that return `None` or a flag instead of raising, and ETL
   kept separate from UI.
3. **Actually run the verification the DoD asks for** — `python -m pytest`,
   the generator, the ETL, the quality report. Never assume it works.
4. Only once verification passes, check the box, in the **same commit** as
   the change that satisfies it. One task, one commit. Never half-check.
5. Commit message: a one-line summary, then a short paragraph on **why**.
   Match the style already in `git log` — these messages explain the problem
   the change solves, not which lines moved. End with a
   `Co-Authored-By: Claude <noreply@anthropic.com>` trailer.
6. Push the branch, then **open a pull request against `master`**. Title is
   the commit's one-line summary. Body: what the task was, what changed,
   what verification ran and its actual result, and any judgment call a
   reviewer would otherwise have to reverse-engineer from the diff.
7. If a task turns out to need splitting, split it into sub-boxes here in
   that same commit.
8. If verification fails after a genuine attempt to fix it: **do not commit,
   push, or open a PR with broken work.** Log it, Slack it, leave the tree
   alone for a human.
9. Anything tempting that appears mid-task and is not already in this file
   goes to the `## Backlog` section — not into the code.

### 7.6 The run log

Every run writes `docs/runs/YYYY-MM-DD.md`, in the format described in
`docs/runs/README.md`, and pushes it **directly to `master`**. No PR, no
sign-off. The log has to land even on days when nothing else gets reviewed —
that is its entire purpose.

Two hard limits on that push:

- It may touch **`docs/runs/**` and nothing else.** Never bundle a code or
  SPEC change into a direct-to-master push; those go through a PR like
  everything else.
- `master` moves. Fetch and rebase onto `origin/master` immediately before
  pushing, and re-fetch and retry if the push is rejected.

Then send one Slack message: what was done, the PR link, what needs merging.
One per run, including quiet ones — a silent day is indistinguishable from a
run that never fired. If no Slack tool is available, say so in the run
summary; that is a missing tool, not a failed run.

### 7.7 Never commit

`db/*.db`, `data/real/`, `config.local.toml`, service account keys, or any
real material name, price, supplier, or client. `.gitignore` already blocks
these; never override it.

### 7.8 When section 5 is finished

If every box in section 5 is checked, touch no code. Write the log entry,
send one Slack message saying the spec is complete, and stop.

**Do not invent new tasks.** Section 6 is a list of things that look useful
and are not to be built, and it exists for exactly this moment. A run that
fills an empty day with unrequested work is worse than a run that does
nothing, because someone now has to review it and decide whether to throw it
away.

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
