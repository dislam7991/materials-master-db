"""Flavor Sheet tab: build, save and download the company Flavor Sheet.

Which sheet and which flavor are open lives in the page URL
(?sheet=…&flavor=…), not in widget state: widget state is lost when a label
changes, on a reconnect or on a reload, and the URL survives all three.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import date
from functools import partial

import pandas as pd
import streamlit as st
from streamlit_searchbox import st_searchbox

from dtf_materials import flavor_sheets as fs
from dtf_materials import labels_docx
from dtf_materials import queries as q
from dtf_materials.flavor_sheet_xlsx import DEFAULT_TEMPLATE_PATH as TEMPLATE_PATH
from dtf_materials.flavor_sheet_xlsx import Flavor
from dtf_materials.flavor_sheet_xlsx import render as render_flavor_sheet
from dtf_materials.flavor_sheet_xlsx import render_snapshot as render_flavor_sheet_snapshot
from ui.common import money

FOLDER_STATE = "flavor_sheet_folder"   # (customer key, quote folder key) being browsed; None = not chosen
SEARCH_KEY = "flavor_sheet_search"
ADD_FLAVOR = "➕ Add a flavor"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# The line table: which columns are read-only, and how each is shown.
LINE_TABLE_READ_ONLY = ["Material", "Source", "g / sample", "Price / kg", "Cost / serving"]
LINE_TABLE_COLUMNS = {
    "#": st.column_config.NumberColumn(width="small", step=1,
                                       help="Print order — change it to move a line"),
    "mg / serving": st.column_config.NumberColumn(format="%g", min_value=0.0),
    "g / sample": st.column_config.NumberColumn(format="%.3f"),
    "Price / kg": st.column_config.NumberColumn(
        format="$%.2f",
        help="From the catalog row this line references — blank if none is on file",
    ),
    "Cost / serving": st.column_config.NumberColumn(format="$%.4f"),
    "Remove": st.column_config.CheckboxColumn(width="small", help="Tick to remove the line"),
}


def render(conn: sqlite3.Connection) -> None:
    """Draw the tab: search, folder path, then either a folder's contents or the open sheet."""
    st.write(
        "Fill in the Flavor Sheet: customer, product, quote ID and servings once, "
        "then each flavor's materials in mg per serving. Grams per sample are "
        "worked out for you, and **Download** fills in the company template "
        "(four flavors to a page). Saved sheets are filed by customer, then "
        "product · quote ID."
    )

    open_id = _url_id("sheet")
    sheet = fs.get_sheet(conn, open_id) if open_id is not None else None
    if sheet is not None:
        # Going up from an open sheet lands in its own folder, even after a reload.
        st.session_state[FOLDER_STATE] = fs.sheet_folder(sheet)

    _render_search(conn)
    tree = fs.folders(conn)
    customer, quote = _browsed(tree)
    _render_breadcrumbs(customer, quote, sheet)
    if sheet is None:
        _render_folder(conn, tree, customer, quote)
        return

    sheet_id = sheet["flavor_sheet_id"]
    _render_sheet_details(conn, sheet)
    profiles = fs.get_profiles(conn, sheet_id)
    picked = _pick_flavor(sheet_id, profiles)
    if picked == ADD_FLAVOR:
        _render_add_flavor_form(conn, sheet, profiles)
    else:
        _render_flavor_editor(conn, sheet, next(p for p in profiles if p["flavor_profile_id"] == picked))

    st.divider()
    if sheet["servings"] is None:
        st.warning("Servings per flavor is blank, so the sheet can't work out any grams. Set it in the details above.")
    _render_download_and_delete(conn, sheet, profiles)
    _render_labels(conn, sheet, profiles)
    _render_sent_copies(conn, sheet)


# --- URL state ---------------------------------------------------------------

def _url_id(name: str) -> int | None:
    """Read an integer id from the URL query string, or None if absent or not a number."""
    raw = st.query_params.get(name)
    return int(raw) if raw and raw.isdigit() else None


