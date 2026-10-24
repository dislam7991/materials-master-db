"""Smoke tests for the Streamlit app: every tab renders, and the Flavor Sheet
workflow runs from new sheet to flavor editor.

The app has no logic of its own worth unit-testing beyond `cost_caption` and `total_caption` —
queries.py and flavor_sheets.py are tested directly — so these only prove the
UI wiring holds together. Skipped where Streamlit isn't installed (CI installs
pytest only).
"""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit")

import streamlit as st
from streamlit.testing.v1 import AppTest

from dtf_materials import db, etl
from dtf_materials import flavor_sheets as fs
from dtf_materials.db import PROJECT_ROOT
from dtf_materials.sources import CsvInventorySource

APP_PATH = str(PROJECT_ROOT / "app.py")
SYNTHETIC_CSV = PROJECT_ROOT / "data" / "synthetic" / "raw_material_inventory.csv"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """A synthetic database in tmp_path that the app is pointed at."""
    path = tmp_path / "materials.db"
    etl.run(CsvInventorySource(SYNTHETIC_CSV), path)
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", path)
    st.cache_resource.clear()
    yield path
    st.cache_resource.clear()


@pytest.fixture
def app(db_path) -> AppTest:
    """A fresh app run against the synthetic database."""
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()
    return at


def test_every_tab_renders_without_error(app):
    assert not app.exception
    assert [t.label for t in app.tabs] == [
        "All Materials Lookup", "Warehouse Lookup", "R&D Lab Lookup",
        "Location Lookup", "Inventory Table", "Flavor Sheet", "Sample Record Sheet",
    ]


def test_location_search_shows_a_table(app):
    app.text_input(key="loc").input("6L").run()
    assert not app.exception
    assert app.dataframe


def test_combined_tab_prompts_until_a_pick_is_made(app):
    """The search box is a custom component AppTest can't type into, so the
    option builder is tested in test_queries; here, only that the tab asks."""
    assert not app.exception
    assert any("either catalog" in i.value for i in app.info)


def _param(at: AppTest, name: str) -> str | None:
    """Return a URL query param as a string.

    AppTest returned each value as a one-item list before Streamlit 1.65 and
    a plain string since. The app reads st.query_params, which is a string
    in every version, so the tests compare strings too.
    """
    value = at.query_params.get(name)
    return value[0] if isinstance(value, list) else value


def test_flavor_sheet_create_then_add_a_flavor(app):
    app.text_input(key="new_sheet_customer").input("Acme")
    app.number_input(key="new_sheet_servings").set_value(30)
    next(b for b in app.button if b.label == "Create flavor sheet").click().run()
    assert not app.exception
    assert _param(app, "sheet")

    next(b for b in app.button if b.label == "Add flavor").click().run()
    assert not app.exception
    assert _param(app, "flavor") not in (None, "new")
    assert any(b.label == "Save flavor" for b in app.button)


def _click(at: AppTest, label: str) -> AppTest:
    """Click the first button whose label contains `label`, run, and fail on any app exception."""
    next(b for b in at.button if label in b.label).click().run()
    assert not at.exception
    return at


def test_flavor_sheet_folders_browse_to_a_sheet_and_back_up(db_path):
    conn = db.init_db(db_path)
    fs.create_sheet(conn, customer="Acme", product="Pre-Workout", quote_id="Q-1", servings=30)
    newest = fs.create_sheet(conn, customer="Acme", product="Pre-Workout", quote_id="Q-1", servings=30)
    fs.add_profile(conn, newest, flavor_name="Mango")
    fs.create_sheet(conn, customer="Other", product="Bar", quote_id="Q-9")
    conn.close()

    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()
    _click(at, "📁 Acme · 2 sheets")
    _click(at, "📁 Pre-Workout · Q-1 · 2 sheets")
    # A new round started here copies the folder's newest header.
    assert next(t for t in at.text_input if t.label == "Quote ID").value == "Q-1"

    _click(at, "Mango")
    assert _param(at, "sheet") == str(newest)
    _click(at, "› 📁 Acme")
    assert not _param(at, "sheet")
    assert any("Pre-Workout · Q-1" in b.label for b in at.button)


