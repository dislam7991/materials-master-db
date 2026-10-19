"""Sample Record Sheet tab: build a record sheet in the app (SPEC F11) and copy
its sections into the company template; or, for a sheet built elsewhere, copy
just a flavor's lines (SPEC F2i).

The app never opens or writes the record sheet file. Each section is a
tab-separated block the user pastes into column A of its first row; Excel
lays it over A:I and grows the section when the user inserts rows first.
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st
from streamlit_searchbox import st_searchbox

from dtf_materials import flavor_sheets as fs
from dtf_materials import queries as q
from dtf_materials import record_sheets as rs
from dtf_materials.record_copy_block import build_block, record_blocks
from ui.common import money

OPEN_KEY = "record_open"
NEW = "➕ New record sheet"
SECTION_TITLES = {"active": "Actives", "excipient": "Excipients"}

# Inputs are typed as percentages (36 for 36 %) and stored as fractions.
EDITABLE = ["#", "Label claim (mg)", "Activity %", "Overage %", "Remove"]
LINE_COLUMNS = {
    "#": st.column_config.NumberColumn(width="small", step=1, help="Order in the section — change it to move a line"),
    "Label claim (mg)": st.column_config.NumberColumn(format="%g", min_value=0.0),
    "Activity %": st.column_config.NumberColumn(
        format="%g", min_value=0.01, max_value=100.0,
        help="How much of the claimed nutrient the material yields: 36 for a salt that is 36 % sodium",
    ),
    "Overage %": st.column_config.NumberColumn(format="%g", min_value=0.0, help="Extra added for loss, e.g. 10"),
    "Actual input (mg)": st.column_config.NumberColumn(format="%.3f"),
    "g / run": st.column_config.NumberColumn(format="%.3f"),
    "kg / run": st.column_config.NumberColumn(format="%.6f"),
    "Formula %": st.column_config.NumberColumn(format="%.2f%%"),
    "Price / kg": st.column_config.NumberColumn(format="$%.2f", help="From the catalog — blank if none is on file"),
    "Cost / unit": st.column_config.NumberColumn(format="$%.4f", help="Per jar: per serving × servings per unit"),
    "Remove": st.column_config.CheckboxColumn(width="small", help="Tick to remove the line"),
}


def flavor_label(profile) -> str:
    """The Flavor dropdown label: `Flavor_Sample-ID`, no spaces, so it pastes
    straight into a real sample record sheet (SPEC F8). No Sample ID gives the
    name alone; a flavor with no name stays `(unnamed)`."""
    return "_".join(filter(None, [profile["flavor_name"] or "(unnamed)", profile["sample_id"]]))


def render(conn: sqlite3.Connection) -> None:
    """Draw the tab: pick or create a record sheet and build it, then the flavor-only copy block."""
    st.write(
        "Build a Sample Record Sheet: pick the actives and excipients, enter label claim, "
        "activity and overage, and choose the flavor. Every other column is worked out. "
        "Then copy each section into the company template."
    )
    records = {r["record_sheet_id"]: r for r in rs.list_records(conn)}
    if st.session_state.get(OPEN_KEY) not in [NEW, *records]:
        st.session_state[OPEN_KEY] = NEW
    picked = st.selectbox(
        "Record sheet", [NEW, *records], key=OPEN_KEY,
        format_func=lambda o: o if o == NEW else f"{rs.record_title(records[o])} · {records[o]['created_at'][:10]}",
    )
    flavors = dict(fs.copy_sources(conn, -1))
    if picked == NEW:
        _render_new_form(conn, flavors)
    else:
        _render_record(conn, records[picked], flavors)

    st.divider()
    with st.expander("Only a flavor's lines, for a record sheet built elsewhere", expanded=not records):
        _render_flavor_block(conn)


# --- one record sheet ----------------------------------------------------------

def _header_inputs(current, key: str, flavors: dict) -> dict:
    """Draw the header fields, prefilled from `current`, and return their values."""
    def cur(field):
        """Return the current value of a header field, or None for a new sheet."""
        return current[field] if current is not None else None

    c1, c2, c3 = st.columns(3)
    values = {
        "brand": c1.text_input("Brand", value=cur("brand") or "", key=f"{key}_brand"),
        "product": c2.text_input("Product", value=cur("product") or "", key=f"{key}_product"),
        "quote_id": c3.text_input("Quote ID", value=cur("quote_id") or "", key=f"{key}_quote"),
    }
    c4, c5, c6 = st.columns([1, 1, 3])
    values["servings_per_unit"] = c4.number_input(
        "Servings / unit", min_value=0.0, step=1.0, format="%g", key=f"{key}_spu",
        # A new sheet starts at the sample jar's 5; a saved blank stays blank.
        value=float(rs.DEFAULT_SERVINGS_PER_UNIT) if current is None else _float(cur("servings_per_unit")),
        help="Servings in one jar; drives cost per unit. Samples ship 5.",
    )
    values["servings_base"] = c5.number_input(
        "Servings (base)", min_value=0.0, step=100.0, format="%g", key=f"{key}_base",
        value=_float(cur("servings_base")),
        help="Batch size in servings; drives g/run and kg/run.",
    )
    options = [None, *flavors]
    current_flavor = cur("flavor_profile_id")
    values["flavor_profile_id"] = c6.selectbox(
        "Flavor", options, key=f"{key}_flavor",
        index=options.index(current_flavor) if current_flavor in options else 0,
        format_func=lambda o: "None" if o is None else flavors[o],
        help="Its lines join the excipient section at Activity 100 % / Overage 0. BASE is left out.",
    )
    return {k: (v.strip() or None) if isinstance(v, str) else v for k, v in values.items()}


def _float(value) -> float | None:
    """A stored number as a number_input value; blank stays blank."""
    return None if value is None else float(value)


def _render_new_form(conn: sqlite3.Connection, flavors: dict) -> None:
    """Draw the new-record-sheet form; creating it opens it."""
    with st.form("new_record"):
        _header_inputs(None, "new_record", flavors)
        st.form_submit_button("Create record sheet", type="primary", on_click=_create, args=(conn,))


def _create(conn: sqlite3.Connection) -> None:
    """Create the record sheet from the form's values and open it. A callback, so it may set the picker."""
    s = st.session_state
    header = {
        "brand": s["new_record_brand"], "product": s["new_record_product"], "quote_id": s["new_record_quote"],
        "servings_per_unit": s["new_record_spu"], "servings_base": s["new_record_base"],
        "flavor_profile_id": s["new_record_flavor"],
    }
    header = {k: (v.strip() or None) if isinstance(v, str) else v for k, v in header.items()}
    s[OPEN_KEY] = rs.create_record(conn, **header)


