# Phase F Templates — Layout Map (F3)

Reverse-engineering pass on the three company templates a finished sample
needs (SPEC "Direction — Phase F", the prerequisite that blocks all Phase F
code). Same role as `flavor_sample_sheet_layout.md` was for Phase E: cell
addresses, formulas and roles only. No customer, product, material or price
from the templates is reproduced here.

Originals live in `data/real/templates/` (gitignored). Source of the copies:
the user's OneDrive Desktop, 2026-09-21.

| Template | File format | Macros | Sheets / parts |
|---|---|---|---|
| Sample Record Sheet | `.xlsx` (OOXML, SharePoint content type) | **none** (no `vbaProject.bin`) | Sheet1 (the form), Sheet2 (empty), Sheet3 (stub) |
| Flavor Sheet | `.xlsx` (OOXML, SharePoint content type) | **none** | Sheet1 (form), Sheet2 (older variant) |
| Sample Labels | **`.doc`** — Word 97-2003 binary, not Excel | **none** (`HasVBProject = False`) | one page |

SPEC question 3 answered: **no macros anywhere.** Question 8 answered in
part: labels are already Word, not a spreadsheet.

Role legend used below — **DB**: the database already holds it; **Human**:
typed per sample, no catalog has it; **Formula**: the sheet computes it, the
renderer must not write it; **Static**: template text, never touched.

## 1. Sample Record Sheet (`Sheet1`)

Landscape, one formula per sample. Columns U:V are **hidden** and hold the
jar dropdown source.

### Header block

| Cell | Label (col A / D) | Role | Notes |
|---|---|---|---|
| B3 (B3:C3) | Brand | Human | nearest DB field is `samples.client`, not populated today |
| B4 (B4:C4) | Product | Human | |
| B5 (B5:C5) | Flavor | Human | |
| B6 (B6:C6) | Sample code | Human (SPEC Q7) | shape `<prefix>YYMMDD-NN` |
| B7 (B7:C7) | Notes | Human | free text (powder colour etc.) |
| E3 (E3:F3) | Scoop Size | Human | text, e.g. `11cc` |
| E4 (E4:F4) | Servings/Unit | Human | **drives every cost** — blank → `Missing Data` |
| E5 (E5:F5) | Quote ID | Human | shape `<prefix>-MMDDYY` — note: different date order from the sample code |
| E6 (E6:F6) | Jar/Lid Size | Human, dropdown | data validation list `=$U$4:$U$5` (2 jar sizes) |
| H9 | Servings(base) (label G9) | Human | batch size in servings; blank in template → all g/run = 0 |

### Formula lines

Two sections, fixed capacity:

- **Actives** rows **12–34** (23 lines), subtotal row 35.
- **Flavor System and Excipients** rows **37–48** (12 lines), subtotal row 49.
- Totals row 50.

| Col | Header | Role | Formula / source |
|---|---|---|---|
| A | Part Number | DB | `materials.dtf_part_num` / `lab_samples.dtf_part_num`; blank for lab-only samples |
| B | Raw Material | DB | `material_name` / `flavor_name` (text-formatted `@`) |
| C | Label Claim (mg) | **Human** | the formula amount, mg per serving |
| D | Activity | Human (today) | fraction, e.g. 0.20 for a 20%-elemental salt. Not in the DB; a candidate material attribute later |
| E | Overage | Human | fraction, 0.10 typical for actives, 0 for excipients |
| F | Actual Input | Formula | `=IFERROR(C*(1+E)/D, 0)` — **but see finding 1** |
| G | g/run | Formula | `=(F*$H$9)/1000` |
| H | Formula % | Formula | `=IFERROR((C/$F$50%)/100,0)` = C ÷ F50 — **see finding 2** |
| I | Price/kg | DB | `current_price_per_kilo` / `lab_samples.price_per_kilo` |
| J | Cost/Unit | Formula | actives `=(F/1e6)*I*$E$4`; excipients `=(C/1e6)*I*$E$4` — **see finding 3** |
| K | kg/run | Formula | `=G/1000` |

### Totals and cost panel (all Formula)

| Cell | Meaning | Formula |
|---|---|---|
| J35 | Active ingredient cost / unit | `=SUM(J12:J34)` |
| J49 | Inactive ingredient cost / unit | `=SUM(J37:J48)` |
| F50 / G50 / H50 | Totals: mg per serving, g per run, % | `SUM` over rows 12–48 |
| J50 | Ingredient cost / unit | `=J35+J49` |
| M5 | Actives cost / serving | `=IFERROR(J35/$E$4,"Missing Data")` |
| N5 | Inactives cost / serving | `=IFERROR(J49/$E$4,"Missing Data")` |
| O5 | Total # of servings | `=$E$4` |
| P5 | Jar/lid cost | dynamic-array `IFS` on E6: each of the two jar sizes maps to a fixed cost, else a prompt string. **Jar prices are hard-coded in the formula.** |
| Q5 | Total cost / unit | `=IFERROR(M5*O5+N5*O5+P5,"Missing Data")` |