def _open(sheet_id: int | None, flavor: int | str | None = None) -> None:
    """Point the URL at a sheet and, optionally, a flavor id or "new"."""
    st.query_params.clear()
    if sheet_id is not None:
        st.query_params["sheet"] = str(sheet_id)
    if flavor is not None:
        st.query_params["flavor"] = str(flavor)


# --- folders and search (F6a) ------------------------------------------------
#
# Which folder is being browsed lives in session state, not the URL: it holds
# customer names, and an open sheet re-derives it from its own header anyway.

def _go(customer_key: str | None, quote_key: tuple[str, str] | None = None) -> None:
    """Browse a folder (None, None = all customers), closing any open sheet. Used as a button callback."""
    st.session_state[FOLDER_STATE] = (customer_key, quote_key)
    _open(None)


def _open_sheet(sheet_id: int) -> None:
    """Open a sheet and clear the search box. Used as a button callback."""
    st.session_state[SEARCH_KEY] = ""
    _open(sheet_id)


def _browsed(tree: list[dict]) -> tuple[dict | None, dict | None]:
    """Return the customer and quote folder being browsed, dropping a level that no longer exists."""
    customer_key, quote_key = st.session_state.get(FOLDER_STATE, (None, None))
    customer = next((c for c in tree if c["key"] == customer_key), None)
    quote = next((f for f in customer["quotes"] if f["key"] == quote_key), None) if customer else None
    return customer, quote


def _customer_label(customer: dict) -> str:
    """Return a customer folder's name, or its "(no customer)" placeholder."""
    return customer["customer"] or fs.BLANK_LABELS["customer"]


def _quote_label(quote: dict) -> str:
    """Return a quote folder's name as "Product · Quote ID", with placeholders for blanks."""
    return f"{quote['product'] or fs.BLANK_LABELS['product']} · {quote['quote_id'] or fs.BLANK_LABELS['quote_id']}"


def _sheet_label(entry: dict) -> str:
    """Return a sheet's line in a folder: its date and flavors."""
    titles = [fs.profile_title(p) for p in entry["profiles"]]
    flavors = ", ".join(titles[:4]) + (f", +{len(titles) - 4} more" if len(titles) > 4 else "")
    return f"{entry['sheet']['created_at'][:10]} · {len(titles)} flavor(s)" + (f": {flavors}" if titles else "")


def _render_search(conn: sqlite3.Connection) -> None:
    """Draw the search box and, for a term, every matching sheet with its folder path."""
    term = st.text_input(
        "Search flavor sheets", key=SEARCH_KEY,
        placeholder="Customer, product, quote ID, flavor name or Sample ID",
    )
    if not term.strip():
        return
    hits = fs.search_sheets(conn, term)
    if not hits:
        st.caption(f"No flavor sheet matches “{term.strip()}”.")
    for entry in hits:
        sheet = entry["sheet"]
        path = " › ".join((
            sheet["customer"] or fs.BLANK_LABELS["customer"],
            f"{sheet['product'] or fs.BLANK_LABELS['product']} · {sheet['quote_id'] or fs.BLANK_LABELS['quote_id']}",
        ))
        st.button(
            f"📄 {path} › {_sheet_label(entry)}", key=f"hit_{sheet['flavor_sheet_id']}",
            on_click=_open_sheet, args=(sheet["flavor_sheet_id"],), type="tertiary",
        )
    st.divider()


def _render_breadcrumbs(customer: dict | None, quote: dict | None, sheet) -> None:
    """Draw the folder path as buttons; the last step is where you are, so it's not a button."""
    crumbs: list[tuple[str, tuple]] = [("All customers", (None, None))]
    if customer is not None:
        crumbs.append((f"📁 {_customer_label(customer)}", (customer["key"], None)))
    if quote is not None:
        crumbs.append((f"📁 {_quote_label(quote)}", (customer["key"], quote["key"])))  # type: ignore[index]
    here = f"📄 {sheet['created_at'][:10]}" if sheet is not None else None

    with st.container(horizontal=True, vertical_alignment="center"):
        for i, (label, target) in enumerate(crumbs):
            last = here is None and i == len(crumbs) - 1
            st.button(
                label if i == 0 else f"› {label}", key=f"crumb_{i}",
                on_click=_go, args=target, type="tertiary", disabled=last,
            )
        if here is not None:
            st.button(f"› {here}", key="crumb_sheet", type="tertiary", disabled=True)