def _render_record(conn: sqlite3.Connection, record, flavors: dict) -> None:
    """Draw an open record sheet: header, the two sections, flavor lines, totals and copy blocks."""
    rid = record["record_sheet_id"]
    with st.form(f"record_header_{rid}"):
        header = _header_inputs(record, f"record_{rid}", flavors)
        if st.form_submit_button("Save details"):
            rs.update_record(conn, rid, **header)
            st.rerun()

    result = rs.compute(rs.all_lines(conn, record), record["servings_per_unit"], record["servings_base"])
    by_section = {s: [l for l in result["lines"] if l["section"] == s] for s in ("active", "excipient", "flavor")}

    for section in rs.SECTIONS:
        st.subheader(SECTION_TITLES[section])
        _render_add_line(conn, rid, section)
        if by_section[section]:
            _render_line_table(conn, rid, section, by_section[section])

    st.subheader("Flavor")
    if record["flavor_profile_id"] is None:
        st.caption("No flavor chosen. Pick one in the details above.")
    elif not by_section["flavor"]:
        st.caption("This flavor has no lines.")
    else:
        st.dataframe(_table(by_section["flavor"]).drop(columns=["#", "Remove"]),
                     hide_index=True, width="stretch", column_config=LINE_COLUMNS)
        st.caption("From the flavor profile, at Activity 100 % / Overage 0. Edit them on the Flavor Sheet tab.")

    _render_totals(record, result)
    if record["servings_per_unit"] is None:
        st.warning("Servings / unit is blank, so there is no cost per unit. Set it in the details above.")
    if record["servings_base"] is None:
        st.info("Servings (base) is blank, so g/run and kg/run are empty. Set it in the details above.")
    _render_record_blocks(conn, rid)

    with st.popover("Delete this record sheet"):
        st.write(f"Delete **{rs.record_title(record)}**? The flavor sheet it uses is kept.")
        if st.button("Delete", key=f"delete_record_{rid}", type="primary"):
            rs.delete_record(conn, rid)
            st.rerun()


