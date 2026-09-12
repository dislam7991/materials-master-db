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

from dtf_materials import queries as q
from dtf_materials.db import DEFAULT_DB_PATH

st.set_page_config(page_title="Materials Master", page_icon="~", layout="wide")


@st.cache_resource
def get_conn() -> sqlite3.Connection:
    """One connection reused across reruns. check_same_thread=False because
    Streamlit reruns scripts on its own threads; safe here since the app only
    ever reads."""
    conn = sqlite3.connect(DEFAULT_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
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

tab_combined, tab_material, tab_location, tab_all, tab_lab = st.tabs(
    ["Warehouse + Lab", "Material lookup", "What's in a location",
     "All materials", "Lab Samples"]
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