def _render_folder(conn: sqlite3.Connection, tree: list[dict], customer: dict | None, quote: dict | None) -> None:
    """Draw the browsed folder: a new-sheet form pre-filled from it, a rename, and its contents."""
    if customer is None:
        _render_new_sheet_form(conn, {}, "new_sheet", expanded=not tree)
        if not tree:
            st.info("No flavor sheets yet. Start one above.")
        _folder_grid([(f"📁 {_customer_label(c)}", _sheet_count(c), (c["key"], None)) for c in tree])
        return

    if quote is None:
        _render_new_sheet_form(conn, {"customer": customer["customer"]}, _form_key(customer["key"]))
        _render_rename_customer(conn, customer)
        _folder_grid([(f"📁 {_quote_label(f)}", len(f["sheets"]), (customer["key"], f["key"]))
                      for f in customer["quotes"]])
        return

    newest = quote["sheets"][0]["sheet"]
    _render_new_sheet_form(conn, {k: newest[k] for k in fs.HEADER_FIELDS}, _form_key(customer["key"], quote["key"]))
    _render_rename_quote(conn, customer, quote)
    for entry in quote["sheets"]:
        st.button(
            f"📄 {_sheet_label(entry)}", key=f"open_{entry['sheet']['flavor_sheet_id']}",
            on_click=_open_sheet, args=(entry["sheet"]["flavor_sheet_id"],), width="stretch",
        )


def _sheet_count(customer: dict) -> int:
    """Return how many sheets are filed under a customer."""
    return sum(len(f["sheets"]) for f in customer["quotes"])


def _folder_grid(folders: list[tuple[str, int, tuple]]) -> None:
    """Draw folder buttons three to a row, each with its sheet count."""
    cols = st.columns(3)
    for i, (label, count, target) in enumerate(folders):
        cols[i % 3].button(
            f"{label} · {count} sheet{'s' if count != 1 else ''}", key=f"folder_{i}",
            on_click=_go, args=target, width="stretch",
        )


def _form_key(*folder) -> str:
    """Return a widget-key prefix unique to a folder, so its pre-filled form doesn't reuse another's values."""
    return "new_sheet_" + hashlib.md5(repr(folder).encode()).hexdigest()[:8]


def _render_rename_customer(conn: sqlite3.Connection, customer: dict) -> None:
    """Draw the rename popover for a customer folder."""
    count = _sheet_count(customer)
    with st.popover("Rename folder"):
        with st.form(f"rename_{_form_key(customer['key'])}"):
            new = st.text_input("Customer", value=customer["customer"] or "")
            st.caption(f"Changes the customer on all {count} sheet(s) here. Renaming to another "
                       "folder's name merges the two.")
            if st.form_submit_button("Rename", type="primary"):
                fs.rename_customer(conn, customer["key"], new)
                st.session_state[FOLDER_STATE] = (fs.folder_key(new), None)
                st.rerun()


def _render_rename_quote(conn: sqlite3.Connection, customer: dict, quote: dict) -> None:
    """Draw the rename popover for a Product · Quote ID folder."""
    with st.popover("Rename folder"):
        with st.form(f"rename_{_form_key(customer['key'], quote['key'])}"):
            c1, c2 = st.columns(2)
            product = c1.text_input("Product", value=quote["product"] or "")
            quote_id = c2.text_input("Quote ID", value=quote["quote_id"] or "")
            st.caption(f"Changes product and quote ID on all {len(quote['sheets'])} sheet(s) here. "
                       "Renaming to another folder's name merges the two.")
            if st.form_submit_button("Rename", type="primary"):
                fs.rename_quote_folder(conn, customer["key"], quote["key"], product, quote_id)
                st.session_state[FOLDER_STATE] = (customer["key"], (fs.folder_key(product), fs.folder_key(quote_id)))
                st.rerun()


# --- sheet level -------------------------------------------------------------