def test_flavor_sheet_search_opens_a_match(db_path):
    conn = db.init_db(db_path)
    sheet = fs.create_sheet(conn, customer="Acme", product="Pre-Workout", quote_id="Q-1")
    fs.add_profile(conn, sheet, flavor_name="Blue Raspberry")
    conn.close()

    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()
    at.text_input(key="flavor_sheet_search").input("raspberry").run()
    _click(at, "📄 Acme › Pre-Workout · Q-1")
    assert _param(at, "sheet") == str(sheet)
    # An open sheet hides the search; the breadcrumbs bring it back, cleared.
    assert not any(t.label == "Search flavor sheets" for t in at.text_input)
    _click(at, "All customers")
    assert not _param(at, "sheet")
    assert at.text_input(key="flavor_sheet_search").value == ""


def _edit_details(at: AppTest):
    """Return the "Edit details" popover block."""
    return next(p for p in at.get("popover") if p.proto.popover.label == "Edit details")


def _assert_details_only_in_popover(at: AppTest, key_prefix: str) -> None:
    """Every details field (key starting `key_prefix`) is inside the Edit details popover, none outside."""
    inside = {w.key for w in [*_edit_details(at).text_input, *_edit_details(at).number_input]}
    every = {w.key for w in [*at.text_input, *at.number_input] if (w.key or "").startswith(key_prefix)}
    assert every and every <= inside


def test_flavor_sheet_details_show_as_one_line_and_edit_in_a_popover(db_path):
    conn = db.init_db(db_path)
    sheet = fs.create_sheet(conn, customer="Acme", quote_id="Q-1", servings=30)
    conn.close()

    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.query_params["sheet"] = str(sheet)
    at.run()
    assert any(m.value == "**Acme · (no product) · Q-1 · 30 servings · (no prefix)**" for m in at.markdown)
    _assert_details_only_in_popover(at, f"sheet_{sheet}_")

    _edit_details(at).text_input(key=f"sheet_{sheet}_product").input("Pre-Workout")
    _click(at, "Save details")
    assert any(m.value == "**Acme · Pre-Workout · Q-1 · 30 servings · (no prefix)**" for m in at.markdown)
    conn = db.init_db(db_path)
    assert fs.get_sheet(conn, sheet)["product"] == "Pre-Workout"
    conn.close()


def test_new_sheet_from_an_open_one_offers_its_flavors_under_start_from(db_path):
    conn = db.init_db(db_path)
    old = fs.create_sheet(conn, customer="Acme", product="Pre-Workout", quote_id="Q-1", servings=30)
    fs.add_profile(conn, old, flavor_name="Peach", sample_id="X260927-01")
    conn.close()

    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.query_params["sheet"] = str(old)
    at.run()
    _click(at, "Create flavor sheet")   # the "from this one" form: the only one on an open sheet
    new = _param(at, "sheet")
    assert new and new != str(old)
    assert at.text_input(key=f"sheet_{new}_quote").value == "Q-1"
    assert "Copy of Acme › Pre-Workout · Q-1 › 1. Peach_X260927-01" in at.selectbox(
        key=f"new_fcopy_{new}_1").options

def test_open_flavor_shows_its_lines_and_cost(db_path):
    conn = db.init_db(db_path)
    sheet_id = fs.create_sheet(conn, customer="Acme", servings=30)
    profile_id = fs.add_profile(conn, sheet_id, flavor_name="Mango")
    material_id = conn.execute("SELECT material_id FROM materials LIMIT 1").fetchone()[0]
    fs.add_line(conn, profile_id, material_id=material_id, mg_per_serving=100.0)
    fs.add_line(conn, profile_id, typed_name="Mystery powder", mg_per_serving=50.0)
    conn.close()

    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.query_params["sheet"] = str(sheet_id)
    at.query_params["flavor"] = str(profile_id)
    at.run()

    assert not at.exception
    assert any("Mystery powder" in str(df.value) for df in at.dataframe)
    assert any("Rough material cost" in c.value or "No prices on file" in c.value for c in at.caption)


def test_open_flavor_shows_its_total_under_the_table(db_path):
    conn = db.init_db(db_path)
    sheet_id = fs.create_sheet(conn, customer="Acme", servings=4)
    profile_id = fs.add_profile(conn, sheet_id, flavor_name="Peach", base_mg=12000)
    fs.add_line(conn, profile_id, typed_name="Peach flavor", mg_per_serving=350)
    conn.close()

    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.query_params["sheet"] = str(sheet_id)
    at.query_params["flavor"] = str(profile_id)
    at.run()

    assert not at.exception
    assert any(
        "Total (BASE + 1 line): 12,350 mg / serving = 49.400 g per sample" in c.value
        for c in at.caption
    )


