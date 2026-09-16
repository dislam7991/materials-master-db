# Materials Master — Project Spec & Plan

Single source of truth for what this project is, what's done, what remains, and
what's deliberately out of scope. Work the checklists top to bottom. **Every
checked box is a real, tested change in the commit that checks it.** One task
per commit.

---

## 1. Goal

A supplement contract manufacturer with no ERP keeps inventory in a shared
Google Sheet and sample requests in loose Excel files. Nothing connects; it's
all manual copy-paste.

This is the minimum system that fixes that:

1. **A SQLite materials master** — one typed record per material, keyed on DTF
   Part #, with lots (receivings) underneath.
2. **A source-swappable ETL** — pulls the inventory sheet, cleans, validates,
   loads (CSV today, Google Sheets API at the end).
3. **A data-quality report** identifying every dirty row in the source.
4. **A Streamlit lookup app** — type a part # or name → location, price,
   supplier, lot history.
5. **Sample-request ingestion** — link materials ↔ samples so usage history
   lives in one place. *Parked; nobody is asking for it yet.*

Doubles as a public portfolio project, so the repo holds **synthetic data
only**. The real-sheet connection lives in gitignored local config, never
committed.

## 2. Principles (read before adding anything)

- **Simplicity over complexity, always.** A feature earns its place by
  answering a question someone at the company actually asks, not by impressing.
- **Never fabricate data.** If the source doesn't record it, the tool says so
  instead of inventing a plausible number.
- **Flag, don't guess.** Ambiguity goes to the quality report for a human; the
  ETL never auto-resolves it.
- **Preserve the raw.** Every source row lands verbatim in staging before any
  cleaning. Parsing failures are countable, not silent.
- **Synthetic-first.** Built and tested against generated fake data shaped like
  the real sheet, dirt included. Real data connects last, via local config.
- **Idempotent, atomic loads.** Re-running is always safe; a crash mid-run
  leaves the DB untouched.

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
   ├── quality_report.py       reads staging → identifies every dirty row
   └── app.py + queries.py     Streamlit lookup (UI and data access separated)