def _sheet_header_inputs(current, key: str) -> dict:
    """Draw the five header fields, prefilled from `current` when editing, and return their values."""
    def cur(field):
        """Return the current value of a header field, or None for a new sheet."""
        return current[field] if current is not None else None

    c1, c2, c3, c4, c5 = st.columns([3, 3, 2, 2, 2])
    values = {
        "customer": c1.text_input("Customer", value=cur("customer") or "", key=f"{key}_customer"),
        "product": c2.text_input("Product", value=cur("product") or "", key=f"{key}_product"),
        "quote_id": c3.text_input("Quote ID", value=cur("quote_id") or "", key=f"{key}_quote"),
        "servings": c4.number_input(
            "Servings per flavor", min_value=0, step=1,
            value=int(cur("servings")) if cur("servings") is not None else None,  # type: ignore[arg-type]
            key=f"{key}_servings",
        ),
        "sample_prefix": c5.text_input(
            "Sample ID prefix", value=cur("sample_prefix") or "", key=f"{key}_prefix",
            help="Start of the suggested Sample ID: SMPL gives SMPL260922-01, -02, …",
        ),
    }
    # Blank stays NULL, not an empty string or a 0 nobody entered.
    return {k: (v.strip() or None) if isinstance(v, str) else v for k, v in values.items()}


def _render_new_sheet_form(conn: sqlite3.Connection, prefill: dict, key: str, expanded: bool = False) -> None:
    """Draw the header form for a new sheet, pre-filled from the folder it's started in, and open the sheet once created."""
    label = "➕ New flavor sheet here" if prefill else "➕ New flavor sheet"
    with st.expander(label, expanded=expanded), st.form(f"{key}_form"):
        header = _sheet_header_inputs({k: prefill.get(k) for k in fs.HEADER_FIELDS}, key)
        create = st.form_submit_button("Create flavor sheet", type="primary")
    if create:
        _open(fs.create_sheet(conn, **header))
        st.rerun()


def _render_sheet_details(conn: sqlite3.Connection, sheet) -> None:
    """Draw the open sheet's editable header and save it on submit."""
    sheet_id = sheet["flavor_sheet_id"]
    with st.form(f"sheet_header_{sheet_id}"):
        header = _sheet_header_inputs(sheet, f"sheet_{sheet_id}")
        if st.form_submit_button("Save details"):
            fs.update_sheet(conn, sheet_id, **header)
            st.rerun()


def _pick_flavor(sheet_id: int, profiles: list) -> int | str:
    """Draw the flavor picker and return the open flavor's id, or ADD_FLAVOR."""
    profile_ids = [p["flavor_profile_id"] for p in profiles]
    options = profile_ids + [ADD_FLAVOR]
    open_flavor = ADD_FLAVOR if st.query_params.get("flavor") == "new" else _url_id("flavor")
    if open_flavor not in options:
        open_flavor = profile_ids[-1] if profile_ids else ADD_FLAVOR
    picked = st.radio(
        "Flavor",
        options,
        index=options.index(open_flavor),
        format_func=lambda o: o if o == ADD_FLAVOR else next(
            f"{p['position']}. {fs.profile_title(p)}" for p in profiles if p["flavor_profile_id"] == o
        ),
        horizontal=True,
    )
    assert picked is not None
    if picked != open_flavor:
        _open(sheet_id, "new" if picked == ADD_FLAVOR else picked)
        st.rerun()
    return picked


