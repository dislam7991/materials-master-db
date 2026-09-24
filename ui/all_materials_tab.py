"""All materials tab: every material as one sortable table."""

from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from dtf_materials import queries as q


def render(conn: sqlite3.Connection) -> None:
    """Draw the tab: the full material list."""
    st.write(
        "Every material in the database. Click a column header to sort, or use "
        "the search icon in the table's own toolbar to filter."
    )
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
            for r in q.list_materials(conn)
        ]),
        hide_index=True, width="stretch",
        column_config={
            # Numbers stay numbers so sorting works; only the display is
            # formatted ("$100" sorts before "$20" as a string).
            "Price/kg": st.column_config.NumberColumn(format="$%.2f"),
            "Stock on hand": st.column_config.NumberColumn(format="%.2f"),
        },
    )
