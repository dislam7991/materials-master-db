"""What's in a location tab: everything stored at or under a location code."""

from __future__ import annotations

import sqlite3

import streamlit as st

from dtf_materials import queries as q
from ui.common import rows_to_df


def render(conn: sqlite3.Connection) -> None:
    """Draw the tab: a location box and the stock found there."""
    st.write("Look up a full location code, or an aisle prefix to see everything in it.")
    loc = st.text_input(
        "Location", placeholder="e.g. 6L-27-D, or just 6L", key="loc"
    ).strip()

    if not loc:
        st.info("Enter a location code or prefix.")
        return

    found = q.search_by_location(conn, loc)
    if not found:
        st.warning(f"Nothing in stock at “{loc.upper()}”.")
        return

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