def _render_add_flavor_form(conn: sqlite3.Connection, sheet, profiles: list) -> None:
    """Draw the new-flavor form: name, suggested Sample ID, and an optional flavor to copy.

    Copying is the default because flavors on one sheet usually share their
    base, acids and sweeteners.
    """
    sheet_id = sheet["flavor_sheet_id"]
    profile_ids = [p["flavor_profile_id"] for p in profiles]
    next_position = len(profiles) + 1
    with st.form(f"add_flavor_{sheet_id}_{next_position}"):
        c1, c2, c3 = st.columns([3, 2, 3])
        flavor_name = c1.text_input("Flavor name", key=f"new_fname_{sheet_id}_{next_position}")
        sample_id = c2.text_input(
            "Sample ID",
            value=fs.suggest_sample_id(sheet["sample_prefix"], date.today(), next_position),
            key=f"new_fsid_{sheet_id}_{next_position}",
        )
        copy_from = c3.selectbox(
            "Start from",
            [None] + profile_ids,
            index=len(profile_ids),
            format_func=lambda o: "Empty" if o is None else next(
                f"Copy of {p['position']}. {fs.profile_title(p)}" for p in profiles
                if p["flavor_profile_id"] == o
            ),
            key=f"new_fcopy_{sheet_id}_{next_position}",
        )
        if st.form_submit_button("Add flavor", type="primary"):
            new_id = fs.add_profile(
                conn, sheet_id,
                flavor_name=flavor_name.strip() or None,
                sample_id=sample_id.strip() or None,
                copy_lines_from=copy_from,
            )
            _open(sheet_id, new_id)
            st.rerun()


# --- one flavor --------------------------------------------------------------

@st.fragment
def _render_flavor_editor(conn: sqlite3.Connection, sheet, profile) -> None:
    """Draw one flavor's editor as a fragment, so each keystroke reruns only this part."""
    pid = profile["flavor_profile_id"]
    _render_profile_form(conn, profile)

    st_searchbox(
        lambda term: _line_options(conn, term),
        label="Add a material",
        placeholder="Search warehouse and lab by name, Part # or sample code — pick one to add it",
        key=f"add_pick_{pid}",
        clear_on_submit=True,
        submit_function=lambda picked: _add_picked_line(conn, pid, picked),
        rerun_scope="fragment",
    )
    if f"line_error_{pid}" in st.session_state:
        st.error(st.session_state.pop(f"line_error_{pid}"))

    lines = fs.get_lines(conn, pid)
    if not lines:
        st.info("No materials yet. Search above and pick one to add it.")
    else:
        _render_line_table(conn, profile, lines, sheet["servings"])
        caption = cost_caption(fs.profile_cost(lines, sheet["servings"]), sheet["servings"])
        if caption:
            st.caption(caption)

    with st.popover("Remove this flavor"):
        st.write(f"Remove **{fs.profile_title(profile)}** and its {len(lines)} lines?")
        if st.button("Remove", key=f"remove_{pid}", type="primary"):
            fs.delete_profile(conn, pid)
            _open(sheet["flavor_sheet_id"])
            st.rerun(scope="app")


def _render_profile_form(conn: sqlite3.Connection, profile) -> None:
    """Draw the flavor's name, Sample ID and BASE fields and save them on submit."""
    pid = profile["flavor_profile_id"]
    with st.form(f"profile_{pid}"):
        c1, c2, c3 = st.columns([3, 2, 2])
        flavor_name = c1.text_input("Flavor name", value=profile["flavor_name"] or "", key=f"pname_{pid}")
        sample_id = c2.text_input("Sample ID", value=profile["sample_id"] or "", key=f"psid_{pid}")
        base_mg = c3.number_input(
            "BASE (mg / serving)", min_value=0.0, step=10.0,
            value=profile["base_mg"], key=f"pbase_{pid}",
        )
        if st.form_submit_button("Save flavor"):
            fs.update_profile(conn, pid, flavor_name.strip() or None,
                              sample_id.strip() or None, base_mg)
            st.rerun(scope="app")   # the flavor's title shows outside this fragment


def _price_tag(price) -> str:
    """Return " · $x.xx/kg" for a search option, or nothing when no price is on file."""
    return f" · {money(price)}/kg" if price is not None else ""


def _line_options(conn: sqlite3.Connection, term: str) -> list[tuple[str, tuple]]:
    """Return search-box options: warehouse matches, lab matches, then "use as typed"."""
    options = [
        (
            f"Warehouse · {r['dtf_part_num'] or '(no part #)'} — {r['material_name']}"
            + _price_tag(r["current_price_per_kilo"]),
            ("material", r["material_id"]),
        )
        for r in q.search_materials(conn, term, limit=8)
    ]
    options += [
        (
            f"Lab · {r['flavor_name'] or '(unnamed)'} {r['sample_code'] or ''} "
            f"({r['vendor'] or 'unknown vendor'})" + _price_tag(r["price_per_kilo"]),
            ("lab", r["rd_id"]),
        )
        for r in q.search_lab_samples(conn, term, limit=8)
    ]
    if term.strip():
        options.append((f"✎ Use “{term.strip()}” as typed — not in either catalog",
                        ("typed", term.strip())))
    return options


