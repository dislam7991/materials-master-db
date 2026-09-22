"""Streamlit lookup app for the materials master database.

The everyday R&D question this answers: "I have a part number (or half a
material name) — where is it, what does it cost, who supplies it?"

All data access lives in dtf_materials/queries.py; this file is only UI.
Run with:  streamlit run app.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_searchbox import st_searchbox

from dtf_materials import formulas as f
from dtf_materials import queries as q
from dtf_materials.db import DEFAULT_DB_PATH

st.set_page_config(page_title="Materials Master", page_icon="~", layout="wide")


@st.cache_resource
def get_conn() -> sqlite3.Connection:
    """One connection reused across reruns. check_same_thread=False because
    Streamlit reruns scripts on its own threads.

    The lookup tabs only read; the Formula builder tab writes through
    dtf_materials.formulas, which commits its own inserts. That's a single
    local writer — the same single-writer assumption the project chose SQLite
    on — so one shared connection is still correct. foreign_keys is ON so a
    formula line can only reference a material or lab sample that exists (the
    builder never lets one be typed, but the constraint is the real guarantee)."""
    conn = sqlite3.connect(DEFAULT_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def money(value) -> str:
    return "—" if value is None else f"${value:,.2f}"


def rows_to_df(rows, columns: dict[str, str]) -> pd.DataFrame:
    """sqlite3.Rows -> DataFrame with friendly column names, keeping only the
    columns we mean to show."""
    data = [{label: r[key] for key, label in columns.items()} for r in rows]
    return pd.DataFrame(data, columns=list(columns.values()))


# Shown when the lab catalog hasn't been loaded. Flush-left because
# Streamlit renders this as markdown, and an indented line would become a
# code block. Kept as a constant rather than inline for the same reason.
NO_LAB_SAMPLES_MESSAGE = """
**No lab samples have been loaded into this database.**

The catalog lives in its own Google Sheet and is loaded by its own command,
separately from the warehouse inventory — so this tab can be empty while
everything above is full.

If a colleague gave you this database, ask them for an updated copy: the lab
samples travel inside the database file.

To load it yourself, add a `[lab_sheet]` section to `config.local.toml` (see
`config.example.toml`), then run:

```
python -m dtf_materials.lab_samples
```

