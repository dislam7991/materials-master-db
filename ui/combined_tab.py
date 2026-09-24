"""Warehouse + Lab tab: one search over both catalogs."""

from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from dtf_materials import queries as q


def render(conn: sqlite3.Connection, summary: dict) -> None:
    """Draw the tab: a search box and one summary row per warehouse/lab match."""
    st.write(
        "One search over both catalogs, for when you don't yet know which one "
        "holds the answer: is this in the warehouse, in the lab, or both?"
    )

    if summary["lab_samples"] == 0:
        # Normal, not an error: the lab catalog is loaded by its own command.
        st.caption(
            "No lab samples are loaded in this database, so every result here "
            "can only come from the warehouse. See the **Lab Samples** tab."
        )

    term = st.text_input(
        "Search by part number, material or flavor name, sample code, or vendor",
        placeholder="e.g. 15-009, mango, 7182011, sensapure",
        key="combined",
    ).strip()

    if not term:
        st.info("Type anything either catalog might know it by.")
        return

    found = q.search_warehouse_and_lab(conn, term, limit=25)
    if not found:
        st.warning(f"Nothing matching “{term}” in the warehouse or the lab.")
        return

    st.dataframe(
        pd.DataFrame([_summary_row(conn, result) for result in found]),
        hide_index=True, width="stretch",
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


def _summary_row(conn: sqlite3.Connection, result: dict) -> dict:
    """Build one table row from a combined search result plus each side's detail.

    Each side's detail comes from the same queries the single-catalog tabs use,
    so the two views can't drift apart.
    """
    warehouse = {"Stock": None, "Stocked locations": "—"}
    if result["material_id"] is not None:
        stocked = q.get_stocked_locations(conn, result["material_id"])
        warehouse = {
            "Stock": q.total_stock(conn, result["material_id"]),
            # Listed, not summed: no quantity is attached to a location here.
            "Stocked locations": ", ".join(r["location"] for r in stocked) or "—",
        }

    lab = {"Lab location": "—", "Declaration": "—"}
    sample = (
        q.get_lab_sample(conn, result["lab_sample_id"])
        if result["lab_sample_id"] is not None else None
    )
    if sample is not None:
        lab = {
            "Lab location": sample["location_lab"] or "—",
            "Declaration": sample["declaration_type"] or "—",
        }

    return {
        "Where": result["kind"],
        "Part #": result["dtf_part_num"] or "—",
        "Material": result["material_name"] or "—",
        **warehouse,
        "Vendor": result["vendor"] or "—",
        "Flavor": result["flavor_name"] or "—",
        "Sample code": result["sample_code"] or "—",
        **lab,
        "Matched by": result["match_rule"] or "—",
    }