def _render_add_line(conn: sqlite3.Connection, rid: int, section: str) -> None:
    """Draw the search box that adds a material to a section."""
    st_searchbox(
        lambda term: _line_options(conn, term),
        label=f"Add to {SECTION_TITLES[section].lower()}",
        placeholder="Search warehouse and lab by name, Part # or sample code — pick one to add it",
        key=f"record_add_{rid}_{section}",
        clear_on_submit=True,
        submit_function=lambda picked: _add_picked(conn, rid, section, picked),
    )
    error_key = f"record_error_{rid}_{section}"
    if error_key in st.session_state:
        st.error(st.session_state.pop(error_key))


def _line_options(conn: sqlite3.Connection, term: str) -> list[tuple[str, tuple]]:
    """Return search-box options from both catalogs. No "use as typed": this sheet carries a cost."""
    options = [
        (f"Warehouse · {r['dtf_part_num'] or '(no part #)'} — {r['material_name']}"
         + (f" · {money(r['current_price_per_kilo'])}/kg" if r["current_price_per_kilo"] is not None else ""),
         ("material", r["material_id"]))
        for r in q.search_materials(conn, term, limit=8)
    ]
    options += [
        (f"Lab · {r['flavor_name'] or '(unnamed)'} {r['sample_code'] or ''} ({r['vendor'] or 'unknown vendor'})"
         + (f" · {money(r['price_per_kilo'])}/kg" if r["price_per_kilo"] is not None else ""),
         ("lab", r["rd_id"]))
        for r in q.search_lab_samples(conn, term, limit=8)
    ]
    return options


def _add_picked(conn: sqlite3.Connection, rid: int, section: str, picked) -> None:
    """Add the picked material to the section, pre-filled with its last-used activity and overage."""
    if not picked:
        return
    kind, ref = picked
    if kind == "lab" and not ref:
        st.session_state[f"record_error_{rid}_{section}"] = (
            "That lab sample has no RD-ID yet, so a line can't link to it. Give it one in the lab sheet."
        )
        return
    rs.add_line(conn, rid, section,
                material_id=ref if kind == "material" else None, rd_id=ref if kind == "lab" else None)


def _pct(value) -> float | None:
    """A stored fraction as the percentage shown in the table (0.36 -> 36)."""
    return None if value is None else round(value * 100, 6)


def _display_row(line: dict) -> dict:
    """One computed line as a table row."""
    return {
        "#": line.get("position"),
        "Part #": line["part_num"],
        "Material": line["name"],
        "Label claim (mg)": line["label_claim_mg"],
        "Activity %": _pct(line["activity"]),
        "Overage %": _pct(line["overage"]),
        "Actual input (mg)": line["actual_input_mg"],
        "g / run": line["g_per_run"],
        "kg / run": line["kg_per_run"],
        "Formula %": _pct(line["formula_pct"]),
        "Price / kg": line["price_per_kilo"],
        "Cost / unit": line["cost_per_unit"],
        "Remove": False,
    }


def _table(lines: list[dict]) -> pd.DataFrame:
    """The lines as a DataFrame with every number column numeric, so a blank shows empty, not "None"."""
    df = pd.DataFrame([_display_row(l) for l in lines])
    numeric = [c for c in df.columns if c not in ("Part #", "Material", "Remove")]
    return df.astype({c: "float64" for c in numeric})


