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

**Phases A, B, E and D are done.** Phase C (sample requests) is parked. Phase F
(formula builder + sample documentation) was promoted from Direction on
2026-09-21 at the user's request and is now the active queue — its checklist is
below the built list, its load-bearing design in the **Phase F design
reference** section. What's built:

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

### Phase F (formula builder + sample documentation) — active

Promoted from Direction on 2026-09-21 at the user's request. Work top to bottom;
the load-bearing design is the **Phase F design reference** section below —
implement it, don't redesign it. F0 (stable `RD-ID` for lab samples) is already
done. The three real templates were received 2026-09-21 and are mapped in
`docs/phase_f_templates_layout.md` (F3).

- [x] **F1. Formula object** — `formulas` + `formula_lines` schema and idempotent
      init. A line *references* a row (`material_id` for adopted materials, the
      stable `rd_id` for lab samples — the rebuild-robust handle), never copies
      its name or price, so a correction reaches every formula. Full-reload
      loaders must NOT touch these tables (first DB data no loader can rebuild).
      DoD: `pytest` covers create/total and reference integrity (a repriced
      material flows through an existing line); an ETL run leaves formula rows
      untouched.
- [x] **F2. Formula builder UI** — pick materials from Warehouse/Lab results into
      a formula (never typed), enter amounts + batch size + the header fields no
      catalog holds, view and total it. Data access in `queries.py`, UI in
      `app.py`.
      DoD: a formula built end-to-end against synthetic data with totals shown.
      **Superseded 2026-09-22:** the user tried it and it didn't match the daily
      work. The tab is replaced by F2b; `formulas.py` and its tables stay (F1)
      until the Sample Record Sheet decides whether it needs them.
- [x] **F2b. Flavor Sheet, built backwards from the deliverable** (user's call,
      2026-09-22 — the sheet R&D fills daily, so it goes first). Header
      (customer, product, quote ID, servings per flavor) + any number of flavor
      profiles (BASE mg + lines in mg/serving); g/sample derived, never stored.
      Saved and reopenable; Sample ID suggested as `<prefix><YYMMDD>-NN`,
      editable. Lines are picked from Warehouse/Lab, **or typed and flagged "not
      in catalog"** — a deliberate exception to "chosen, never typed", safe here
      because the sheet carries no price. Renderer fills the real template
      (`flavor_sheet_xlsx.py`): no line limit (a block grows rows), four flavors
      a page (extra pages copy the template sheet). Tables:
      `flavor_sheets`/`flavor_profiles`/`flavor_profile_lines`.
      DoD: `pytest` covers the object, loader isolation, and the renderer
      against a synthetic template of the real shape (CI installs openpyxl).
- [x] **F2c. Sample Record Sheet** — next deliverable, same approach: work
      backwards from the template (layout map §1), reuse the flavor profiles.
      Renderer only (`sample_record_xlsx.py`); no UI. It stays unused while
      F2d–F2g are parked — managers build the sheet themselves (F2i).
- [x] **F3. Reverse-engineer the three real templates** (flavor sheet, sample
      record sheet, labels) — which cells are inputs, which are formulas, what
      the autocalc computes, which inputs the DB supplies vs. a human types.
      Done 2026-09-21: layout map `docs/phase_f_templates_layout.md` (all
      three, with template defects as findings); the two spreadsheet shapes
      are rebuilt synthetically inside `tests/test_flavor_sheets.py` and
      `tests/test_sample_record.py`, so CI needs no committed template file.
- **You, not the automation (updated 2026-09-24):** for F2f when it's unparked: one PL Cost Sheet
  formula table pasted into `data/real/templates/pl_cost_paste.txt` (header
  row included). The labels template arrived as `.docx` 2026-09-24. The
  manager-built record sheet arrived 2026-09-24
  (`Sample_Record_Sheet_Template_Filled.xlsx`, layout map §1). The template
  is **not** to be enlarged or "fixed" by hand any more — the typed F values
  are the PL Cost Sheet paste, on purpose. When a flavor profile doesn't fit,
  insert rows in Excel (it extends the ranges itself) before pasting F2i's
  block.
