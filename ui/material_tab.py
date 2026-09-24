"""Material lookup tab: where a material is, what it costs, its lot history."""

from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st
from streamlit_searchbox import st_searchbox

from dtf_materials import queries as q
from ui.common import money, rows_to_df


def render(conn: sqlite3.Connection) -> None:
    """Draw the tab: a material search box, then the picked material's detail."""
    material_id = st_searchbox(
        lambda term: _material_options(conn, term),
        label="Search by DTF Part # or material name",
        placeholder="e.g. 15-009, caffeine, blueberry",
        key="material_searchbox",
    )

    # A stale pick (the database was rebuilt under an open page) finds no row.
    # No st.stop() here: it would end the whole run and blank every later tab.
    material = q.get_material(conn, material_id) if material_id else None
    if not material_id:
        st.info("Type a part number or part of a material name to begin.")
        return
    if material is None:
        st.warning("That material is no longer in the database. Search again.")
        return

    st.subheader(f"{material['dtf_part_num'] or '(no part #)'} — {material['material_name']}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Price / kilo", money(material["current_price_per_kilo"]))
    c2.metric("Supplier", material["supplier"] or "—")
    c3.metric("Category", (material["category"] or "—").title())
    c4.metric("Allergen", material["allergen"] or "—")

    _render_stock(conn, material["material_id"])
    _render_lot_history(conn, material["material_id"])
    _render_price_chart(conn, material["material_id"])


def _material_options(conn: sqlite3.Connection, term: str) -> list[tuple[str, int]]:
    """Return (label, material_id) search-box options for a typed term."""
    return [
        (f"{r['dtf_part_num'] or '(no part #)'} — {r['material_name']}", r["material_id"])
        for r in q.search_materials(conn, term, limit=10)
    ]


def _render_stock(conn: sqlite3.Connection, material_id: int) -> None:
    """Draw where the material is stocked, or where it was last seen if none is on hand."""
    st.markdown("#### Where it is")
    stocked = q.get_stocked_locations(conn, material_id)
    if not stocked:
        _render_last_known_location(conn, material_id)
        return

    st.dataframe(
        rows_to_df(stocked, {
            "location": "Location",
            "stock": "Stock in lots here",
            "lot_count": "Lots",
        }),
        hide_index=True, width="stretch",
    )
    st.caption(f"Total stock on hand: **{q.total_stock(conn, material_id):,.2f}**")

    missing = q.unlocated_stock(conn, material_id)
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


def _render_last_known_location(conn: sqlite3.Connection, material_id: int) -> None:
    """Draw the most recent lot's location, labelled so it isn't read as current stock."""
    last = q.get_last_known_locations(conn, material_id)
    if not last:
        st.warning("No stock on hand, and no location recorded on any lot.")
        return
    locs = ", ".join(r["location"] for r in last)
    when = last[0]["receiving_date"] or "unknown date"
    st.warning(
        f"No stock on hand. Last known location: **{locs}** "
        f"(lot {last[0]['dtf_lot_num'] or '—'}, received {when})."
    )


def _render_lot_history(conn: sqlite3.Connection, material_id: int) -> None:
    """Draw every lot of the material, newest first."""
    st.markdown("#### Lot history")
    st.dataframe(
        rows_to_df(q.get_lots(conn, material_id), {
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


def _render_price_chart(conn: sqlite3.Connection, material_id: int) -> None:
    """Draw price per kilo over time, when there is more than one priced lot."""
    history = q.price_history(conn, material_id)
    if len(history) <= 1:
        return
    st.markdown("#### Price per kilo over time")
    st.caption(
        "Recorded per lot, so repricing between receivings is visible "
        "instead of being overwritten."
    )
    df = pd.DataFrame(
        [{"Received": r["receiving_date"], "Price/kg": r["price_per_kilo"]} for r in history]
    ).set_index("Received")
    st.line_chart(df)