def _render_line_table(conn: sqlite3.Connection, rid: int, section: str, lines: list[dict]) -> None:
    """Draw a section's editable lines, saving each edit as it's made."""
    version_key = f"record_lines_version_{rid}_{section}"
    editor_key = f"record_lines_{rid}_{section}_{st.session_state.get(version_key, 0)}"
    state = [{k: l[k] for k in ("record_line_id", "position", "label_claim_mg", "activity", "overage")}
             for l in lines]
    st.data_editor(
        _table(lines),
        key=editor_key,
        on_change=_apply_edits,
        args=(conn, editor_key, state, version_key),
        hide_index=True, width="stretch",
        disabled=[c for c in _display_row(lines[0]) if c not in EDITABLE],
        column_config=LINE_COLUMNS,
    )


def _apply_edits(conn: sqlite3.Connection, editor_key: str, lines: list[dict], version_key: str) -> None:
    """Save the table's edits, then reset it (the editor tracks rows by position, stale after a removal)."""
    def value(edit: dict, column: str, current, scale: float = 1.0):
        """The edited value (a cleared cell is None), or the current one when untouched."""
        if column not in edit:
            return current
        new = edit[column]
        return None if new is None or pd.isna(new) else float(new) / scale

    for idx, edit in st.session_state[editor_key].get("edited_rows", {}).items():
        line = lines[int(idx)]
        if edit.get("Remove"):
            rs.delete_line(conn, line["record_line_id"])
            continue
        position = value(edit, "#", line["position"])
        rs.update_line(
            conn, line["record_line_id"],
            label_claim_mg=value(edit, "Label claim (mg)", line["label_claim_mg"]),
            activity=value(edit, "Activity %", line["activity"], 100),
            overage=value(edit, "Overage %", line["overage"], 100),
            position=line["position"] if position is None else int(position),
        )
    st.session_state[version_key] = st.session_state.get(version_key, 0) + 1


def _render_totals(record, result: dict) -> None:
    """Draw the totals and say what they leave out."""
    t = result["totals"]
    mg = t["actual_input_mg"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Serving size (mg)", "—" if mg is None else f"{mg:,.3f}")
    c2.metric("Batch (kg / run)", "—" if t["kg_per_run"] is None else f"{t['kg_per_run']:,.3f}")
    c3.metric("Cost / unit", money(t["cost_per_unit"]))
    c4.metric("Cost / serving", "—" if t["cost_per_serving"] is None else f"${t['cost_per_serving']:,.4f}")
    if record["servings_per_unit"] is not None:
        st.caption(
            # \$ — two bare dollar signs in one line render as LaTeX.
            f"Per unit of {record['servings_per_unit']:g} servings: actives "
            f"{money(t['actives_cost_per_unit'])} + excipients and flavor "
            f"{money(t['inactives_cost_per_unit'])}.".replace("$", "\\$")
        )
    if result["incomplete"]:
        st.warning("Left out of every total until label claim, activity and overage are all filled: "
                   + ", ".join(result["incomplete"]) + ".")
    if result["unpriced"]:
        st.warning("No price on file for: " + ", ".join(result["unpriced"])
                   + ". Their cost is left out, not counted as $0.")


def _render_record_blocks(conn: sqlite3.Connection, rid: int) -> None:
    """Draw one copy block per section of the template."""
    st.subheader("Copy into the template")
    st.caption(
        "Paste each block into column A of the section's first row (Ctrl+Shift+V keeps the banding), "
        "inserting rows in Excel first if the section is short. Then fill g/run and Formula % down."
    )
    blocks = record_blocks(conn, rid)
    for key, where in (("actives", "Actives — paste at A12"), ("second", "Excipients and flavor — paste at A37")):
        n = len(blocks[key]["rows"])
        st.write(f"**{where}** · {n} line(s)")
        if n:
            st.code(blocks[key]["text"], language=None)


# --- flavor lines only (F2i) -----------------------------------------------------

def _render_flavor_block(conn: sqlite3.Connection) -> None:
    """Pick a flavor sheet and a flavor, then show its lines as a copy block."""
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
        format_func=lambda i: flavor_label(profiles[i]),
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
