"""Sample Record Sheet tab: a flavor profile's lines as a block to paste into
the record sheet the manager builds in Excel (SPEC F2i).

Holds only the copy block. The app never opens or writes the manager's file;
the user pastes the text and Excel lays it over columns A:I.
"""

from __future__ import annotations

import sqlite3

import streamlit as st

from dtf_materials import flavor_sheets as fs
from dtf_materials.record_copy_block import build_block


def render(conn: sqlite3.Connection) -> None:
    """Draw the tab: pick a flavor sheet and a flavor, then show its copy block."""
    st.write(
        "Copy a flavor's lines into a Sample Record Sheet. Click in column A on the "
        "first empty row below the last excipient and paste (Ctrl+Shift+V keeps the "
        "banding). Then fill g/run and Formula % down from the excipient row."
    )

    sheets = {s["flavor_sheet_id"]: s for s in fs.list_sheets(conn)}
    if not sheets:
        st.info("No flavor sheets yet — build one on the Flavor Sheet tab first.")
        return
    sheet_id = st.selectbox(
        "Flavor sheet", list(sheets), key="record_sheet",
        format_func=lambda i: f"{fs.product_line(sheets[i]) or '(untitled)'} · {sheets[i]['created_at'][:10]}",
    )

    profiles = {p["flavor_profile_id"]: p for p in fs.get_profiles(conn, sheet_id)}
    if not profiles:
        st.info("This sheet has no flavors yet.")
        return
    profile_id = st.selectbox(
        "Flavor", list(profiles), key="record_flavor",
        format_func=lambda i: " · ".join(
            filter(None, [profiles[i]["flavor_name"] or "(unnamed)", profiles[i]["sample_id"]])
        ),
    )

    block = build_block(conn, profile_id)
    n = len(block["rows"])
    if n == 0:
        st.info("This flavor has no lines to copy.")
        return
    st.write(
        f"**{n} line(s)** — the section needs {n} empty row(s) below the last "
        "excipient. If it's short, insert rows in Excel first."
    )
    st.code(block["text"], language=None)
    if block["unpriced"]:
        st.warning(
            "No price on file for: " + ", ".join(block["unpriced"])
            + ". Their Price/kg is left blank — the sheet counts a blank as $0, so fill it in."
        )