- [x] **F2i. Flavor lines as a copy block** (user's workflow and call,
      2026-09-24 — layout map §1, "A filled manager sheet"). The manager
      downloads the record sheet from OneDrive, pastes the actives from the PL
      Cost Sheet and types the excipients; the flavor lines are the part the
      app saves typing on. The app **never opens or writes the manager's
      file** — openpyxl would change it on save (layout §4) and can't grow the
      section safely (finding 5); Excel does both correctly when the user
      inserts rows. On a Sample Record Sheet tab (new; holds only this — the
      rest of F2e's design stays parked): pick a flavor profile, get its lines
      as tab-separated text with a copy button, pasted by the user into column
      A of the first empty row below the last excipient, spreading over A:I.
      - One row per profile line, BASE excluded (BASE is the actives already on
        the sheet). Columns: A Part # (blank if none), B material name,
        C label claim = the profile's mg/serving, D `1` (Activity 100 %),
        E `0` (Overage), F = C (Actual Input, a value), G and H empty (the user
        fills g/run and Formula % down from the excipient row), I Price/kg.
        No header row. J/K are not in the block.
      - Part # and Price/kg resolved from the DB (`record_line_catalog`),
        never copied into the profile; a missing price is an empty field,
        flagged beside the block, never 0.
      - Show the line count, so the user knows how many rows to insert first
        when the section is short.
      - Part numbers that Excel would reformat on paste (all digits with
        leading zeros) must survive — check the real `dtf_part_num` shapes
        before choosing how.
      - Scoop size and Jar/Lid are not involved.
      DoD: usable in the app — pick a profile, copy, paste into the real
      template in Excel lands in the right columns (user confirms). `pytest`
      on the block builder with synthetic profiles: column order and count,
      BASE skipped, D/E/F defaults, blank Part # and blank price stay empty
      fields, a name containing a tab or newline can't shift columns.
- [ ] **F4. Labels** (unparked 2026-09-24, user's call). Fill the labels
      template `data/real/templates/Sample Labels Blank Template.docx`
      (layout map §3 — the text is in a 5×3 table, not the shapes) from a
      flavor sheet: **one label per flavor profile**, in the sheet's order,
      labels 1–10 read left-right, top-down. More than 10 flavors → more
      documents, 10 flavors each; unused labels stay empty. A "Download
      Labels" button on the Flavor Sheet tab, one download per document.
      Extra copies (retain, multiple ship-to units) the user makes by hand.
      - Five lines per label, **values only — no captions** (user,
        2026-09-24): customer, product (flavor sheet header), flavor name,
        sample code (profile), and serving size as `<n> scoop serving
        (<g> g)`, e.g. `2 scoop serving (12.3 g)`.
      - `<n>` = scoops per serving, a field in the app **pre-filled with 1**
        (most products, user 2026-09-24) and stored per product, so a
        product that differs is changed once and remembered; a whole number
        prints without decimals. `<g>` = the
        profile's BASE mg + its flavor lines' mg, in grams, **1 decimal,
        rounded half-up** (not Python's `round`, which rounds 0.05 to even).
        BASE typically already includes the excipients. Water volume, when
        needed, the user adds by hand.
      - Fill, never regenerate: copy label 1's five paragraphs into each
        label used, keeping `w:pPr`/`w:rPr`, and set the run text; never
        touch the outline shapes. `.docx` out. No docx library in the ETL.
      DoD: usable in the app — download labels for a flavor sheet, open in
      Word, and the grid lines up with the label stock (user confirms on a
      test print). `pytest` fills a synthetic `.docx` of the real shape (5×3
      table, exact row heights, 10 outline shapes): one label per profile in
      order, no captions, 11 flavors → two documents (10 + 1), formatting and
      row heights unchanged, shapes untouched, grams half-up to 1 decimal,
      a new product defaults to 1 scoop, a changed count is remembered per
      product, a blank field prints empty, never
      `None`.
- [ ] **F2h. Snapshot at download** (moved from F4; narrowed 2026-09-24 —
      the record sheet has no download any more, and the app can't see a
      copy). Each download of a flavor sheet or of labels (F4) stores the
      numbers it was rendered from (prices included) with a timestamp, so a
      reprint after a reprice is distinguishable from — and comparable to —
      the copy that was sent.
      DoD: `pytest` shows a reprice between two downloads yields two
      snapshots that differ, and the first is still retrievable.

#### Parked in Phase F (user's call, 2026-09-24 — the automation skips these)

Kept, not dropped: the user wants all of these eventually. F2c2 and F2d–F2g
were the "build the whole sheet in the app" path; F2i does the part that
saves the most copy-paste first. Activity per material: the user will supply
data for it later, likely as its own table — that feeds F2d.

- [ ] **F2c2. Grow the excipient section instead of refusing.** *(Planned
      in #40, parked the same day: F2i's copy block replaced it, user's
      call 2026-09-24 — Excel grows the section when the user inserts rows.
      It only matters again if F2e renders whole sheets.)* Flavor
      profile + excipients sometimes exceed the 12 rows (37–48). The renderer
      inserts the extra rows below row 48, and everything under them moves
      down intact: the merged "Inactive Ingredient Cost" subtotal row and the
      totals row, their `SUM` ranges extended over the new rows, every
      reference to a moved cell (`$F$50` in column H, `J49`/`J50` in the cost
      panel), merged ranges, banding and the print area. New rows copy row
      48's formulas and styles. openpyxl's `insert_rows` shifts none of this,
      so the renderer rewrites it — anything it can't move correctly is a
      refusal, never a silently wrong total. Actives keep their fixed 23 rows.
      DoD: `pytest` renders 15 excipient lines into the synthetic template and
      checks the subtotal/total formulas cover all 15, the cost panel points
      at the moved cells, and the merged subtotal label moved with its row;
      12 lines still renders byte-for-byte as before.
- [ ] **F2d. Remembered Activity / Overage per material.** Neither is in the
      source data. Store the last value used per material (Warehouse or Lab
      row, by id), pre-fill it on the next record sheet, overridable per sheet.
      Flavor-profile lines default to Activity 1, Overage 0. Never shown as a
      catalog fact — it's "last used", labelled as such.
      DoD: `pytest` covers save, pre-fill, per-sheet override, and that a
      material with no history pre-fills blank (never a guessed number).
- [ ] **F2e. Sample Record Sheet tab** (user's design, 2026-09-23). Pick a
      flavor profile → Flavor, Sample code, Product (and Brand when the sheet
      has a customer) fill from it. Human header fields typed: Scoop size,
      Servings/Unit, Quote ID, Jar/Lid (the template's two options), Servings
      (base). Actives and excipients picked from Warehouse/Lab like the Flavor
      Sheet (label claim mg, Activity/Overage via F2d); Part # and Price/kg
      always resolved from the DB (`record_line_catalog`), a missing price
      shown blank and flagged. The profile's flavor lines append after the
      excipients automatically. Saved and reopenable; a "Download Sample Record
      Sheet" button renders it. Excipient overflow grows the sheet (F2c2).
      Flag (don't block) when the profile's BASE mg ≠ the actives'
      label-claim total.
      DoD: usable in the app end-to-end — a record sheet built from a synthetic
      flavor profile, saved, reopened and downloaded; `pytest` covers the save
      tables, the auto-fill and the BASE check.
- [ ] **F2f. Paste actives from the PL Cost Sheet.** A paste box on the F2e
      tab: user copies a formula table from the Google Sheet (TSV on the
      clipboard), the app parses Material, Label Claim, Activity, Overage,
      matches each material to the DB, and shows unmatched rows for the user
      to resolve by picking (never auto-merged — section 5). A confirmed match
      is remembered so the same spelling resolves next time. Pasted Activity/
      Overage feed F2d. No Google API — paste only. Needs
      `data/real/templates/pl_cost_paste.txt` for the column shape; commit a
      synthetic fixture of that shape, never the real rows.
      DoD: usable in the app; `pytest` covers parsing the synthetic paste,
      matched/unmatched handling, and the remembered match.
- [ ] **F2g. Import a manager-built record sheet (.xlsx).** Upload on the F2e
      tab: read header, actives (rows 12–34) and excipients (rows 37–48) into
      the record, then render fresh from the template — never write into the
      uploaded file. Part # / Price/kg re-resolved from the DB; Activity and
      Overage taken from the file (and fed to F2d); rows whose material doesn't
      match go to the same resolve step as F2f. Needs the real manager sheet in
      `data/real/templates/` to confirm it follows the template's rows.
      DoD: usable in the app; `pytest` covers import from a synthetic manager
      sheet, flavor lines appended after its excipients, and excipient overflow
      growing the sheet (F2c2).
      *2026-09-24: F2i chose a copy block over writing into the manager's
      file, partly because openpyxl alters a workbook it saves — weigh that
      here too when this is unparked.*

### Phase G — Online and multi-user (gated: do not start unprompted)

Today the app is local and read-only. This is the path to several people in the
company using it from a browser. It is written down so the shape is agreed in
advance — not so it gets built next. (Written 2026-09-08 as "Phase F"; renamed
to G when Phase F was reused for the formula builder.)

**The daily automation must not open a box in this phase.** Every task here
either spends money, puts company data outside the company, or changes who is
allowed to see prices. Those are human decisions. Treat this section the way
Parked is treated, until someone explicitly moves a task out of it.

Two assumptions corrected up front, so they are not re-litigated later:

- **Netlify and Vercel cannot host this.** Not a cost limit, an architectural
  one. They serve static files and short-lived serverless functions; Streamlit
  is a long-running process holding an open WebSocket to every connected
  browser. No configuration makes that fit.
- **Docker is not step one.** It is G6. Streamlit Community Cloud runs an app
  straight from a GitHub repo with no container at all.

- [ ] **G0. Real sheet access.** Everything below is worthless while the ETL
      reads a personal copy of the inventory sheet. Multi-user access to stale
      data is worse than single-user access to stale data, because now other
      people trust it. Same root problem as **Shared database on a synced
      drive** in the Backlog, stated larger.
      DoD: `--source sheets` reads the company's live sheet, not a copy of it.

- [ ] **G1. Read-only deploy to Streamlit Community Cloud.** Free, and no
      container. Do not upload `db/materials.db`; put the service account key in
      Streamlit's secrets manager and have the app run the loaders on startup
      into a throwaway local SQLite. The sheet stays the source of truth, so a
      disposable filesystem costs nothing.
      *Blocked on written permission to put real material names, prices and
      suppliers on a third-party service under a personal account. Ask before,
      not after. If the answer is no, skip to G6 and host inside the company
      tenant — a slower start, not a harder one.*
      DoD: reachable by URL, showing current data; no database file and no key
      in the repo.

- [ ] **G2. Restrict who can open it.** Streamlit Community Cloud supports
      private apps with a viewer allowlist by email. Crude — a hand-kept list,
      not a permission system — but free, ten minutes, and enough for a handful
      of colleagues. Check the current free-tier limits; they change.
      DoD: an uninvited account cannot open the app.

- [ ] **G3. Real login, and a users table.** `st.login` / `st.user` (native
      OIDC, present in the pinned Streamlit) pointed at Microsoft Entra ID,
      since the company already runs Microsoft 365. People sign in with the work
      account they already have and no password ever reaches this app. Then a
      `users` table — email, role, active — checked on every login: an address
      off the company domain, or absent from the table, gets nothing. This is
      where "verified in a database" actually happens.
      *Roles pay for themselves while still read-only. Pricing is usually
      purchasing's to see, and a role check is what lets everyone else use the
      tool without it.*
      DoD: sign-in works with a company account; an unlisted address is refused;
      price columns hidden from roles without them.

- [ ] **G4. Postgres.** Section 5 says SQLite is right until there are
      concurrent writers. G5 is exactly that trigger, so this is the spec
      working as designed rather than being overridden. The schema is plain SQL
      and was written to port nearly verbatim. Startup rebuilds from the sheet
      stop here: the database begins holding data of its own.
      DoD: pipeline, app and tests run against Postgres; SQLite still works for
      local development.

- [ ] **G5. Write access.** The mechanics are the easy part: roles from G3
      decide who sees an edit control, and every change writes an audit row —
      who, what, before, after, when. Never an edit without one.
      **Settle this before building it, because the hard part is not
      technical.** Today the sheet is the system of record and this database is
      a copy. The first edit made in the app creates two systems of record that
      disagree. Either the app writes back to the sheet and the sheet stays
      authoritative (section 5 currently forbids that), or the sheet is retired
      and the app becomes authoritative (not a decision one developer makes
      alone). There is no third option in which both are true. Most internal
      tools die precisely here.
      DoD: the system-of-record decision is written down and agreed *first*;
      then edits work, are permissioned, and are audited.

- [ ] **G6. Container and real hosting, once the free tier is outgrown.** Now
      Docker earns its place. Given the company already runs Microsoft 365,
      Azure App Service is the natural home: data stays in the company tenant,
      Entra login is native, and IT can own it. Render or Fly.io are cheaper if
      this stays a personal project.
      DoD: one command builds the image; the hosted app matches local behavior.

## 5. Explicitly out of scope (do not build)

- No Postgres/MySQL — SQLite is correct at this scale (revisit only for
  concurrent writers). Phase G5 (write access) is the one thing that would
  create them; G4 is that revisit, and it does not happen on its own.
- No auth, hosting, or Docker — the app runs locally on demand. Phase G is the
  written shape of what going online would mean, and it stays gated: having a
  plan is not permission to execute it. The point of writing it down was to stop
  the project drifting online one convenient step at a time.
- No write-back to the Google Sheet — one-way ingestion only. The sheet stays
  the operational system of record until the company decides otherwise; that is
  G5's blocking question, and it is an organizational decision rather than a
  technical one.
- No incremental/CDC loading — full reload is idempotent and fast at hundreds
  of rows (revisit at ~50k, not before).
- No auto-merge of fuzzy supplier matches, no auto-resolution of part-#
  conflicts — humans resolve, the report flags.
- No invented stock splits across locations — the source doesn't record it.
- No ORM, no web framework beyond Streamlit, no dashboarding suite.
- No scheduled ETL daemon — run on demand. Both G1 (refresh on app startup) and
  the shared database in the Backlog would revisit this — deliberately, as their
  own entry, not as a side effect of something else.

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
   checklists (section 4 Status, then Parked), top to bottom, Phase A→B→E→D→F,
   skipping Parked and Phase G. A task covered by an open PR is done.
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

**Nothing outside the checklists is a queue.** Parked Phase C and the Backlog
are held deliberately and carry no `- [ ]` boxes for a reason; never promote
work out of them. Moving something into a checklist is the user's call, in a
reviewed PR (as Phase F was on 2026-09-21).

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

## Phase F design reference (formula builder + sample documentation)

Promoted into section 4 on 2026-09-21 at the user's request; the checklist
(F1–F4) lives there, this is the load-bearing design behind it — **implement it,
don't redesign it** (same status as the Phase E match rule). Written down so the
direction survives and the questions in front of it are asked once, not
rediscovered mid-build.

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
  *Exception (user's call, 2026-09-24): the Sample Record Sheet.* The manager
  builds it by hand from the PL Cost Sheet, so the app hands over a copy
  block for its flavor lines (F2i) instead of filling a file. The block puts
  a value in F over the section's formula; identical while flavor lines are
  Activity 1 / Overage 0, stale if someone edits D/E on those rows after.
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
  it); `lab_sample_id` did not — it was a full-reload surrogate. **Resolved
  (F0):** lab samples now have a stable, human-maintained `RD-ID`
  (`RD-0000`..`RD-9999`) in the lab sheet, and the loader upserts on it instead
  of full-reloading, so `lab_sample_id` stays put across runs. Identity is a
  third namespace, not Sample Code (§4(a): collides) and not the Part #
  (company-owned, not ours to mint); a sample can hold both. See
  `docs/flavor_sample_sheet_layout.md` §2(a). **F1** fixes the formula-line FK:
  `rd_id` for lab samples (the more rebuild-robust handle) and `material_id` for
  adopted materials — never a copied name or price.
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

**Prerequisite for the renderers (F3 → F4).** Copies of the three real templates
and a reverse-engineering pass on each — which cells are inputs, which formulas,
which formatting, what the autocalc computes, and which inputs the DB already
supplies vs. a human types. The formula object and builder (F1–F2) don't need it
and come first ("build the object first"); no renderer code until this map
exists (same step E1 was). F3 is blocked until the user supplies the three real
templates.

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