Formatting: conditional-format banding `MOD(ROW(),2)=0` on the line ranges;
accounting formats on money cells; `0%` on D/E/H.

`Sheet2` is empty. `Sheet3` is a stub (`Testing`, `pH:`, `Density:` labels,
no values) — Human, filled after lab testing if at all.

### Findings (template defects — owner decides, the tool must not paper over them)

1. **F12:F18 are pasted values, not formulas.** Rows 19–48 carry
   `=IFERROR(C*(1+E)/D,0)`; the first seven active rows hold literal numbers
   that match that formula for the example data. A renderer that writes C/D/E
   there gets a stale F, and every cost, % and g/run downstream is wrong
   without any visible error. Fix in the template (restore the formula), not
   in code.
2. **Formula % divides label claim by total actual input.** `H = C / F50`
   mixes pre-overage mg with post-overage mg; the cached H50 in the template
   is **56.65%**, not 100%. Probably meant `F / F50`.
3. **Actives cost on F, excipients cost on C.** Identical while excipients
   keep Activity 1 / Overage 0 (they all do in the example); diverges the
   moment someone sets an excipient overage.
4. **A blank Price/kg costs $0, silently.** The example's flavor lines have
   no price; `J` treats blank I as 0 and the total looks complete. The DB
   side must leave the cell blank and flag it, never write 0.
5. **Fixed capacity.** 23 active + 12 excipient lines. Inserting rows breaks
   the `SUM` ranges and banding; the renderer must refuse a formula that
   doesn't fit rather than grow the sheet.
6. **The "blank" template isn't blank** — it carries a full example formula
   (header, 16 lines, prices). The renderer must clear A:E and I on rows
   12–34 / 37–48 and the header inputs before writing, or be given a truly
   empty copy.

## 2. Flavor Sheet (`Sheet1`)

> **Template revised 2026-09-22** (the copy in `data/real/templates/` was
> replaced; the old one is kept alongside as `(2026-09-21 copy)`): example
> data is now generic placeholders, **all four slots calculate** (g formula on
> rows 8–15 and 23–30), and the top block runs to row 20 (BASE + 12 rows), the
> bottom to row 34 (BASE + 11). Findings 2.1 and 2.6 below describe the old
> copy. The renderer (`dtf_materials/flavor_sheet_xlsx.py`) writes the g
> formula on every line it fills, so the formula's extent in the template no
> longer matters.

Landscape, scale 86%. Bench-scale weigh-up of flavor variants on top of one
base: **four flavor slots** per page, two across × two down.

| Slot | Title cell (merged) | Lines | mg col | g col |
|---|---|---|---|---|
| top-left | B6 (B6:D6) | A8:A17 | B | C |
| top-right | F6 (F6:H6) | E8:E17 | F | G |
| bottom-left | B21 (B21:D21) | A23:A34 | B | C |
| bottom-right | F21 (F21:H21) | E23:E34 | F | G |

### Header

| Cell | Label | Role | Notes |
|---|---|---|---|
| A3 (A3:D4) | `Product Name: ` | Human | **label and value share one merged cell** — the renderer writes `"Product Name: <x>"` as one string |
| F3 | Servings per flavor | Human | **the only cell any formula reads** |
| H3 | Serv. per retain | Human | informational, no formula uses it |
| F4 | Servings per sample | Human | informational, no formula uses it (example: 4 + 1 ≠ 6 — nothing enforces it) |
| H4 | Scoop Size (label G4) | Human | empty in template; same value as record sheet E3 |

### Lines

| Col | Role | Notes |
|---|---|---|
| Title B6/F6/B21/F21 | DB | one string: `<flavor name> <sample code>` |
| A/E | DB | material name. Row 8 is always `BASE` (Static label) |
| B/F | **Human** | mg per serving. BASE's mg is a single figure — plausibly the record sheet's F50, but nothing links them (question below) |
| C/G | Formula | `=B*$F$3/1000` (g to weigh) |

### Findings

