"""The home header: what state the data is in, what needs a human, what the lab did.

Drawn above the tabs on every run, so it stays short: one caption with the
freshness and catalog size, three panels of numbers, and three collapsed
expanders for the detail. Every figure is read straight from the database
(`queries.dashboard`); none is estimated, and no delta is shown because no
run history exists to compare against.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from dtf_materials import flavor_sheets as fs
from dtf_materials import quality_report
from dtf_materials import queries as q
from ui.common import money, rows_to_df

# A sheet pull older than this is called out in the caption.
STALE_AFTER_DAYS = 7
EXPIRING_WINDOW_DAYS = 90


def render(conn: sqlite3.Connection, db_path: Path, summary: dict) -> None:
    """Draw the title, the freshness caption, the three panels and the detail expanders."""
    dash = q.dashboard(conn)

    st.title("Materials Master")
    st.caption("DTF R&D · Formulation & Inventory")
    st.markdown(_freshness_line(dash["ingested_at"], summary))

    attention, activity, inventory = st.columns(3)
    with attention.container(border=True):
        st.markdown("**Needs attention**")
        _metrics([
            ("On hold", f"{dash['on_hold']:,}"),
            (f"Expiring ≤ {EXPIRING_WINDOW_DAYS} d", f"{dash['expiring_90d']:,}"),
            ("No location", f"{dash['unlocated_lots']:,}"),
            ("No price", f"{dash['unpriced_materials']:,}"),
        ])
        st.caption("Stocked lots, except *No price*: catalog materials with no current price.")

    with activity.container(border=True):
        st.markdown("**R&D activity**")
        _metrics([
            ("Flavor sheets, 7 days", f"{dash['sheets_7d']:,}"),
            ("Downloads, 7 days", f"{dash['downloads_7d']:,}"),
            ("Flavor sheets, total", f"{dash['sheets_total']:,}"),
            ("Record sheets", f"{dash['record_sheets']:,}"),
        ])
        st.caption(_activity_line(dash["last_sheet"], dash["last_download"]))

    with inventory.container(border=True):
        st.markdown("**Inventory**")
        value = dash["stocked_value"]
        _metrics([
            ("Stocked lots", f"{dash['stocked_lots']:,}"),
            ("Sheet-reported value", _compact_money(value)),
            ("Raw / Flavor", f"{dash['raw_materials']:,} / {dash['flavor_materials']:,}"),
            ("Sample-only", f"{dash['sample_only']:,}"),
        ])
        uncosted = dash["stocked_lots_uncosted"]
        st.caption(
            f"Value is the sheet's own Total Cost column, {money(value)} over stocked lots"
            + (f"; {uncosted:,} stocked lot(s) have none." if uncosted else ".")
        )

    with st.expander(f"Expiring lots (next {EXPIRING_WINDOW_DAYS} days, soonest first)"):
        _render_expiring(conn)
    with st.expander("Recent flavor sheets"):
        _render_recent_sheets(conn)
    with st.expander("Data quality report"):
        _render_quality(db_path, dash["ingested_at"])


def _freshness_line(ingested_at: str | None, summary: dict) -> str:
    """Return the caption: when the sheet was pulled, then the catalog counts as an aside."""
    counts = (
        f"{summary['materials']:,} materials · {summary['lots']:,} lots · "
        f"{summary['locations']:,} locations · {summary['suppliers']:,} supplier spellings"
    )
    if ingested_at is None:
        return f":red[No inventory sheet loaded yet.] {counts}"
    pulled = datetime.fromisoformat(ingested_at).replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - pulled).days
    when = f"Inventory sheet pulled {pulled:%Y-%m-%d} ({_days_ago(days)})"
    if days > STALE_AFTER_DAYS:
        when = f":orange[{when}]"
    return f"{when} · {counts}"


def _days_ago(days: int) -> str:
    """Return 'today', 'yesterday' or 'N days ago'."""
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def _metrics(items: list[tuple[str, str]]) -> None:
    """Draw label/value pairs as metric tiles, two to a row."""
    for i in range(0, len(items), 2):
        for col, (label, value) in zip(st.columns(2), items[i:i + 2]):
            col.metric(label, value)


def _compact_money(value) -> str:
    """Return a dollar amount short enough for a metric tile ($4.93M, $12.5K, $850.00), or a dash."""
    if value is None:
        return "—"
    if value >= 1_000_000:
        return f"${value / 1_000_000:,.2f}M"
    if value >= 10_000:
        return f"${value / 1_000:,.1f}K"
    return money(value)


def _activity_line(sheet, snapshot) -> str:
    """Return 'Last sheet: Customer / Product, day · Last download: kind, day' with dashes for none."""
    if sheet is None:
        last_sheet = "—"
    else:
        last_sheet = f"{sheet['customer'] or '(no customer)'} / {sheet['product'] or '(no product)'}, {sheet['created_at'][:10]}"
    if snapshot is None:
        last_download = "—"
    else:
        kind = "flavor sheet" if snapshot["kind"] == "flavor_sheet" else snapshot["kind"]
        last_download = f"{kind}, {snapshot['taken_at'][:10]}"
    return f"Last sheet: {last_sheet} · Last download: {last_download}"


def _render_expiring(conn: sqlite3.Connection) -> None:
    """Draw the soonest-expiring stocked lots; a negative Days left is already expired."""
    rows = q.expiring_lots(conn, EXPIRING_WINDOW_DAYS)
    if not rows:
        st.write("No stocked lot expires in the window.")
        return
    st.dataframe(rows_to_df(rows, {
        "dtf_part_num": "Part #", "material_name": "Material", "dtf_lot_num": "DTF Lot #",
        "exp_date": "Expires", "days_left": "Days left", "current_stock": "Stock",
        "status": "Status", "locations_raw": "Locations",
    }), hide_index=True, width="stretch")
    st.caption("Top 20 by date. A far-off negative number is usually a mistyped year on the sheet.")


def _render_recent_sheets(conn: sqlite3.Connection) -> None:
    """Draw the five newest flavor sheets; open one from the Flavor Sheet tab."""
    rows = fs.list_sheets(conn)[:5]
    if not rows:
        st.write("No flavor sheets yet.")
        return
    st.dataframe(rows_to_df(rows, {
        "created_at": "Created", "customer": "Customer", "product": "Product",
        "quote_id": "Quote ID", "flavor_count": "Flavors",
    }), hide_index=True, width="stretch")


@st.cache_data(show_spinner="Reading the staged sheet…")
def _quality_headings(db_path: str, ingested_at: str | None) -> list[str]:
    """Return the quality report's section headings (each carries its count), cached per sheet pull."""
    if ingested_at is None:
        return []
    return [heading for heading, _ in quality_report.build_sections(quality_report.report(db_path))]


def _render_quality(db_path: Path, ingested_at: str | None) -> None:
    """Draw the quality report's headline counts and how to get the full report."""
    headings = _quality_headings(str(db_path), ingested_at)
    if not headings:
        st.write("Nothing flagged." if ingested_at else "No staged sheet to report on.")
    else:
        st.markdown("\n".join(f"- {h.rstrip(':')}" for h in headings))
    st.caption("Full report with source rows: `python -m dtf_materials.quality_report`")