def _add_picked_line(conn: sqlite3.Connection, profile_id: int, picked) -> None:
    """Add the picked material as a new line straight away; mg is filled in the table after."""
    if not picked:
        return
    kind, ref = picked
    if kind == "lab" and not ref:
        st.session_state[f"line_error_{profile_id}"] = (
            "That lab sample has no RD-ID yet, so a line can't link to it. Give it "
            "one in the lab sheet, or search again and pick the “use as typed” option."
        )
        return
    fs.add_line(
        conn, profile_id,
        material_id=ref if kind == "material" else None,
        rd_id=ref if kind == "lab" else None,
        typed_name=ref if kind == "typed" else None,
    )


def _render_line_table(conn: sqlite3.Connection, profile, lines: list, servings) -> None:
    """Draw the editable table of a flavor's lines, saving each edit as it's made."""
    pid = profile["flavor_profile_id"]
    version_key = f"lines_version_{pid}"
    editor_key = f"lines_{pid}_{st.session_state.get(version_key, 0)}"
    line_state = [
        {"id": l["flavor_profile_line_id"], "mg": l["mg_per_serving"], "position": l["position"]}
        for l in lines
    ]
    st.data_editor(
        pd.DataFrame([
            {
                "#": l["position"],
                "Material": l["name"],
                "Source": "Typed — not in catalog" if l["source"] == "Typed" else l["source"],
                "mg / serving": l["mg_per_serving"],
                "g / sample": fs.grams_per_sample(l["mg_per_serving"], servings),
                "Price / kg": l["price_per_kilo"],
                "Cost / serving": fs.line_cost_per_serving(l["mg_per_serving"], l["price_per_kilo"]),
                "Remove": False,
            }
            for l in lines
        ]),
        key=editor_key,
        on_change=_apply_line_edits,
        args=(conn, editor_key, line_state, version_key),
        hide_index=True, width="stretch",
        disabled=LINE_TABLE_READ_ONLY,
        column_config=LINE_TABLE_COLUMNS,
    )
    base_g = fs.grams_per_sample(profile["base_mg"], servings)
    st.caption(
        "Type mg straight into the table; tick **Remove** to drop a line; change "
        "**#** to reorder. Changes save as you make them."
        + (f" BASE: {base_g:,.3f} g per sample." if base_g is not None else "")
    )


def _apply_line_edits(conn: sqlite3.Connection, editor_key: str, lines: list[dict], version_key: str) -> None:
    """Save the table's edits to the database, then reset the table.

    The reset matters: the editor tracks edits by row position, which is stale
    once a row is removed or reordered.
    """
    for idx, edit in st.session_state[editor_key].get("edited_rows", {}).items():
        line = lines[int(idx)]
        if edit.get("Remove"):
            fs.delete_line(conn, line["id"])
            continue
        mg = edit.get("mg / serving", line["mg"])
        position = edit.get("#", line["position"])
        fs.update_line(
            conn, line["id"],
            mg_per_serving=None if mg is None or pd.isna(mg) else float(mg),
            position=line["position"] if position is None or pd.isna(position) else int(position),
        )
    st.session_state[version_key] = st.session_state.get(version_key, 0) + 1


def cost_caption(cost: dict, servings) -> str | None:
    """Return the rough-cost caption for a flavor (from fs.profile_cost), or None if there's nothing to say.

    Built from priced lines only and labelled an estimate: the generated sheet
    stays price-free, and a missing price is never counted as free.
    """
    if cost["per_serving"] is not None:
        estimate = f"**Rough material cost ≈ {money(cost['per_serving'])} / serving**"
        if cost["per_sample"] is not None:
            estimate += f"  ·  {money(cost['per_sample'])} / sample"
        notes = []
        if cost["unpriced"]:
            notes.append(f"excludes {cost['unpriced']} line(s) with no price on file")
        if servings is None:
            notes.append("set servings per flavor for a per-sample figure")
        if notes:
            estimate += f" — {', '.join(notes)}"
        return estimate + ". Estimate only; BASE and unpriced lines are left out."
    if cost["unpriced"]:
        return f"No prices on file for these {cost['unpriced']} material(s) yet, so no cost estimate."
    return None


