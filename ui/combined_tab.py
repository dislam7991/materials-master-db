"""All Materials Lookup tab: one search over both catalogs."""

from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st
from streamlit_searchbox import st_searchbox

from dtf_materials import queries as q
from ui.common import money


def render(conn: sqlite3.Connection, summary: dict) -> None:
    """Draw the tab: a search box suggesting every match, then the picked one's summary row."""
    st.write(
        "One search over both catalogs, for when you don't yet know which one "
        "holds the answer: is this in the warehouse, in the lab, or both?"
    )

    if summary["lab_samples"] == 0:
        # Normal, not an error: the lab catalog is loaded by its own command.
        st.caption(
            "No lab samples are loaded in this database, so every result here "
            "can only come from the warehouse. See the **R&D Lab Lookup** tab."
        )

    result = st_searchbox(
        lambda term: _search_options(conn, term),
        label="Search by part number, material or flavor name, sample code, or vendor",
        placeholder="e.g. 15-009, mango, 7182011, sensapure",
        key="combined_searchbox",
    )

    # A stale pick (the database was rebuilt under an open page) finds no row.
    # No st.stop() here: it would end the whole run and blank every later tab.
    if not result:
        st.info("Type anything either catalog might know it by.")
        return
    if not _still_exists(conn, result):
        st.warning("That row is no longer in the database. Search again.")
        return

    st.dataframe(
        pd.DataFrame([_summary_row(conn, result)]),
        hide_index=True, width="stretch",
        column_config={"Stock": st.column_config.NumberColumn(format="%.2f")},
    )
    st.caption(
        "**Both** means one row linked to the other by Part #, or by its "
        "sample code appearing in the warehouse material's name — "
        "“Matched by” says which. Unlinked is normal: most lab samples "
        "have never been adopted into inventory. This is a summary; the "
        "**Warehouse Lookup** tab has lot history, price over time and "
        "full stock reconciliation, and the **R&D Lab Lookup** tab has the "
        "vendor and sensory detail."
    )


def _search_options(conn: sqlite3.Connection, term: str) -> list[tuple[str, dict]]:
    """Return every (label, result) search-box option for a typed term, uncapped.

    The option's value is the whole combined-search result, because a row is
    identified by both of its sides: a Both row needs its material and its
    lab sample to build the summary.
    """
    return [(_option_label(r), r) for r in q.search_warehouse_and_lab(conn, term)]


def _still_exists(conn: sqlite3.Connection, result: dict) -> bool:
    """Return whether every side the result names is still in the database."""
    material_ok = (
        result["material_id"] is None
        or q.get_material(conn, result["material_id"]) is not None
    )
    lab_ok = (
        result["lab_sample_id"] is None
        or q.get_lab_sample(conn, result["lab_sample_id"]) is not None
    )
    return material_ok and lab_ok


def _option_label(result: dict) -> str:
    """Name a combined-search result the way its own catalog would, prefixed by where it is."""
    if result["kind"] == "Lab":
        who = f"{result['sample_code'] or '(no code)'} — {result['flavor_name'] or '(unnamed)'}"
    else:
        who = f"{result['dtf_part_num'] or '(no part #)'} — {result['material_name']}"
    return f"{result['kind']}: {who}"


def _summary_row(conn: sqlite3.Connection, result: dict) -> dict:
    """Build one table row from a combined search result plus each side's detail.

    Each side's detail comes from the same queries the single-catalog tabs use,
    so the two views can't drift apart. The two prices stay in two columns and
    a blank one stays a dash: a lot price and a lab vendor quote are different
    numbers, and borrowing one for the other would pass it off as something
    it isn't.
    """
    warehouse = {"Stock": None, "Stocked locations": "—", "Warehouse $/kg": "—"}
    if result["material_id"] is not None:
        stocked = q.get_stocked_locations(conn, result["material_id"])
        material = q.get_material(conn, result["material_id"])
        warehouse = {
            "Stock": q.total_stock(conn, result["material_id"]),
            # Listed, not summed: no quantity is attached to a location here.
            "Stocked locations": ", ".join(r["location"] for r in stocked) or "—",
            "Warehouse $/kg": money(material["current_price_per_kilo"]),
        }

    lab = {"Lab location": "—", "Declaration": "—", "Lab $/kg": "—"}
    sample = (
        q.get_lab_sample(conn, result["lab_sample_id"])
        if result["lab_sample_id"] is not None else None
    )
    if sample is not None:
        lab = {
            "Lab location": sample["location_lab"] or "—",
            "Declaration": sample["declaration_type"] or "—",
            "Lab $/kg": money(sample["price_per_kilo"]),
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
