"""Streamlit lookup app for the materials master database.

The everyday R&D question this answers: "I have a part number (or half a
material name) — where is it, what does it cost, who supplies it?"

Data access lives in dtf_materials/; each tab's UI lives in ui/.
Run with:  streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from dtf_materials import db
from dtf_materials import queries as q
from ui import all_materials_tab, combined_tab, flavor_sheet_tab, lab_tab, location_tab, material_tab
from ui.common import get_conn

st.set_page_config(page_title="Materials Master", page_icon="~", layout="wide")

if not db.DEFAULT_DB_PATH.exists():
    st.error(
        f"No database at `{db.DEFAULT_DB_PATH}`.\n\n"
        "Build it first:\n\n"
        "```\npython scripts/generate_synthetic_sheet.py\npython -m dtf_materials.etl\n```"
    )
    st.stop()

conn = get_conn(db.DEFAULT_DB_PATH)
summary = q.database_summary(conn)

st.title("Materials Master Database")

stat_materials, stat_lots, stat_suppliers, stat_locations = st.columns(4)
stat_materials.metric("Materials", f"{summary['materials']:,}")
stat_lots.metric("Lots", f"{summary['lots']:,}")
stat_suppliers.metric("Supplier Spellings", f"{summary['suppliers']:,}")
stat_locations.metric("Locations", f"{summary['locations']:,}")

tabs = st.tabs([
    "Warehouse + Lab", "Material lookup", "What's in a location",
    "All materials", "Lab Samples", "Flavor Sheet",
])
with tabs[0]:
    combined_tab.render(conn, summary)
with tabs[1]:
    material_tab.render(conn)
with tabs[2]:
    location_tab.render(conn)
with tabs[3]:
    all_materials_tab.render(conn)
with tabs[4]:
    lab_tab.render(conn, summary)
with tabs[5]:
    flavor_sheet_tab.render(conn)
