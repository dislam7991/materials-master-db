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


if not Path(DEFAULT_DB_PATH).exists():
    st.error(
        f"No database at `{DEFAULT_DB_PATH}`.\n\n"
        "Build it first:\n\n"
        "```\npython scripts/generate_synthetic_sheet.py\npython -m dtf_materials.etl\n```"
    )
    st.stop()

conn = get_conn()
summary = q.database_summary(conn)

st.title("Materials Master")
st.caption(
    f"{summary['materials']} materials · {summary['lots']} lots · "
    f"{summary['suppliers']} supplier spellings · {summary['locations']} locations"
)

tab_material, tab_location = st.tabs(["Material lookup", "What's in a location"])

with tab_material:
    term = st.text_input(
        "Search by DTF Part # or material name",
        placeholder="e.g. RM-1409, caffeine, blueberry",
    ).strip()

    if not term:
        st.info("Type a part number or part of a material name to begin.")
    else:
        results = q.search_materials(conn, term)
        if not results:
            st.warning(f"No material matches “{term}”.")
        else:
            if len(results) == 1:
                chosen = results[0]
            else:
                st.write(f"**{len(results)} matches**")
                labels = {
                    f"{r['dtf_part_num'] or '(no part #)'} — {r['material_name']}": r
                    for r in results
                }
                pick = st.radio("Select a material", list(labels), label_visibility="collapsed")
                chosen = labels[pick]

            material = q.get_material(conn, chosen["material_id"])

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
                    hide_index=True, use_container_width=True,
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
                hide_index=True, use_container_width=True,
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
            st.dataframe(
                rows_to_df(found, {
                    "location": "Location",
                    "dtf_part_num": "Part #",
                    "material_name": "Material",
                    "current_stock": "Stock",
                    "dtf_lot_num": "DTF Lot #",
                    "exp_date": "Expires",
                }),
                hide_index=True, use_container_width=True,
            )