# --- download / delete -------------------------------------------------------

def _file_stem(sheet) -> str:
    """Return the sheet's product line made safe for a file name."""
    return re.sub(r'[\\/:*?"<>|]+', "-", fs.product_line(sheet) or "untitled")


def _build_workbook(conn: sqlite3.Connection, sheet_id: int) -> bytes:
    """Render the sheet to .xlsx from what's in the database right now (called on Download).

    Snapshots the numbers once it renders (F2h), so a reprint after a reprice
    can be told apart from the copy already sent.
    """
    sheet = fs.get_sheet(conn, sheet_id)
    assert sheet is not None
    workbook = render_flavor_sheet(
        fs.product_line(sheet),
        sheet["servings"],
        [
            Flavor(
                title=fs.profile_title(p),
                base_mg=p["base_mg"],
                lines=[(l["name"], l["mg_per_serving"])
                       for l in fs.get_lines(conn, p["flavor_profile_id"])],
            )
            for p in fs.get_profiles(conn, sheet_id)
        ],
        TEMPLATE_PATH,
    )
    fs.take_snapshot(conn, sheet_id, "flavor_sheet")
    return workbook


def _render_download_and_delete(conn: sqlite3.Connection, sheet, profiles: list) -> None:
    """Draw the Download button (when the template exists) and the Delete-sheet popover."""
    sheet_id = sheet["flavor_sheet_id"]
    c_download, c_delete = st.columns([3, 1])
    if not TEMPLATE_PATH.exists():
        c_download.error(
            f"No Flavor Sheet template at `{TEMPLATE_PATH}`. Copy the company "
            "template there (the folder is gitignored) to enable downloads."
        )
    elif profiles:
        c_download.download_button(
            "Download Flavor Sheet (.xlsx)",
            data=partial(_build_workbook, conn, sheet_id),
            file_name=f"Flavor Sheet - {_file_stem(sheet)}.xlsx",
            mime=XLSX_MIME,
            type="primary",
            on_click="ignore",
        )

    with c_delete.popover("Delete this flavor sheet"):
        st.write(f"Delete **{fs.product_line(sheet) or '(untitled)'}** and all {len(profiles)} flavors?")
        if st.button("Delete", key=f"delete_sheet_{sheet_id}", type="primary"):
            fs.delete_sheet(conn, sheet_id)
            _open(None)
            st.rerun()


# --- labels --------------------------------------------------------------------

def _build_labels(conn: sqlite3.Connection, sheet_id: int, index: int) -> bytes:
    """Render the sheet's labels from the database right now and return document `index` (called on Download).

    Snapshots the numbers once it renders (F2h), like the flavor sheet download.
    """
    document = labels_docx.render(labels_docx.labels_for_sheet(conn, sheet_id), labels_docx.DEFAULT_TEMPLATE_PATH)[index]
    fs.take_snapshot(conn, sheet_id, "labels")
    return document


