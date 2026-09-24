"""Lab Samples tab: the R&D lab's flavor sample catalog."""

from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st
from streamlit_searchbox import st_searchbox

from dtf_materials import queries as q
from ui.common import NO_LAB_SAMPLES_MESSAGE, money


def render(conn: sqlite3.Connection, summary: dict) -> None:
    """Draw the tab: a lab sample search box, then the picked sample's detail."""
    st.write(
        "The R&D lab's flavor sample catalog — a separate sheet from the "
        "warehouse inventory above, so results here are independent of it."
    )

    if summary["lab_samples"] == 0:
        # Normal, not a failure: say why rather than let every search come back empty.
        st.info(NO_LAB_SAMPLES_MESSAGE)
        return

    lab_sample_id = st_searchbox(
        lambda term: _lab_sample_options(conn, term),
        label="Search by vendor, flavor name, sample code, or Part #",
        placeholder="e.g. 7182011, mango, sensapure",
        key="lab_sample_searchbox",
    )

    sample = q.get_lab_sample(conn, lab_sample_id) if lab_sample_id else None
    if not lab_sample_id:
        st.info("Type a vendor, flavor name, sample code, or part number to begin.")
        return
    if sample is None:
        st.warning("That lab sample is no longer in the database. Search again.")
        return

    st.subheader(f"{sample['sample_code'] or '(no code)'} — {sample['flavor_name'] or '(unnamed)'}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Vendor", sample["vendor"] or "—")
    c2.metric("Declaration Type", sample["declaration_type"] or "—")
    c3.metric("Lab Location", sample["location_lab"] or "—")
    c4.metric("Price / kilo", money(sample["price_per_kilo"]))

    _render_sample_details(sample)


def _lab_sample_options(conn: sqlite3.Connection, term: str) -> list[tuple[str, int]]:
    """Return (label, lab_sample_id) search-box options for a typed term."""
    return [
        (
            f"{r['sample_code'] or '(no code)'} — {r['flavor_name'] or '(unnamed)'} "
            f"({r['vendor'] or 'unknown vendor'})",
            r["lab_sample_id"],
        )
        for r in q.search_lab_samples(conn, term, limit=10)
    ]


def _render_sample_details(sample) -> None:
    """Draw the sample's descriptive fields as a two-column Field/Value table."""
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
