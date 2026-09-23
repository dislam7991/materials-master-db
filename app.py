"""Streamlit lookup app for the materials master database.

The everyday R&D question this answers: "I have a part number (or half a
material name) — where is it, what does it cost, who supplies it?"

All data access lives in dtf_materials/queries.py; this file is only UI.
Run with:  streamlit run app.py
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_searchbox import st_searchbox

from dtf_materials import flavor_sheets as fs
from dtf_materials import queries as q
from dtf_materials.db import DEFAULT_DB_PATH, SCHEMA_PATH
from dtf_materials.flavor_sheet_xlsx import DEFAULT_TEMPLATE_PATH as FLAVOR_TEMPLATE_PATH
from dtf_materials.flavor_sheet_xlsx import Flavor
from dtf_materials.flavor_sheet_xlsx import render as render_flavor_sheet

st.set_page_config(page_title="Materials Master", page_icon="~", layout="wide")


@st.cache_resource
def get_conn() -> sqlite3.Connection:
    """One connection reused across reruns. check_same_thread=False because
    Streamlit reruns scripts on its own threads.

    The lookup tabs only read; the Flavor Sheet tab writes through
    dtf_materials.flavor_sheets, which commits its own inserts. That's a single
    local writer — the same single-writer assumption the project chose SQLite
    on — so one shared connection is still correct. foreign_keys is ON so a
    line can only reference a material or lab sample that exists, and deleting
    a sheet cascades to its flavors and lines.

    The schema is applied on connect (every statement is CREATE ... IF NOT
    EXISTS), so a database built before a table was added gains it here
    instead of the tab crashing with "no such table" until the next ETL run."""
    conn = sqlite3.connect(DEFAULT_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


def money(value) -> str:
    return "—" if value is None else f"${value:,.2f}"


def rows_to_df(rows, columns: dict[str, str]) -> pd.DataFrame:
    """sqlite3.Rows -> DataFrame with friendly column names, keeping only the
    columns we mean to show."""
    data = [{label: r[key] for key, label in columns.items()} for r in rows]
    return pd.DataFrame(data, columns=list(columns.values()))


# Shown when the lab catalog hasn't been loaded. Flush-left because
# Streamlit renders this as markdown, and an indented line would become a
# code block. Kept as a constant rather than inline for the same reason.
NO_LAB_SAMPLES_MESSAGE = """
**No lab samples have been loaded into this database.**

The catalog lives in its own Google Sheet and is loaded by its own command,
separately from the warehouse inventory — so this tab can be empty while
everything above is full.

If a colleague gave you this database, ask them for an updated copy: the lab
samples travel inside the database file.

To load it yourself, add a `[lab_sheet]` section to `config.local.toml` (see
`config.example.toml`), then run:

```
python -m dtf_materials.lab_samples
```