def _render_labels(conn: sqlite3.Connection, sheet, profiles: list) -> None:
    """Draw the scoops-per-serving field and one Download Labels button per ten flavors."""
    sheet_id, product = sheet["flavor_sheet_id"], sheet["product"]
    c_scoops, c_download = st.columns([1, 3])
    stored = fs.scoops_per_serving(conn, product)
    scoops = c_scoops.number_input(
        "Scoops per serving", min_value=0.5, step=0.5, value=float(stored), format="%g",
        key=f"scoops_{sheet_id}_{product}", disabled=not (product or "").strip(),
        help="Printed on the labels. Remembered for this product; most are 1.",
    )
    if scoops != stored:
        fs.set_scoops_per_serving(conn, product, scoops)

    if not labels_docx.DEFAULT_TEMPLATE_PATH.exists():
        c_download.error(
            f"No labels template at `{labels_docx.DEFAULT_TEMPLATE_PATH}`. Copy the company "
            "template there (the folder is gitignored) to enable label downloads."
        )
        return
    per_doc = labels_docx.LABELS_PER_DOCUMENT
    for index, start in enumerate(range(0, len(profiles), per_doc)):
        last = min(start + per_doc, len(profiles))
        suffix = f" {start + 1}-{last}" if len(profiles) > per_doc else ""
        c_download.download_button(
            f"Download Labels{suffix} (.docx)",
            data=partial(_build_labels, conn, sheet_id, index),
            file_name=f"Labels - {_file_stem(sheet)}{suffix}.docx",
            mime=DOCX_MIME,
            on_click="ignore",
            key=f"labels_{sheet_id}_{index}",
        )


# --- re-download as sent (F6b) -------------------------------------------------

def _rebuild_workbook(conn: sqlite3.Connection, snapshot_id: int) -> bytes:
    """Re-render the flavor sheet from a stored snapshot (called on Download); takes no new snapshot."""
    return render_flavor_sheet_snapshot(fs.get_snapshot(conn, snapshot_id), TEMPLATE_PATH)


def _rebuild_labels(conn: sqlite3.Connection, snapshot_id: int, index: int) -> bytes:
    """Re-render labels document `index` from a stored snapshot (called on Download); takes no new snapshot."""
    labels = labels_docx.labels_from_snapshot(fs.get_snapshot(conn, snapshot_id))
    return labels_docx.render(labels, labels_docx.DEFAULT_TEMPLATE_PATH)[index]


def _render_sent_copies(conn: sqlite3.Connection, sheet) -> None:
    """List every past download of the sheet with a button that re-renders it from its snapshot.

    The copy that was sent, not today's numbers: a rename or reprice since
    then doesn't reach it. A download from before snapshots existed left
    nothing to rebuild from, so it isn't listed, and nothing is guessed.
    """
    sheet_id = sheet["flavor_sheet_id"]
    snapshots = fs.list_snapshots(conn, sheet_id)
    with st.expander(f"Copies sent ({len(snapshots)})"):
        if not snapshots:
            st.caption(
                "No downloads recorded for this sheet. A copy downloaded before "
                "snapshots were kept has nothing stored to rebuild it from."
            )
            return
        st.caption("Each download, rebuilt from the numbers it was printed with. Times are UTC.")
        stem = _file_stem(sheet)
        for snap in reversed(snapshots):
            when, snapshot_id = snap["taken_at"][:16], snap["snapshot_id"]
            if snap["kind"] == "flavor_sheet":
                if TEMPLATE_PATH.exists():
                    st.download_button(
                        f"{when} · Flavor Sheet (.xlsx)",
                        data=partial(_rebuild_workbook, conn, snapshot_id),
                        file_name=f"Flavor Sheet - {stem} - sent {when[:10]}.xlsx",
                        mime=XLSX_MIME, on_click="ignore", key=f"sent_{snapshot_id}",
                    )
                else:
                    st.write(f"{when} · Flavor Sheet (no template on this machine to rebuild it)")
                continue
            count = len(fs.get_snapshot(conn, snapshot_id)["profiles"])
            if not labels_docx.DEFAULT_TEMPLATE_PATH.exists():
                st.write(f"{when} · Labels (no template on this machine to rebuild them)")
                continue
            per_doc = labels_docx.LABELS_PER_DOCUMENT
            for index, start in enumerate(range(0, count, per_doc)):
                suffix = f" {start + 1}-{min(start + per_doc, count)}" if count > per_doc else ""
                st.download_button(
                    f"{when} · Labels{suffix} (.docx)",
                    data=partial(_rebuild_labels, conn, snapshot_id, index),
                    file_name=f"Labels - {stem}{suffix} - sent {when[:10]}.docx",
                    mime=DOCX_MIME, on_click="ignore", key=f"sent_{snapshot_id}_{index}",
                )
