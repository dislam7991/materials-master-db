"""Smoke tests for the Streamlit app: every tab renders, and the Flavor Sheet
workflow runs from new sheet to flavor editor.

The app has no logic of its own worth unit-testing beyond `cost_caption` —
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
    assert len(app.tabs) == 6


def test_location_search_shows_a_table(app):
    app.text_input(key="loc").input("6L").run()
    assert not app.exception
    assert app.dataframe


def test_combined_search_shows_a_table(app):
    app.text_input(key="combined").input("a").run()
    assert not app.exception
    assert app.dataframe


def test_flavor_sheet_create_then_add_a_flavor(app):
    app.text_input(key="new_sheet_customer").input("Acme")
    app.number_input(key="new_sheet_servings").set_value(30)
    next(b for b in app.button if b.label == "Create flavor sheet").click().run()
    assert not app.exception
    assert app.query_params.get("sheet")

    next(b for b in app.button if b.label == "Add flavor").click().run()
    assert not app.exception
    assert app.query_params.get("flavor") not in (None, "new")
    assert any(b.label == "Save flavor" for b in app.button)


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


def test_cost_caption_labels_a_partial_estimate():
    from ui.flavor_sheet_tab import cost_caption

    cost = {"per_serving": 0.5, "per_sample": 15.0, "priced": 2, "unpriced": 1}
    caption = cost_caption(cost, servings=30)
    assert "$0.50 / serving" in caption and "$15.00 / sample" in caption
    assert "excludes 1 line(s) with no price on file" in caption


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