def test_flavor_list_marks_complete_flavors_and_opens_one(db_path):
    conn = db.init_db(db_path)
    sheet_id = fs.create_sheet(conn, customer="Acme", servings=30)
    done = fs.add_profile(conn, sheet_id, flavor_name="Mango", base_mg=9000)
    fs.add_line(conn, done, typed_name="Mango flavor", mg_per_serving=300)
    blank = fs.add_profile(conn, sheet_id, flavor_name="Peach", base_mg=9000)
    fs.add_line(conn, blank, typed_name="Peach flavor")
    conn.close()

    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.query_params["sheet"] = str(sheet_id)
    at.query_params["flavor"] = str(blank)
    at.run()

    assert not at.exception
    labels = [b.label for b in at.button]
    assert "1. Mango ✓" in labels and "2. Peach ⚠" in labels and "➕ Add a flavor" in labels
    assert not at.radio
    _click(at, "1. Mango")
    assert _param(at, "flavor") == str(done)
    assert at.text_input(key=f"pname_{done}").value == "Mango"


def test_total_caption_names_what_it_leaves_out():
    from ui.flavor_sheet_tab import total_caption

    full = {"mg": 12350.0, "grams": 49.4, "lines": 3, "blank_lines": 0, "base_set": True}
    assert total_caption(full) == "**Total (BASE + 3 lines): 12,350 mg / serving = 49.400 g per sample**."
    partial = {"mg": 200.5, "grams": None, "lines": 1, "blank_lines": 2, "base_set": False}
    assert total_caption(partial) == "**Total (1 line): 200.5 mg / serving** — BASE not set, 2 lines have no mg yet."
    assert total_caption({"mg": None, "grams": None, "lines": 0, "blank_lines": 1, "base_set": False}) is None


def test_record_sheet_tab_shows_the_copy_block(db_path):
    conn = db.init_db(db_path)
    sheet_id = fs.create_sheet(conn, customer="Acme", servings=30)
    profile_id = fs.add_profile(conn, sheet_id, flavor_name="Mango")
    fs.add_line(conn, profile_id, typed_name="Mystery powder", mg_per_serving=50.0)
    conn.close()

    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()

    assert not at.exception
    assert any("Mystery powder\t50\t1\t0\t50" in c.value for c in at.code)
    assert any("Mystery powder" in w.value for w in at.warning)


def test_record_sheet_flavor_dropdown_uses_the_underscore_label(db_path):
    conn = db.init_db(db_path)
    sheet_id = fs.create_sheet(conn, customer="Acme", servings=30)
    fs.add_profile(conn, sheet_id, flavor_name="Peach", sample_id="X260927-01")
    conn.close()

    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()

    assert not at.exception
    assert "Peach_X260927-01" in at.selectbox(key="record_flavor").options


def test_flavor_label_joins_name_and_sample_id_with_an_underscore():
    from ui.sample_record_tab import flavor_label

    assert flavor_label({"flavor_name": "Peach", "sample_id": "X260927-01"}) == "Peach_X260927-01"
    assert flavor_label({"flavor_name": "Peach", "sample_id": None}) == "Peach"
    assert flavor_label({"flavor_name": None, "sample_id": None}) == "(unnamed)"


def test_scoops_per_serving_is_remembered_for_the_product(db_path):
    conn = db.init_db(db_path)
    sheet_id = fs.create_sheet(conn, customer="Acme", product="Pre-Workout", servings=30)
    fs.add_profile(conn, sheet_id, flavor_name="Mango")
    conn.close()

    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.query_params["sheet"] = str(sheet_id)
    at.run()
    scoops = next(n for n in at.number_input if n.label == "Scoops per serving")
    assert scoops.value == 1
    scoops.set_value(2.0).run()

    assert not at.exception
    conn = db.connect(db_path)
    assert fs.scoops_per_serving(conn, "Pre-Workout") == 2
    conn.close()


def test_cost_caption_labels_a_partial_estimate():
    from ui.flavor_sheet_tab import cost_caption

    cost = {"per_serving": 0.5, "per_sample": 15.0, "priced": 2, "unpriced": 1}
    caption = cost_caption(cost, servings=30)
    assert "\\$0.50 / serving" in caption and "\\$15.00 / sample" in caption
    assert "excludes 1 line(s) with no price on file" in caption