```

| Decision | Why |
|---|---|
| SQLite, not Postgres | Single-writer internal tool; zero admin; ports to Postgres nearly verbatim if concurrency is ever needed. |
| Surrogate `material_id`, `dtf_part_num` UNIQUE-nullable | Samples exist before they get a Part #; promoting one is a column update, not a row migration. |
| Lots in a child table | One material, many receivings — textbook one-to-many. |
| Price on the lot + derived current price on material | History for free; suppliers reprice between receivings. |
| Materials upserted, never deleted | `material_id` is referenced by sample history; delete-and-reinsert corrupts links. |
| Lots fully reloaded each run | Nothing external references `lot_id`; reload is idempotent and simpler than change-tracking at this volume. |
| One atomic transaction per run | A crash mid-load must not leave a wiped DB. |
| Raw `locations_raw` + parsed `lot_locations` | Cell is free text, possibly several codes; keep truth, query the parse. |
| Supplier aliases, fuzzy matches only *flagged* | Auto-merging similar strings is how two real companies get combined. |
| Dates as ISO text | SQLite has no date type; ISO sorts correctly. |
| ETL is stdlib-only | Fewer deps, trivially portable; pandas/streamlit needed only by the app. |

## 4. Status

**Section 5 is complete** — every task in Phases A, B, E and D is done.
Phase C (sample requests) is parked. What's built:

- [x] Schema + idempotent DB init, synthetic dirty-sheet generator
- [x] ETL: source adapter interface, CSV source, staging, cleaning, atomic load
- [x] Data-quality report citing exact source rows; `--out` Markdown flag
- [x] Location parsing for the real code format (`6L-27-D`)
- [x] Streamlit app: material + location lookup, honest stock reporting
      (split lots flagged, unlocated stock surfaced, last-known-location
      fallback)
- [x] Pytest for `cleaning.py`, ETL invariants, and `queries.py`; GitHub
      Actions CI (stdlib-only, synthetic-only), README badge
- [x] Local config loader (`tomllib`), `SheetsInventorySource` (service
      account), first real run confirmed on a configured machine
- [x] Flavor-sample (lab) feature: `lab_samples` table + loader, Lab Samples
      search tab, and the combined **Warehouse + Lab** lookup tab
- [x] Portfolio polish: README rewrite + screenshot, LICENSE, `.gitattributes`,
      CONTRIBUTING

### Phase E match rule (load-bearing — implement, don't redesign)

The combined tab links a lab sample to a material by, in order:

1. `lab_samples.dtf_part_num` = `materials.dtf_part_num`, both non-blank.
2. `sample_code` as a case-insensitive substring of `material_name`,
   requiring `LENGTH(sample_code) >= 4` (real codes are 5–7 digits; a shorter
   code matches half the warehouse, and a wrong link is fabricated data).

Show **every** match (a code can sit in several material names — that's several
real rows), label which rule matched, and **never fuzzy-match names** (two
vendors' "Vanilla" must not be merged). Matching is at read time in
`queries.py`; both loaders full-reload, so a stored link would buy nothing.
`lab_samples` is empty in any synthetic-only DB (every CI run), so the tab
degrades to warehouse-only rather than erroring.

Two identifier questions, kept distinct (both in
`docs/flavor_sample_sheet_layout.md`): (a) what identifies a row *within*
`lab_samples` — a surrogate `lab_sample_id` plus a flagged-duplicates report,
never silent merging; (b) how a lab sample *links to* a material — the rule
above.

## 5. Explicitly out of scope (do not build)

- No Postgres/MySQL — SQLite is correct at this scale (revisit only for
  concurrent writers).
- No auth, hosting, or Docker — the app runs locally on demand.
- No write-back to the Google Sheet — one-way ingestion only.
- No incremental/CDC loading — full reload is idempotent and fast at hundreds
  of rows (revisit at ~50k, not before).
- No auto-merge of fuzzy supplier matches, no auto-resolution of part-#
  conflicts — humans resolve, the report flags.
- No invented stock splits across locations — the source doesn't record it.
- No ORM, no web framework beyond Streamlit, no dashboarding suite.
- No scheduled ETL daemon — run on demand.

## 6. Working agreement for the daily automation

This section is the automation's whole instruction set — the scheduled prompt
defers to it, so changing the automation means editing this section in a
reviewed PR.

**Check GitHub first.** The checkout is a fresh clone of `master`, so unmerged
work is invisible. Get open PRs, branches and CI state from GitHub and decide
from that. `docs/runs/` is a record, not state; if it disagrees with GitHub,
it's stale.

**Order of work — do the first that applies:**

1. An automation PR has failing CI or a merge conflict → fix it on that branch
   (it blocks everything stacked behind it). Never skip, disable or delete a
   test to get green.
2. Fewer than 3 open automation PRs → take the next unchecked box in the
   checklists (section 4 Status, then Parked), top to bottom, Phase A→B→E→D,
   skipping Parked. A task covered by an open PR is done.
3. 3 open automation PRs → stop taking work. Write the log, Slack which PR to
   merge first. Review is the bottleneck; stopping on a full queue is correct.

**Branch from** the PR you depend on (so the stack merges bottom-up), else from
`master`. The name doesn't matter; the PR title and run log carry the task.

**Do the task:** implement the smallest change meeting the DoD and nothing more;
match the existing code (read `cleaning.py` and `etl.py` first); run the
verification for real (pytest, generator, ETL, report); only then tick the box
in the **same commit**. Commit message: one-line summary + a short paragraph on
*why*, ending `Authored-By: Daniel <dislam7991>`. Push, open a PR
against `master`. If verification fails after a real fix attempt, don't
commit — log it, Slack it, leave the tree alone.

**Blocked** = only a human can do it (real credentials, real files, live sheet
access). Everything else — tests, code, synthetic data, refactoring, docs — is
the work; "looks hard" and "needs a decision" are not blockers. When genuinely
blocked, add a Backlog entry, commit only that, open a PR titled
`Note blocked task: <task id>`, Slack it, stop.

**The run log:** every run writes `docs/runs/YYYY-MM-DD.md` (format in
`docs/runs/README.md`) straight to `master` — no PR. That push touches
`docs/runs/**` and nothing else; rebase onto `origin/master` immediately before
pushing and retry if rejected. Then Slack once — what you did, the PR link,
what needs merging — every run, including quiet ones. A missing Slack tool is a
missing tool, not a failed run; say so in the summary.

**Never commit** `db/*.db`, `data/real/`, `config.local.toml`, service account
keys, or any real material name, price, supplier or client.

**Nothing outside the checklists is a queue.** Parked Phase C, Direction
Phase F and the Backlog are held deliberately and carry no `- [ ]` boxes for a
reason; never promote work out of them. Moving something into a checklist is
the user's call, in a reviewed PR.

**When the checklists are finished** (every box in section 4 checked, Parked
skipped): touch no code, write the log, Slack that the spec is complete, stop.
Do not invent new tasks — section 5 (out of scope) and the Backlog exist for
exactly this.

## Parked — Phase C (sample requests)

Goal 5, but nobody is asking for it and it isn't part of the materials database
as it stands. Parked, not deleted — still the right tasks in the right order if
usage history is ever wanted. The automation skips this section.

- [ ] **C1. Get 2–3 real sample-request Excel files, map their layout** in
      `docs/sample_sheet_layout.md`. No code.
      DoD: layout doc committed.
- [ ] **C2. Synthetic sample-request generator** — fake `.xlsx` matching the
      real layout, dirt included, seeded.
      DoD: generator writes N fake files.
- [ ] **C3. Sample ingestion** (openpyxl) — fill `samples` +
      `sample_materials`, match by Part #; unmatched part #s become quality
      findings, never guessed rows.
      DoD: ETL loads synthetic samples; report gains an "unmatched sample
      materials" section; tests cover match/no-match.
- [ ] **C4. Sample history in the app** — a material's page shows which samples
      used it; a "Samples" tab looks up a sample and its materials. Queries in
      `queries.py`, UI in `app.py`.
      DoD: both directions visible against synthetic data.

## Direction — Phase F (formula builder + sample documentation)

**Not scheduled, no `- [ ]` boxes** — the automation skips it like Parked C
and must not promote it. Written down so the direction survives and the
questions in front of it are asked once, not rediscovered mid-build.

**The problem.** Finishing a sample means producing three documents by hand —
a **flavor sheet**, a **sample record sheet**, and **labels** — each an exact
copy of a company Excel template with autocalculating fields, each uploaded to
a cloud folder (OneDrive, PD Ops). Almost every value is already in this
database; the work is transcription, repeated per sample. Target: pick
materials from the DB into a formula, enter amounts, press one button, get all
three filled in exactly like the template. **Success is measured in clicks and
minutes per completed sample** — record the current number before building, or
there's no way to know it helped.

**Shape.**
- **One formula, three renderers.** A formula (its (material, amount) lines +
  batch size + a few header fields) is a first-class DB object; each artifact
  is one rendering. Build the object first (view, edit, total it); a fourth
  document later is one renderer, not a redesign.
- **Fill the template, never regenerate it.** "Copied exactly" is the
  requirement: copy the `.xlsx`, write into known cells, save as new. Rebuilt
  layout drifts from the company form the first time someone nudges a border.
- **Leave the autocalc fields alone** — write inputs, let the sheet's formulas
  produce outputs; two copies of a trusted calculation is a support call.
  Consequence: openpyxl doesn't evaluate formulas, so any computed value the
  *app* must display or print needs Excel/LibreOffice to recalc, or is
  computed twice on purpose. Decide per field.
- **Materials are chosen, never typed** — dragged from Warehouse or Lab
  results, or added by an "Add to formula" button on the row, which pulls
  every field the DB knows. Both catalogs are pickable (a formula mixes an
  adopted material with a lab-only sample). A free-text material line would put
  an unbacked name/price in the DB — fabricated data by another route (§2), and
  the very transcription this removes. The only authored values: **which rows,
  how much of each, batch size, and header fields no catalog holds.**
- **Lines reference rows, never copy them** — a corrected price reaches every
  formula. This needs ids that survive a reload: `material_id` does (A2 pins
  it); **`lab_sample_id` does not** — it's a full-reload surrogate precisely
  because nothing references it, so a formula pointing at a lab sample makes
  that false and the next load silently repoints it. Stable lab-sample identity
  is a **prerequisite** of this phase (and §4(a): Sample Code can't be it).
- **Reference for editing, snapshot at generation** — a flavor sheet already
  sent out keeps that day's numbers; a reprint must not silently disagree with
  the copy on someone's desk.
- **This is the first DB data no loader can rebuild.** So: full-reload loaders
  must not touch formula tables, `db/*.db` stops being a disposable cache and
  needs a backup story, and schema changes start needing migrations. The real
  cost of this phase.
- **Local files first, sync later.** Artifacts land in a folder the user
  already syncs; a real OneDrive/SharePoint integration needs M365 credentials
  and IT — stage it last (like B1–B3), never let it gate the time-saving part.
- **Synthetic-first still holds** — real templates carry branding and, filled,
  real names/prices (§6 forbids committing them): they live in `data/real/`
  (gitignored); committed is a layout map (`docs/`, like E1) + a synthetic
  template of the same shape for CI.

**Prerequisite, blocks everything.** Copies of the three real templates and a
reverse-engineering pass on each — which cells are inputs, which formulas,
which formatting, what the autocalc computes, and which inputs the DB already
supplies vs. a human types. No code until that map exists (same step E1 was).

**Questions to answer first.**
1. Which of the three hurts most, and how often? Do that one end-to-end before
   the others.
2. What does a sample's documentation cost today, in minutes and clicks? The
   baseline.
3. `.xlsx` or `.xlsm`? Macros change everything — openpyxl preserves neither
   macros nor every chart/image.
4. Who owns the templates, how often do they change? If revised centrally, the
   tool loads a new file rather than baking the layout into code.
5. Percentage-based, weight-based, or both? Does scaling to batch size round to
   what can actually be weighed?
6. Which fields come from the DB vs. typed per sample? Decides how much the
   builder prefills.
7. Does the sample get a number, assigned by whom — this tool, a person, or an
   existing log that must stay in sync?
8. Do labels belong in Excel at all? Label printers usually want PDF or a
   label format, even though the company's current template is a spreadsheet.
9. Does anything go back to a sheet/log when a sample is finished? (§5 forbids
   inventory-sheet write-back; a *sample log* is a different sheet — a
   question, not a contradiction.)
10. Does anyone sign off the generated documents, and must a regenerated one be
    distinguishable from the version already sent?

**Alternatives considered.** Generate from scratch (rejected — exact copy
required). Excel-side automation, a macro/Power Query reading the SQLite file
(simpler for the company to own, worth a real look — but untestable here, and
the builder belongs next to the data). PDF output (right for labels and
anything not to be edited after; wrong for a workbook recipients expect).

## Backlog

Things that came up mid-task and are deliberately not built yet (principle:
flag, don't guess). Decision + why; the detail is in git history if needed.

- **Widen `cleaning.py` formats?** No — parking `1/5/25`, `Jan 5, 2026`,
  `12.50 USD` and `(15.00)` in the quality report is correct until a real user
  reports a blank date/price that should have parsed. `(15.00)` should stay
  unparsed regardless (neither `15.00` nor `-15.00` is a defensible price to
  invent).
- **Committed sample report can go stale.** `docs/quality_report_sample.md` is
  a snapshot; a wording or seed change silently desyncs it. Not worth a
  CI-diff step (makes every tweak a two-file change); revisit only if it
  misleads someone.
- **Config loader doesn't check the key file exists.** Left alone — B2 must
  handle an unusable key anyway, and one clear failure path beats two
  half-checks.
- **Second sheet needs a second config section, not a second key.** Share the
  flavor sheet with the same service-account email as Viewer; one key reads
  both. `config.local.toml` grows a section (or list) — noted, not built,
  since E1/E2 may change what it holds.
- **Shared database on a synced drive.** The colleague handoff is a manual,
  instantly-stale `db/materials.db` copy. A shared OneDrive DB would fix it,
  but two things block it: (a) `DEFAULT_DB_PATH` is hardcoded
  (`dtf_materials/db.py:10`) and must become configurable; (b) the ETL reads a
  personal *copy* of the sheet that doesn't update, so a shared DB would just
  distribute stale data faster. Build it the day the real sheet is shared, not
  before. When built: write locally then copy the finished file in, keep
  exactly one writer, never point a loader at a sync-touched folder. Collides
  with "no scheduled ETL daemon" — revisit that explicitly if daily
  auto-refresh is wanted, don't drift into a scheduler.
- **Aisle search is a substring match, not an anchored prefix.**
  `search_by_location`'s pattern keeps a leading wildcard, so `6L` matches
  `A6L-99-Z`. One-line fix, but a behavior change for anyone typing fragments,
  and no real code embeds another aisle's prefix — latent, not felt. Revisit
  when a location search returns something visibly wrong. Pinned by
  `test_location_search_currently_matches_a_prefix_anywhere_in_the_code`.

### Resolved (kept for the reasoning)

- **`search_by_location` didn't filter `ready_to_archive`.** Resolved: a
  material's page answers "can I use this?" (archived = no); the rack view
  answers "what's physically on this shelf?" (the drum is there, so show it,
  labelled `Archived`). No inference — the flag is the sheet's own column.
- **`run_app.bat` setup gaps (issue #15).** Resolved: launcher now separates an
  **operator** (has config + key, gets sheets extras, refreshes each launch)
  from a **viewer** (neither; app deps only; DB left strictly alone, since it's
  a handed-over real-data snapshot). The synthetic lab-sample generator was
  dropped — `tests/test_lab_samples.py` already covers the coverage gap, and
  the empty-tab message handles the stranger-cloning-the-repo case.