There is no synthetic stand-in for this sheet, so a database built only from
the sample data will always show this message.
"""


if not Path(DEFAULT_DB_PATH).exists():
    st.error(
        f"No database at `{DEFAULT_DB_PATH}`.\n\n"
        "Build it first:\n\n"
        "```\npython scripts/generate_synthetic_sheet.py\npython -m dtf_materials.etl\n```"
    )
    st.stop()

conn = get_conn()
summary = q.database_summary(conn)

st.title("Materials Master Database")

stat_materials, stat_lots, stat_suppliers, stat_locations = st.columns(4)
stat_materials.metric("Materials", f"{summary['materials']:,}")
stat_lots.metric("Lots", f"{summary['lots']:,}")
stat_suppliers.metric("Supplier Spellings", f"{summary['suppliers']:,}")
stat_locations.metric("Locations", f"{summary['locations']:,}")

(tab_combined, tab_material, tab_location, tab_all, tab_lab,
 tab_flavor) = st.tabs(
    ["Warehouse + Lab", "Material lookup", "What's in a location",
     "All materials", "Lab Samples", "Flavor Sheet"]
)

with tab_combined:
    st.write(
        "One search over both catalogs, for when you don't yet know which one "
        "holds the answer: is this in the warehouse, in the lab, or both?"
    )

    if summary["lab_samples"] == 0:
        # Not an error state: the lab catalog comes from a different sheet
        # loaded by a different command, and a synthetic-only database never
        # has one. Results are still useful, just warehouse-only — say that
        # rather than letting every row read as "not in the lab".
        st.caption(
            "No lab samples are loaded in this database, so every result here "
            "can only come from the warehouse. See the **Lab Samples** tab."
        )

    combined_term = st.text_input(
        "Search by part number, material or flavor name, sample code, or vendor",
        placeholder="e.g. 15-009, mango, 7182011, sensapure",
        key="combined",
    ).strip()

    if not combined_term:
        st.info("Type anything either catalog might know it by.")
    else:
        found = q.search_warehouse_and_lab(conn, combined_term, limit=25)
        if not found:
            st.warning(f"Nothing matching “{combined_term}” in the warehouse or the lab.")
        else:
            table = []
            for result in found:
                # Each side's summary comes from the same queries the
                # single-source tabs use, so the two can't drift apart.
                warehouse = {"Stock": None, "Stocked locations": "—"}
                if result["material_id"] is not None:
                    stocked = q.get_stocked_locations(conn, result["material_id"])
                    warehouse = {
                        "Stock": q.total_stock(conn, result["material_id"]),
                        # Listed, not summed — this is where the material can
                        # be found, and no quantity is attached to it here.
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

                table.append({
                    "Where": result["kind"],
                    "Part #": result["dtf_part_num"] or "—",
                    "Material": result["material_name"] or "—",
                    **warehouse,
                    "Vendor": result["vendor"] or "—",
                    "Flavor": result["flavor_name"] or "—",
                    "Sample code": result["sample_code"] or "—",
                    **lab,
                    "Matched by": result["match_rule"] or "—",
                })

            st.dataframe(
                pd.DataFrame(table), hide_index=True, width="stretch",
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

with tab_material:
    def _material_options(term: str):
        return [
            (f"{r['dtf_part_num'] or '(no part #)'} — {r['material_name']}", r["material_id"])
            for r in q.search_materials(conn, term, limit=10)
        ]

    material_id = st_searchbox(
        _material_options,
        label="Search by DTF Part # or material name",
        placeholder="e.g. 15-009, caffeine, blueberry",
        key="material_searchbox",
    )

    # A stale pick (the database was rebuilt under an open page) finds no row.
    # No st.stop() here: it would end the whole run and blank every later tab.
    material = q.get_material(conn, material_id) if material_id else None
    if not material_id:
        st.info("Type a part number or part of a material name to begin.")
    elif material is None:
        st.warning("That material is no longer in the database. Search again.")
    else:

        st.subheader(f"{material['dtf_part_num'] or '(no part #)'} — {material['material_name']}")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Price / kilo", money(material["current_price_per_kilo"]))
        c2.metric("Supplier", material["supplier"] or "—")
        c3.metric("Category", (material["category"] or "—").title())
        c4.metric("Allergen", material["allergen"] or "—")

        st.markdown("#### Where it is")
        stocked = q.get_stocked_locations(conn, material["material_id"])
        if stocked:
            st.dataframe(
                rows_to_df(stocked, {
                    "location": "Location",
                    "stock": "Stock in lots here",
                    "lot_count": "Lots",
                }),
                hide_index=True, width="stretch",
            )
            st.caption(f"Total stock on hand: **{q.total_stock(conn, material['material_id']):,.2f}**")

            missing = q.unlocated_stock(conn, material["material_id"])
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
        else:
            # Nothing in stock — show the last known location rather than
            # a blank panel, clearly labelled so it isn't mistaken for
            # current inventory.
            last = q.get_last_known_locations(conn, material["material_id"])
            if last:
                locs = ", ".join(r["location"] for r in last)
                when = last[0]["receiving_date"] or "unknown date"
                st.warning(
                    f"No stock on hand. Last known location: **{locs}** "
                    f"(lot {last[0]['dtf_lot_num'] or '—'}, received {when})."
                )
            else:
                st.warning("No stock on hand, and no location recorded on any lot.")

        st.markdown("#### Lot history")
        lots = q.get_lots(conn, material["material_id"])
        st.dataframe(
            rows_to_df(lots, {
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

        history = q.price_history(conn, material["material_id"])
        if len(history) > 1:
            st.markdown("#### Price per kilo over time")
            st.caption(
                "Recorded per lot, so repricing between receivings is visible "
                "instead of being overwritten."
            )
            df = pd.DataFrame(
                [{"Received": r["receiving_date"], "Price/kg": r["price_per_kilo"]}
                 for r in history]
            ).set_index("Received")
            st.line_chart(df)

with tab_location:
    st.write("Look up a full location code, or an aisle prefix to see everything in it.")
    loc = st.text_input(
        "Location", placeholder="e.g. 6L-27-D, or just 6L", key="loc"
    ).strip()

    if not loc:
        st.info("Enter a location code or prefix.")
    else:
        found = q.search_by_location(conn, loc)
        if not found:
            st.warning(f"Nothing in stock at “{loc.upper()}”.")
        else:
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

with tab_all:
    st.write(
        "Every material in the database. Click a column header to sort, or use "
        "the search icon in the table's own toolbar to filter."
    )
    materials = q.list_materials(conn)
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
            for r in materials
        ]),
        hide_index=True, width="stretch",
        column_config={
            # Numbers stay numbers so sorting works right; only the display
            # is formatted (see money(): "$xx.xx" strings sort wrong, e.g.
            # "$100" before "$20").
            "Price/kg": st.column_config.NumberColumn(format="$%.2f"),
            "Stock on hand": st.column_config.NumberColumn(format="%.2f"),
        },
    )

with tab_lab:
    st.write(
        "The R&D lab's flavor sample catalog — a separate sheet from the "
        "warehouse inventory above, so results here are independent of it."
    )

    if summary["lab_samples"] == 0:
        # An empty catalog is a normal state, not a failure: it comes from a
        # different sheet loaded by a different command, and a
        # synthetic-only database never has any. Say so, rather than letting
        # every search quietly return nothing and read as broken.
        st.info(NO_LAB_SAMPLES_MESSAGE)
    else:

        def _lab_sample_options(term: str):
            return [
                (
                    f"{r['sample_code'] or '(no code)'} — {r['flavor_name'] or '(unnamed)'} "
                    f"({r['vendor'] or 'unknown vendor'})",
                    r["lab_sample_id"],
                )
                for r in q.search_lab_samples(conn, term, limit=10)
            ]

        lab_sample_id = st_searchbox(
            _lab_sample_options,
            label="Search by vendor, flavor name, sample code, or Part #",
            placeholder="e.g. 7182011, mango, sensapure",
            key="lab_sample_searchbox",
        )

        sample = q.get_lab_sample(conn, lab_sample_id) if lab_sample_id else None
        if not lab_sample_id:
            st.info("Type a vendor, flavor name, sample code, or part number to begin.")
        elif sample is None:
            st.warning("That lab sample is no longer in the database. Search again.")
        else:

            st.subheader(f"{sample['sample_code'] or '(no code)'} — {sample['flavor_name'] or '(unnamed)'}")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Vendor", sample["vendor"] or "—")
            c2.metric("Declaration Type", sample["declaration_type"] or "—")
            c3.metric("Lab Location", sample["location_lab"] or "—")
            c4.metric("Price / kilo", money(sample["price_per_kilo"]))

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


with tab_flavor:
    st.write(
        "Fill in the Flavor Sheet: customer, product, quote ID and servings once, "
        "then each flavor's materials in mg per serving. Grams per sample are "
        "worked out for you, and **Download** fills in the company template "
        "(four flavors to a page)."
    )

    # Which sheet and which flavor are open lives in the page URL
    # (?sheet=…&flavor=…), not in widget state. Widget state is lost whenever a
    # widget's label changes (the dropdown shows each sheet's flavor count), on
    # a reconnect, or on a reload; the URL survives all three, so the page
    # always comes back to the sheet you were working on.
    def _url_id(name: str) -> int | None:
        raw = st.query_params.get(name)
        return int(raw) if raw and raw.isdigit() else None

    def _open(sheet_id: int | None, flavor: int | str | None = None) -> None:
        """Point the URL at a sheet and, optionally, a flavor id or "new"."""
        st.query_params.clear()
        if sheet_id is not None:
            st.query_params["sheet"] = str(sheet_id)
        if flavor is not None:
            st.query_params["flavor"] = str(flavor)

    NEW_SHEET = "➕ Start a new flavor sheet…"
    saved_sheets = fs.list_sheets(conn)
    sheet_labels = {
        s["flavor_sheet_id"]: f"{fs.product_line(s) or '(untitled)'} · {s['created_at'][:10]}"
        for s in saved_sheets
    }
    sheet_options: list[int | str] = [NEW_SHEET, *sheet_labels]
    flavor_sheet_id = _url_id("sheet")
    if flavor_sheet_id not in sheet_labels:
        flavor_sheet_id = None
    picked_sheet = st.selectbox(
        "Flavor sheet",
        sheet_options,
        index=sheet_options.index(flavor_sheet_id if flavor_sheet_id is not None else NEW_SHEET),
        format_func=lambda c: NEW_SHEET if c == NEW_SHEET else sheet_labels[c],
    )
    selected_id: int | None = None if picked_sheet == NEW_SHEET else int(picked_sheet)  # type: ignore[arg-type]
    if selected_id != flavor_sheet_id:
        _open(selected_id)
        st.rerun()

    def _sheet_header_inputs(current, key: str) -> dict:
        """The five header fields, prefilled from `current` when editing."""
        def cur(field):
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

    def _profile_title(p) -> str:
        return " ".join(x for x in (p["flavor_name"], p["sample_id"]) if x) or "(unnamed flavor)"

    def _line_options(term: str):
        """Warehouse and Lab matches, then the typed fallback for a material
        neither catalog has yet (flagged on the line as not in catalog)."""
        def _price_tag(price) -> str:
            # Show the price while picking so cost-aware choices happen at the
            # point of choice; silent when the catalog has no price on file.
            return f" · {money(price)}/kg" if price is not None else ""

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

    def _add_picked_line(profile_id: int, picked) -> None:
        """Add the picked material straight away (mg filled in the table after),
        so picking is one step and the search box clears for the next one."""
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

    def _apply_line_edits(editor_key: str, lines: list[dict], version_key: str) -> None:
        """Save the table's edits the moment they're made (no Save click), then
        reset the table: its edit state is keyed by row position, which is
        stale once a row is removed or reordered."""
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

    @st.fragment
    def _flavor_editor(sheet, profile) -> None:
        """One flavor's editor. A fragment: searching, adding and editing lines
        rerun only this part of the page, not every tab — so each keystroke in
        the search box stays cheap and nothing else on the page moves."""
        pid = profile["flavor_profile_id"]
        servings = sheet["servings"]

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

        st_searchbox(
            _line_options,
            label="Add a material",
            placeholder="Search warehouse and lab by name, Part # or sample code — pick one to add it",
            key=f"add_pick_{pid}",
            clear_on_submit=True,
            submit_function=lambda picked: _add_picked_line(pid, picked),
            rerun_scope="fragment",
        )
        if f"line_error_{pid}" in st.session_state:
            st.error(st.session_state.pop(f"line_error_{pid}"))

        lines = fs.get_lines(conn, pid)
        if not lines:
            st.info("No materials yet. Search above and pick one to add it.")
        else:
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
                args=(editor_key, line_state, version_key),
                hide_index=True, width="stretch",
                disabled=["Material", "Source", "g / sample", "Price / kg", "Cost / serving"],
                column_config={
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
                },
            )
            base_g = fs.grams_per_sample(profile["base_mg"], servings)
            st.caption(
                "Type mg straight into the table; tick **Remove** to drop a line; change "
                "**#** to reorder. Changes save as you make them."
                + (f" BASE: {base_g:,.3f} g per sample." if base_g is not None else "")
            )

            # A rough material cost for the flavor, summed from the priced lines
            # only. The generated flavor sheet stays price-free; this estimate
            # lives in the builder to help judge a formula's cost as it's built.
            cost = fs.profile_cost(lines, servings)
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
                st.caption(estimate + ". Estimate only; BASE and unpriced lines are left out.")
            elif cost["unpriced"]:
                st.caption(
                    f"No prices on file for these {cost['unpriced']} material(s) yet, so no cost estimate."
                )

        with st.popover("Remove this flavor"):
            st.write(f"Remove **{_profile_title(profile)}** and its {len(lines)} lines?")
            if st.button("Remove", key=f"remove_{pid}", type="primary"):
                fs.delete_profile(conn, pid)
                _open(sheet["flavor_sheet_id"])
                st.rerun(scope="app")

    sheet = fs.get_sheet(conn, flavor_sheet_id) if flavor_sheet_id is not None else None

    if sheet is None:
        with st.form("new_flavor_sheet"):
            new_header = _sheet_header_inputs(None, "new_sheet")
            create_sheet = st.form_submit_button("Create flavor sheet", type="primary")
        if create_sheet:
            _open(fs.create_sheet(conn, **new_header))
            st.rerun()

    else:
        assert flavor_sheet_id is not None  # sheet is non-None only when flavor_sheet_id is
        sheet_id: int = flavor_sheet_id      # local binding so closures see a narrowed type
        with st.form(f"sheet_header_{sheet_id}"):
            header = _sheet_header_inputs(sheet, f"sheet_{sheet_id}")
            if st.form_submit_button("Save details"):
                fs.update_sheet(conn, sheet_id, **header)
                st.rerun()

        profiles = fs.get_profiles(conn, sheet_id)
        profile_ids = [p["flavor_profile_id"] for p in profiles]
        ADD_FLAVOR = "➕ Add a flavor"
        flavor_options = profile_ids + [ADD_FLAVOR]
        open_flavor = ADD_FLAVOR if st.query_params.get("flavor") == "new" else _url_id("flavor")
        if open_flavor not in flavor_options:
            open_flavor = profile_ids[-1] if profile_ids else ADD_FLAVOR
        picked_flavor = st.radio(
            "Flavor",
            flavor_options,
            index=flavor_options.index(open_flavor),
            format_func=lambda o: o if o == ADD_FLAVOR else next(
                f"{p['position']}. {_profile_title(p)}" for p in profiles if p["flavor_profile_id"] == o
            ),
            horizontal=True,
        )
        if picked_flavor != open_flavor:
            _open(sheet_id, "new" if picked_flavor == ADD_FLAVOR else picked_flavor)
            st.rerun()

        if picked_flavor == ADD_FLAVOR:
            # The Sample ID is suggested from the prefix, today's date and the
            # slot. "Start from" copies another flavor's BASE and lines — flavors
            # on a sheet usually share base, acids and sweeteners — and any line
            # that doesn't belong is one Remove tick away.
            next_position = len(profiles) + 1
            with st.form(f"add_flavor_{sheet_id}_{next_position}"):
                c1, c2, c3 = st.columns([3, 2, 3])
                new_flavor_name = c1.text_input("Flavor name", key=f"new_fname_{sheet_id}_{next_position}")
                new_sample_id = c2.text_input(
                    "Sample ID",
                    value=fs.suggest_sample_id(sheet["sample_prefix"], date.today(), next_position),
                    key=f"new_fsid_{sheet_id}_{next_position}",
                )
                copy_from = c3.selectbox(
                    "Start from",
                    [None] + profile_ids,
                    index=len(profile_ids),
                    format_func=lambda o: "Empty" if o is None else next(
                        f"Copy of {p['position']}. {_profile_title(p)}" for p in profiles
                        if p["flavor_profile_id"] == o
                    ),
                    key=f"new_fcopy_{sheet_id}_{next_position}",
                )
                if st.form_submit_button("Add flavor", type="primary"):
                    new_pid = fs.add_profile(
                        conn, sheet_id,
                        flavor_name=new_flavor_name.strip() or None,
                        sample_id=new_sample_id.strip() or None,
                        copy_lines_from=copy_from,
                    )
                    _open(sheet_id, new_pid)
                    st.rerun()
        else:
            _flavor_editor(sheet, next(p for p in profiles if p["flavor_profile_id"] == picked_flavor))

        st.divider()
        if sheet["servings"] is None:
            st.warning("Servings per flavor is blank, so the sheet can't work out any grams. Set it in the details above.")

        def _build_workbook() -> bytes:
            """Built when Download is clicked, from what's in the database at that
            moment — not on every rerun, and never from a stale copy."""
            current = fs.get_sheet(conn, sheet_id)
            assert current is not None
            return render_flavor_sheet(
                fs.product_line(current),
                current["servings"],
                [
                    Flavor(
                        title=_profile_title(p),
                        base_mg=p["base_mg"],
                        lines=[(l["name"], l["mg_per_serving"])
                               for l in fs.get_lines(conn, p["flavor_profile_id"])],
                    )
                    for p in fs.get_profiles(conn, sheet_id)
                ],
                FLAVOR_TEMPLATE_PATH,
            )

        c_download, c_delete = st.columns([3, 1])
        if not FLAVOR_TEMPLATE_PATH.exists():
            c_download.error(
                f"No Flavor Sheet template at `{FLAVOR_TEMPLATE_PATH}`. Copy the company "
                "template there (the folder is gitignored) to enable downloads."
            )
        elif profiles:
            file_stem = re.sub(r'[\\/:*?"<>|]+', "-", fs.product_line(sheet) or "untitled")
            c_download.download_button(
                "Download Flavor Sheet (.xlsx)",
                data=_build_workbook,
                file_name=f"Flavor Sheet - {file_stem}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                on_click="ignore",
            )

        with c_delete.popover("Delete this flavor sheet"):
            st.write(f"Delete **{fs.product_line(sheet) or '(untitled)'}** and all {len(profiles)} flavors?")
            if st.button("Delete", key=f"delete_sheet_{sheet_id}", type="primary"):
                fs.delete_sheet(conn, sheet_id)
                _open(None)
                st.rerun()