def test_cost_caption_escapes_every_dollar():
    """Two bare `$` in one caption render the text between them as LaTeX (F13)."""
    import re

    from ui.flavor_sheet_tab import cost_caption

    for servings, per_sample in ((30, 15.0), (None, None)):
        cost = {"per_serving": 0.5, "per_sample": per_sample, "priced": 2, "unpriced": 1}
        caption = cost_caption(cost, servings=servings)
        assert "\\$0.50" in caption
        assert not re.search(r"(?<!\\)\$", caption)


def test_cost_caption_asks_for_servings_and_handles_no_prices():
    from ui.flavor_sheet_tab import cost_caption

    no_servings = {"per_serving": 0.5, "per_sample": None, "priced": 1, "unpriced": 0}
    assert "set servings per flavor" in cost_caption(no_servings, servings=None)
    unpriced = {"per_serving": None, "per_sample": None, "priced": 0, "unpriced": 2}
    assert cost_caption(unpriced, servings=30).startswith("No prices on file for these 2")
    empty = {"per_serving": None, "per_sample": None, "priced": 0, "unpriced": 0}
    assert cost_caption(empty, servings=30) is None


def test_overlapping_runs_do_not_share_a_connection(db_path):
    """Streamlit reruns overlap on threads; a connection shared between them returns missing rows."""
    import threading

    from dtf_materials import queries as q
    from ui.common import get_conn

    assert get_conn(db_path) is not get_conn(db_path)

    errors: list[str] = []

    def one_run() -> None:
        """Act like one page run: open its connection, then query repeatedly."""
        conn = get_conn(db_path)
        for _ in range(200):
            try:
                q.database_summary(conn)
            except Exception as exc:  # the old shared connection raised TypeError/IndexError here
                errors.append(repr(exc))

    threads = [threading.Thread(target=one_run) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_record_sheet_builder_creates_then_shows_lines_totals_and_blocks(db_path):
    from dtf_materials import record_sheets as rs

    conn = db.init_db(db_path)
    mid = conn.execute("SELECT material_id FROM materials WHERE dtf_part_num IS NOT NULL LIMIT 1").fetchone()[0]
    sheet_id = fs.create_sheet(conn, customer="Acme", servings=30)
    profile_id = fs.add_profile(conn, sheet_id, flavor_name="Mango", base_mg=9000)
    fs.add_line(conn, profile_id, typed_name="Mystery powder", mg_per_serving=50.0)
    conn.close()

    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()
    assert at.selectbox(key="record_open").value == "➕ New record sheet"
    at.text_input(key="new_record_brand").input("Acme")
    at.number_input(key="new_record_base").set_value(1000)
    at.selectbox(key="new_record_flavor").set_value(profile_id)
    next(b for b in at.button if b.label == "Create record sheet").click().run()
    assert not at.exception

    conn = db.init_db(db_path)
    (record,) = rs.list_records(conn)
    assert (record["brand"], record["servings_per_unit"], record["flavor_profile_id"]) == ("Acme", 5, profile_id)
    line_id = rs.add_line(conn, record["record_sheet_id"], "active", material_id=mid)
    rs.update_line(conn, line_id, label_claim_mg=1000, activity=0.234, overage=0.10, position=1)
    conn.close()

    at.run()
    assert not at.exception
    assert at.selectbox(key="record_open").value == record["record_sheet_id"]
    assert any("\t1000\t0.234\t0.1\t4700.855\t" in c.value for c in at.code)
    assert any("Mystery powder\t50\t1\t0\t50" in c.value for c in at.code)
    assert any(m.label == "Serving size (mg)" and m.value == "4,750.855" for m in at.metric)


def test_record_sheet_details_show_as_one_line_and_edit_in_a_popover(db_path):
    from dtf_materials import record_sheets as rs

    conn = db.init_db(db_path)
    rid = rs.create_record(conn, brand="Acme", quote_id="Q-1", servings_per_unit=5)
    conn.close()

    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()
    at.selectbox(key="record_open").set_value(rid).run()
    assert not at.exception
    line = "**Acme · (no product) · Q-1 · 5 servings / unit · (no base) · (no flavor)**"
    assert any(m.value == line for m in at.markdown)
    _assert_details_only_in_popover(at, f"record_{rid}_")

    _edit_details(at).number_input(key=f"record_{rid}_base").set_value(1000)
    _click(at, "Save details")
    line = "**Acme · (no product) · Q-1 · 5 servings / unit · base 1000 · (no flavor)**"
    assert any(m.value == line for m in at.markdown)
    conn = db.init_db(db_path)
    assert rs.get_record(conn, rid)["servings_base"] == 1000
    conn.close()


def test_record_line_edits_save_activity_and_overage_as_typed_fractions(db_path, monkeypatch):
    """The data editor can't be typed into from AppTest, so its on_change
    handler is driven directly with the edits Streamlit would hand it. The
    values are the PL Cost Sheet's fractions, which must land unscaled (F12)."""
    from dtf_materials import record_sheets as rs
    from ui import sample_record_tab as tab

    conn = db.init_db(db_path)
    mid = conn.execute(
        "SELECT material_id FROM materials WHERE current_price_per_kilo IS NOT NULL LIMIT 1"
    ).fetchone()[0]
    rid = rs.create_record(conn, servings_per_unit=5, servings_base=1000)
    keep, _ = (rs.add_line(conn, rid, "active", material_id=mid) for _ in range(2))
    lines = [dict(l) for l in rs.get_lines(conn, rid, "active")]
    state = {"ed": {"edited_rows": {"0": {"Label claim (mg)": 24.75, "Activity %": 0.99, "Overage %": 0.1},
                                    "1": {"Remove": True}}}}
    monkeypatch.setattr(tab.st, "session_state", state)

    tab._apply_edits(conn, "ed", lines, "ver", "err")

    (line,) = rs.get_lines(conn, rid, "active")
    assert line["record_line_id"] == keep
    assert (line["label_claim_mg"], line["activity"], line["overage"]) == (24.75, 0.99, 0.1)
    record = next(r for r in rs.list_records(conn) if r["record_sheet_id"] == rid)
    (row,) = rs.compute(rs.all_lines(conn, record), record["servings_per_unit"], record["servings_base"])["lines"]
    assert row["actual_input_mg"] == pytest.approx(27.5)
    assert row["g_per_run"] == pytest.approx(27.5)
    assert row["formula_pct"] == pytest.approx(1.0)
    assert row["cost_per_unit"] == pytest.approx(27.5 / 1_000_000 * row["price_per_kilo"] * 5)
    shown = tab._display_row(row)
    assert (shown["Activity %"], shown["Overage %"], shown["Formula %"]) == (0.99, 0.1, 100)
    assert state["ver"] == 1


def test_record_price_edits_save_on_a_typed_line_only(db_path, monkeypatch):
    from dtf_materials import record_sheets as rs
    from ui import sample_record_tab as tab

    conn = db.init_db(db_path)
    mid = conn.execute("SELECT material_id FROM materials LIMIT 1").fetchone()[0]
    rid = rs.create_record(conn)
    rs.add_line(conn, rid, "active", material_id=mid)
    rs.add_line(conn, rid, "active", typed_name="Green Tea Extract (98% L-Theanine)")
    catalog, typed = (dict(l) for l in rs.get_lines(conn, rid, "active"))
    state = {"ed": {"edited_rows": {"0": {"Price / kg": 1.0}, "1": {"Price / kg": 60.0}}}}
    monkeypatch.setattr(tab.st, "session_state", state)

    tab._apply_edits(conn, "ed", [catalog, typed], "ver", "err")

    after = {l["source"]: l["price_per_kilo"] for l in rs.get_lines(conn, rid, "active")}
    assert after["Typed"] == 60.0
    assert after["Warehouse"] == catalog["price_per_kilo"]
    assert "catalog" in state["err"]


def test_record_search_offers_a_typed_material():
    from ui.sample_record_tab import _line_options

    conn = db.init_db(":memory:")
    options = _line_options(conn, " Green Tea Extract ")
    assert options[-1] == ("✎ Use “Green Tea Extract” as typed — not in either catalog",
                           ("typed", "Green Tea Extract"))
    assert _line_options(conn, "  ") == []


def test_home_header_shows_freshness_panels_and_expanders(app):
    """The old four-count header is now a caption; the panels and expanders draw without error."""
    assert app.title[0].value == "Materials Master"
    assert any("Inventory sheet pulled" in m.value for m in app.markdown)
    assert {m.label for m in app.metric} >= {"On hold", "No location", "Stocked lots", "Downloads, 7 days"}
    assert [e.label for e in app.expander][:3] == [
        "Expiring lots (next 90 days, soonest first)", "Recent flavor sheets", "Data quality report",
    ]