1. **Only rows 8–17 of the top two slots calculate.** C/G formulas exist on
   C8:C17 and G8:G17 only. Rows 18–19 and the whole bottom block (rows 23–34)
   have headers but **no formulas** — flavors 3 and 4 get no gram weights.
   Either extend the formulas in the template, or treat the page as two slots
   of 10 lines.
2. **No price, no cost** — the flavor sheet is weights only.
3. **Names differ from the record sheet** for the same material (short
   working names vs. full label names with botanical source). Both can't come
   from one DB field as-is; pick one per document or add a short-name column.
4. **Sheet2 is an older variant** — no formulas at all, servings baked into
   a sentence in E3 (`Make 8 servings … 5 per sample and 3 … retain`), a stray
   `8` in F30. Assumed dead; confirm before deleting it from the copy.
5. Row banding is static fill (`D0CECE`), not conditional — harmless.
6. Also ships with example data (2 flavors, 9 and 10 lines) to clear.

## 3. Sample Labels (`.doc`)

- US Letter portrait, margins T 0.5" / B 0.42" / L 0.24" / R 0.31".
- **2 × 5 grid of 4" × 2" labels** (288 × 144 pt) — Avery 5163/8163 geometry.
  A 5×3 table (middle column is a 0.19" gutter) sets the grid; **10 floating
  rectangle AutoShapes (text boxes)** sit on top of the cells and hold the
  text.
- No mail merge, no fields, no content controls, no form fields, no images.
- Only label 1 has content — five caption lines; labels 2–10 are empty boxes.

| Caption | Role | Source |
|---|---|---|
| CUSTOMER | DB-derived | record sheet B3 (Brand) |
| PRODUCT | DB-derived | record sheet B4 |
| FLAVOR | DB-derived | record sheet B5 |
| SAMPLE ID | DB-derived | record sheet B6 |
| SERVING SCOOP, WEIGHT | DB-derived | record sheet E3 + serving weight = F50 mg → g |

**Nothing on a label is typed** — every value is already in the formula
object once the record sheet's inputs exist. Labels are the cheapest of the
three to automate.

**Format problem:** no Python library writes binary `.doc`. Options:

- **Save As `.docx` once** (Word, by the template owner) and fill the text
  boxes' `w:txbxContent` — keeps "fill, never regenerate". Recommended.
- PDF at the Avery 5163 geometry — right for printing, but a regenerated
  layout (SPEC rejects that for the spreadsheets; for labels it's open, Q8).

## 4. What openpyxl round-trip keeps and drops

Tested: load + save both workbooks unmodified.

- **Kept:** every formula, merges, number formats, column widths, hidden
  columns U:V, data validation, conditional formatting, page orientation and
  scale, the SharePoint `ContentTypeId` custom property.
- **Dropped:** `printerSettings*.bin` (printer-specific paper/tray; page setup
  itself survives), `customXml/*` (SharePoint content-type metadata),
  `calcChain.xml` (Excel rebuilds it), `persons.xml` (threaded-comment
  authors; no comments exist).
- **Downgraded:** P5's dynamic-array flag (`xl/metadata.xml`) is dropped, so
  the `IFS` becomes a legacy single-cell array formula `{=IFS(...)}`. Same
  result; looks different in the formula bar.
- **No cached values:** openpyxl writes formulas without results. Excel
  recalculates on open; anything that previews without calculating (mobile
  previews, `data_only` reads, this app) sees blanks. Any total the *app*
  shows must be computed by the app on purpose, or produced by a headless
  LibreOffice recalc (SPEC: "decide per field").

## 5. Human-typed inputs per sample, all three documents

Brand, Product, Flavor name, Sample code, Quote ID, Notes, Scoop size,
Servings/unit, Jar/lid, Servings (base) — record sheet header.
Servings per flavor / per sample / per retain — flavor sheet header.
Per line: **label claim mg** (record) or **mg/serving** (flavor), Activity,
Overage. Everything else is DB lookup or sheet formula.

Scoop size is typed once and appears on all three documents; brand / product
/ flavor / sample code appear on record sheet + labels (+ flavor sheet
title). One formula object entering them once removes the triple entry.

## 6. Open questions for the template owner

1. Restore the formula in record sheet F12:F18? (finding 1.1)
2. Is Formula % meant to be `F/F50`? (1.2)
3. Should flavor slots 3–4 calculate? (2.1)
4. Is BASE mg on the flavor sheet the record sheet's total mg/serving?
5. Is flavor-sheet Sheet2 dead?
6. Which material name goes on which document — full or short?
7. Can the labels template be re-saved as `.docx`?
8. Jar/lid prices live inside the P5 formula — who updates them, and should
   they move to cells?