There is no synthetic stand-in for this sheet, so a database built only from
the sample data will always show this message.
"""


if not Path(DEFAULT_DB_PATH).exists():
    st.error(
        f"No database at `{DEFAULT_DB_PATH}`.\n\n"
        "Build it first:\n\n"
        "```\npython scripts/generate_synthetic_sheet.py\npython -m dtf_materials.etl\n```"
    )
    st.stop()

conn = get_conn()
summary = q.database_summary(conn)

st.title("Materials Master Database")

stat_materials, stat_lots, stat_suppliers, stat_locations = st.columns(4)
stat_materials.metric("Materials", f"{summary['materials']:,}")
stat_lots.metric("Lots", f"{summary['lots']:,}")
stat_suppliers.metric("Supplier Spellings", f"{summary['suppliers']:,}")
stat_locations.metric("Locations", f"{summary['locations']:,}")

(tab_combined, tab_material, tab_location, tab_all, tab_lab,
 tab_formula) = st.tabs(
    ["Warehouse + Lab", "Material lookup", "What's in a location",
     "All materials", "Lab Samples", "Formula builder"]
)

with tab_combined:
    st.write(
        "One search over both catalogs, for when you don't yet know which one "
        "holds the answer: is this in the warehouse, in the lab, or both?"
    )

    if summary["lab_samples"] == 0:
        # Not an error state: the lab catalog comes from a different sheet
        # loaded by a different command, and a synthetic-only database never
        # has one. Results are still useful, just warehouse-only — say that
        # rather than letting every row read as "not in the lab".
        st.caption(
            "No lab samples are loaded in this database, so every result here "
            "can only come from the warehouse. See the **Lab Samples** tab."
        )

    combined_term = st.text_input(
        "Search by part number, material or flavor name, sample code, or vendor",
        placeholder="e.g. 15-009, mango, 7182011, sensapure",
        key="combined",
    ).strip()

    if not combined_term:
        st.info("Type anything either catalog might know it by.")
    else:
        found = q.search_warehouse_and_lab(conn, combined_term, limit=25)
        if not found:
            st.warning(f"Nothing matching “{combined_term}” in the warehouse or the lab.")
        else:
            table = []
            for result in found:
                # Each side's summary comes from the same queries the
                # single-source tabs use, so the two can't drift apart.
                warehouse = {"Stock": None, "Stocked locations": "—"}
                if result["material_id"] is not None:
                    stocked = q.get_stocked_locations(conn, result["material_id"])
                    warehouse = {
                        "Stock": q.total_stock(conn, result["material_id"]),
                        # Listed, not summed — this is where the material can
                        # be found, and no quantity is attached to it here.
                        "Stocked locations": ", ".join(r["location"] for r in stocked) or "—",
                    }

                lab = {"Lab location": "—", "Declaration": "—"}
                if result["lab_sample_id"] is not None:
                    sample = q.get_lab_sample(conn, result["lab_sample_id"])
                    lab = {
                        "Lab location": sample["location_lab"] or "—",
                        "Declaration": sample["declaration_type"] or "—",
                    }

                table.append({
                    "Where": result["kind"],
                    "Part #": result["dtf_part_num"] or "—",
                    "Material": result["material_name"] or "—",
                    **warehouse,
                    "Vendor": result["vendor"] or "—",
                    "Flavor": result["flavor_name"] or "—",
                    "Sample code": result["sample_code"] or "—",
                    **lab,
                    "Matched by": result["match_rule"] or "—",
                })

            st.dataframe(
                pd.DataFrame(table), hide_index=True, width="stretch",
                column_config={"Stock": st.column_config.NumberColumn(format="%.2f")},
            )
            st.caption(
                "**Both** means one row linked to the other by Part #, or by its "
                "sample code appearing in the warehouse material's name — "
                "“Matched by” says which. Unlinked is normal: most lab samples "
                "have never been adopted into inventory. This is a summary; the "
                "**Material lookup** tab has lot history, price over time and "
                "full stock reconciliation, and the **Lab Samples** tab has the "
                "vendor and sensory detail."
            )

with tab_material:
    def _material_options(term: str):
        return [
            (f"{r['dtf_part_num'] or '(no part #)'} — {r['material_name']}", r["material_id"])
            for r in q.search_materials(conn, term, limit=10)
        ]

    material_id = st_searchbox(
        _material_options,
        label="Search by DTF Part # or material name",
        placeholder="e.g. 15-009, caffeine, blueberry",
        key="material_searchbox",
    )

    if not material_id:
        st.info("Type a part number or part of a material name to begin.")
    else:
        material = q.get_material(conn, material_id)

        st.subheader(f"{material['dtf_part_num'] or '(no part #)'} — {material['material_name']}")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Price / kilo", money(material["current_price_per_kilo"]))
        c2.metric("Supplier", material["supplier"] or "—")
        c3.metric("Category", (material["category"] or "—").title())
        c4.metric("Allergen", material["allergen"] or "—")

        st.markdown("#### Where it is")
        stocked = q.get_stocked_locations(conn, material["material_id"])
        if stocked:
            st.dataframe(
                rows_to_df(stocked, {
                    "location": "Location",
                    "stock": "Stock in lots here",
                    "lot_count": "Lots",
                }),
                hide_index=True, width="stretch",
            )
            st.caption(f"Total stock on hand: **{q.total_stock(conn, material['material_id']):,.2f}**")

            missing = q.unlocated_stock(conn, material["material_id"])
            if missing:
                st.warning(
                    f"**{missing:,.2f}** of the total is in lots with no location recorded "
                    "in the sheet — it is counted above but isn't in the table, because "
                    "nobody wrote down where it went."
                )

            if any(r["lot_spans_locations"] for r in stocked):
                st.caption(
                    ":warning: A lot here is stored across several locations. The sheet "
                    "records one quantity per lot and never how it splits between them, so "
                    "that lot's full quantity appears on each of its rows — don't add the "
                    "rows up. The total above is summed per lot and is correct."
                )
        else:
            # Nothing in stock — show the last known location rather than
            # a blank panel, clearly labelled so it isn't mistaken for
            # current inventory.
            last = q.get_last_known_locations(conn, material["material_id"])
            if last:
                locs = ", ".join(r["location"] for r in last)
                when = last[0]["receiving_date"] or "unknown date"
                st.warning(
                    f"No stock on hand. Last known location: **{locs}** "
                    f"(lot {last[0]['dtf_lot_num'] or '—'}, received {when})."
                )
            else:
                st.warning("No stock on hand, and no location recorded on any lot.")

        st.markdown("#### Lot history")
        lots = q.get_lots(conn, material["material_id"])
        st.dataframe(
            rows_to_df(lots, {
                "receiving_date": "Received",
                "dtf_lot_num": "DTF Lot #",
                "supplier_lot_num": "Supplier Lot",
                "exp_date": "Expires",
                "locations": "Location(s)",
                "current_stock": "Stock",
                "price_per_kilo": "Price/kg",
                "status": "Status",
            }),
            hide_index=True, width="stretch",
        )

        history = q.price_history(conn, material["material_id"])
        if len(history) > 1:
            st.markdown("#### Price per kilo over time")
            st.caption(
                "Recorded per lot, so repricing between receivings is visible "
                "instead of being overwritten."
            )
            df = pd.DataFrame(
                [{"Received": r["receiving_date"], "Price/kg": r["price_per_kilo"]}
                 for r in history]
            ).set_index("Received")
            st.line_chart(df)

with tab_location:
    st.write("Look up a full location code, or an aisle prefix to see everything in it.")
    loc = st.text_input(
        "Location", placeholder="e.g. 6L-27-D, or just 6L", key="loc"
    ).strip()

    if not loc:
        st.info("Enter a location code or prefix.")
    else:
        found = q.search_by_location(conn, loc)
        if not found:
            st.warning(f"Nothing in stock at “{loc.upper()}”.")
        else:
            st.write(f"**{len(found)} items at or under {loc.upper()}**")
            shelf = rows_to_df(found, {
                "location": "Location",
                "dtf_part_num": "Part #",
                "material_name": "Material",
                "current_stock": "Stock",
                "dtf_lot_num": "DTF Lot #",
                "exp_date": "Expires",
                "ready_to_archive": "Status",
            })
            shelf["Status"] = shelf["Status"].map(lambda flag: "Archived" if flag else "")
            st.dataframe(shelf, hide_index=True, width="stretch")

            archived = sum(1 for r in found if r["ready_to_archive"])
            if archived:
                subject = "One of these is" if archived == 1 else f"{archived} of these are"
                st.caption(
                    f"{subject} flagged Ready To Archive in the sheet. "
                    "They are listed because they are still physically on the shelf, "
                    "but they are not counted as available stock on the material's "
                    "own page."
                )

with tab_all:
    st.write(
        "Every material in the database. Click a column header to sort, or use "
        "the search icon in the table's own toolbar to filter."
    )
    materials = q.list_materials(conn)
    st.dataframe(
        pd.DataFrame([
            {
                "Part #": r["dtf_part_num"] or "—",
                "Material": r["material_name"],
                "Category": (r["category"] or "—").title(),
                "Supplier": r["supplier"] or "—",
                "Price/kg": r["current_price_per_kilo"],
                "Allergen": r["allergen"] or "—",
                "Stock on hand": r["total_stock"],
            }
            for r in materials
        ]),
        hide_index=True, width="stretch",
        column_config={
            # Numbers stay numbers so sorting works right; only the display
            # is formatted (see money(): "$xx.xx" strings sort wrong, e.g.
            # "$100" before "$20").
            "Price/kg": st.column_config.NumberColumn(format="$%.2f"),
            "Stock on hand": st.column_config.NumberColumn(format="%.2f"),
        },
    )

with tab_lab:
    st.write(
        "The R&D lab's flavor sample catalog — a separate sheet from the "
        "warehouse inventory above, so results here are independent of it."
    )

    if summary["lab_samples"] == 0:
        # An empty catalog is a normal state, not a failure: it comes from a
        # different sheet loaded by a different command, and a
        # synthetic-only database never has any. Say so, rather than letting
        # every search quietly return nothing and read as broken.
        st.info(NO_LAB_SAMPLES_MESSAGE)
    else:

        def _lab_sample_options(term: str):
            return [
                (
                    f"{r['sample_code'] or '(no code)'} — {r['flavor_name'] or '(unnamed)'} "
                    f"({r['vendor'] or 'unknown vendor'})",
                    r["lab_sample_id"],
                )
                for r in q.search_lab_samples(conn, term, limit=10)
            ]

        lab_sample_id = st_searchbox(
            _lab_sample_options,
            label="Search by vendor, flavor name, sample code, or Part #",
            placeholder="e.g. 7182011, mango, sensapure",
            key="lab_sample_searchbox",
        )

        if not lab_sample_id:
            st.info("Type a vendor, flavor name, sample code, or part number to begin.")
        else:
            sample = q.get_lab_sample(conn, lab_sample_id)

            st.subheader(f"{sample['sample_code'] or '(no code)'} — {sample['flavor_name'] or '(unnamed)'}")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Vendor", sample["vendor"] or "—")
            c2.metric("Declaration Type", sample["declaration_type"] or "—")
            c3.metric("Lab Location", sample["location_lab"] or "—")
            c4.metric("Price / kilo", money(sample["price_per_kilo"]))

            st.markdown("#### Sample details")
            st.caption(
                "Most of these fields are sparse right now and fill in over time — "
                "blank means not recorded yet, not missing data."
            )
            detail_fields = {
                "Flavor Family": sample["flavor_family"],
                "Category/Subcategory": sample["category_subcategory"],
                "Usage Level": sample["usage_level"],
                "Dry Aroma Descriptors": sample["dry_aroma_descriptors"],
                "Aroma Intensity": sample["aroma_intensity"],
                "Top Note": sample["top_note"],
                "Mid Palate Character": sample["mid_palate_character"],
                "Finish Note": sample["finish_note"],
                "Off Note Tendency": sample["off_note_tendency"],
                "Matrix Performance": sample["matrix_performance"],
                "Best Pairings": sample["best_pairings"],
                "Tested In": sample["tested_in"],
                "Allergens": sample["allergens"],
                "Date Received": sample["date_received"],
                "DTF Part #": sample["dtf_part_num"],
            }
            st.dataframe(
                pd.DataFrame([{"Field": k, "Value": v or "—"} for k, v in detail_fields.items()]),
                hide_index=True, width="stretch",
            )

with tab_formula:
    st.write(
        "Build a formula by picking materials from the warehouse or lab "
        "catalogs and entering how much of each. Materials are **chosen, never "
        "typed** — a free-typed name would put an unbacked price in the "
        "database. The only authored values are the header (name, batch size), "
        "which rows, and how much of each. Names and prices are read live from "
        "the catalog, so a repriced material updates every formula that uses it."
    )

    # 1. Pick an existing formula or start a new one. The selectbox carries the
    #    active formula across reruns (each Add reruns the script), so a
    #    just-created formula stays selected while its lines are added.
    NEW_FORMULA = "➕ Start a new formula…"
    saved = f.list_formulas(conn)
    saved_labels = {
        row["formula_id"]: (
            f"{row['name']} — {row['line_count']} "
            f"line{'s' if row['line_count'] != 1 else ''}"
        )
        for row in saved
    }
    choice = st.selectbox(
        "Formula",
        [NEW_FORMULA] + [row["formula_id"] for row in saved],
        format_func=lambda c: NEW_FORMULA if c == NEW_FORMULA else saved_labels[c],
        key="formula_choice",
    )

    if choice == NEW_FORMULA:
        with st.form("new_formula"):
            st.caption(
                "Header fields — the only free-typed values a formula carries; "
                "no catalog holds them."
            )
            new_name = st.text_input("Formula name", placeholder="e.g. Strawberry Base v2")
            col_size, col_unit = st.columns(2)
            new_batch_size = col_size.number_input(
                "Batch size", min_value=0.0, value=0.0, step=0.1
            )
            new_batch_unit = col_unit.text_input("Batch unit", placeholder="e.g. kg")
            new_notes = st.text_area("Notes", placeholder="Optional")
            created = st.form_submit_button("Create formula")
        if created:
            if not new_name.strip():
                st.error("A formula needs a name.")
            else:
                new_id = f.create_formula(
                    conn,
                    new_name.strip(),
                    # A blank batch stays NULL rather than a fabricated 0 — the
                    # sheet doesn't record "zero batch", it records nothing yet.
                    batch_size=new_batch_size or None,
                    batch_unit=new_batch_unit.strip() or None,
                    notes=new_notes.strip() or None,
                )
                st.session_state["formula_choice"] = new_id
                st.rerun()
    else:
        formula_id = choice
        formula = f.get_formula(conn, formula_id)

        st.subheader(formula["name"])
        if formula["batch_size"] is not None:
            st.caption(f"Batch: {formula['batch_size']:g} {formula['batch_unit'] or ''}".strip())
        if formula["notes"]:
            st.caption(formula["notes"])

        # 2. Add a line. The material comes from a catalog search and is picked,
        #    never typed; only the amount and unit are entered here.
        st.markdown("#### Add a material")
        catalog = st.radio(
            "Catalog", ["Warehouse", "Lab"], horizontal=True, key="formula_catalog"
        )

        picked = None
        if catalog == "Warehouse":
            def _formula_wh_options(term: str):
                return [
                    (
                        f"{r['dtf_part_num'] or '(no part #)'} — {r['material_name']} "
                        f"({money(r['current_price_per_kilo'])}/kg)",
                        ("material", r["material_id"]),
                    )
                    for r in q.search_materials(conn, term, limit=10)
                ]

            picked = st_searchbox(
                _formula_wh_options,
                label="Search the warehouse by Part # or name",
                placeholder="e.g. 15-009, caffeine",
                key="formula_wh_box",
            )
        elif summary["lab_samples"] == 0:
            # Same honest degrade as the other lab-aware tabs: a synthetic-only
            # database has no lab catalog, so there's nothing to pick there.
            st.info(
                "No lab samples are loaded in this database, so only warehouse "
                "materials can be picked. See the **Lab Samples** tab."
            )
        else:
            def _formula_lab_options(term: str):
                return [
                    (
                        f"{r['sample_code'] or '(no code)'} — "
                        f"{r['flavor_name'] or '(unnamed)'} "
                        f"({r['vendor'] or 'unknown vendor'})",
                        ("lab", r["lab_sample_id"]),
                    )
                    for r in q.search_lab_samples(conn, term, limit=10)
                ]

            picked = st_searchbox(
                _formula_lab_options,
                label="Search the lab catalog",
                placeholder="e.g. 7182011, mango, sensapure",
                key="formula_lab_box",
            )

        col_amt, col_u, col_add = st.columns([2, 1, 1])
        amount = col_amt.number_input(
            "Amount", min_value=0.0, value=0.0, step=0.1, key="formula_amount"
        )
        line_unit = col_u.text_input("Unit", placeholder="e.g. kg", key="formula_unit")
        col_add.markdown("<div style='height: 1.8em'></div>", unsafe_allow_html=True)
        add_clicked = col_add.button("Add to formula", disabled=not picked)

        if add_clicked and picked:
            kind, row_id = picked
            amt = amount or None  # blank amount stays NULL, not an invented 0
            unit = line_unit.strip() or None
            if kind == "material":
                f.add_material_line(conn, formula_id, row_id, amount=amt, unit=unit)
                st.rerun()
            else:
                sample = q.get_lab_sample(conn, row_id)
                if not sample["rd_id"]:
                    # A formula line references a lab sample by its stable RD-ID
                    # (F1); a sample without one can't be pointed at yet.
                    st.error(
                        "This lab sample has no RD-ID, so a formula line can't "
                        "reference it. Give it a stable RD-ID in the lab sheet first."
                    )
                else:
                    f.add_lab_sample_line(
                        conn, formula_id, sample["rd_id"], amount=amt, unit=unit
                    )
                    st.rerun()

        # 3. View and total. Names and prices are resolved from the catalog by
        #    get_lines, never stored on the line.
        st.markdown("#### The formula")
        lines = f.get_lines(conn, formula_id)
        if not lines:
            st.info("No lines yet. Search a catalog above and add a material.")
        else:
            st.dataframe(
                pd.DataFrame([
                    {
                        "Source": "Lab" if line["rd_id"] else "Warehouse",
                        "Material": line["name"] or "—",
                        "Amount": line["amount"],
                        "Unit": line["unit"] or "—",
                        "Price/kg": line["price_per_kilo"],
                        "Line cost": line["line_cost"],
                    }
                    for line in lines
                ]),
                hide_index=True, width="stretch",
                column_config={
                    "Amount": st.column_config.NumberColumn(format="%.3f"),
                    "Price/kg": st.column_config.NumberColumn(format="$%.2f"),
                    "Line cost": st.column_config.NumberColumn(format="$%.2f"),
                },
            )

            st.metric("Formula total", money(f.formula_total(conn, formula_id)))
            uncosted = sum(1 for line in lines if line["price_per_kilo"] is None)
            if uncosted:
                subject = "line has" if uncosted == 1 else "lines have"
                st.caption(
                    f":warning: {uncosted} {subject} no recorded price and are left "
                    "out of the total — it's a floor, not the full cost. A missing "
                    "price is shown blank rather than guessed (SPEC §2)."
                )
