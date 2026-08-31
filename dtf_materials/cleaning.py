"""Pure parsing/cleaning functions for raw sheet values.

Every function here takes a raw string (or None) and returns either a clean
value or None if it couldn't be parsed — it never raises on bad input and
never guesses silently. Unparseable values are the data-quality report's job
to count, not this module's job to hide.
"""

from __future__ import annotations

import re
from datetime import datetime

_DATE_FORMATS = ["%m/%d/%Y", "%Y-%m-%d", "%b %d %Y"]

_CURRENCY_NOISE = re.compile(r"[\s$]|/kg\b", re.IGNORECASE)
_LOCATION_SPLIT = re.compile(r"[/,&]")


def clean_text(value: str | None) -> str | None:
    """Trim whitespace; empty string becomes None."""
    if value is None:
        return None
    text = value.strip()
    return text or None


def parse_date(value: str | None) -> str | None:
    """Parse a date in any of the sheet's observed formats to ISO yyyy-mm-dd.
    Returns None if blank or unrecognized."""
    text = clean_text(value)
    if text is None:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_price(value: str | None) -> float | None:
    """Parse a price cell that may carry a $ sign, a /kg suffix, stray
    whitespace, or a European decimal comma. Non-numeric text ("TBD",
    "call") returns None rather than raising."""
    text = clean_text(value)
    if text is None:
        return None
    cleaned = _CURRENCY_NOISE.sub("", text)
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")          # thousands separator
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")          # European decimal
    try:
        return round(float(cleaned), 4)
    except ValueError:
        return None


def parse_float(value: str | None) -> float | None:
    """Parse a plain numeric cell (stock quantities)."""
    text = clean_text(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_category(value: str | None) -> str | None:
    """Map the sheet's '1'/'2' (or '1 Raw'/'2 Flavor') to raw/flavor."""
    text = clean_text(value)
    if text is None:
        return None
    if text.startswith("1"):
        return "raw"
    if text.startswith("2"):
        return "flavor"
    return None


def parse_bool_flag(value: str | None) -> int:
    """Loose truthy parse for cells like Ready To Archive ('Yes', 'y', 'TRUE')."""
    text = clean_text(value)
    if text is None:
        return 0
    return 1 if text.strip().lower() in {"yes", "y", "true", "1"} else 0


def split_locations(value: str | None) -> list[str]:
    """Split a free-text Locations cell on '/', ',', '&' into individual
    location tokens, trimmed and de-duplicated, order preserved."""
    text = clean_text(value)
    if text is None:
        return []
    parts = [p.strip() for p in _LOCATION_SPLIT.split(text)]
    seen: dict[str, None] = {}
    for p in parts:
        if p:
            seen.setdefault(p, None)
    return list(seen.keys())


def normalize_key(value: str | None) -> str | None:
    """Case/whitespace-insensitive comparison key. Used to tell genuine
    differences ("NutraSci" vs "Nutra Sci", "Caffeine" vs "Creatine") apart
    from pure formatting noise (extra spaces, ALL CAPS) that shouldn't count
    as a real conflict."""
    text = clean_text(value)
    if text is None:
        return None
    return re.sub(r"\s+", " ", text).strip().casefold()


# Alias kept for call sites that name the specific use case.
normalize_supplier_key = normalize_key


if __name__ == "__main__":
    # Quick self-check against the exact dirty formats the generator injects.
    assert parse_date("5/1/2025") == "2025-05-01"
    assert parse_date("2024-03-04") == "2024-03-04"
    assert parse_date("Oct 19 2024") == "2024-10-19"
    assert parse_date("") is None
    assert parse_date(None) is None

    assert parse_price("117.38") == 117.38
    assert parse_price("$17.10/kg") == 17.10
    assert parse_price(" 18.00 ") == 18.00
    assert parse_price("17,10") == 17.10
    assert parse_price("$1,234.56") == 1234.56
    assert parse_price("TBD") is None
    assert parse_price("call") is None
    assert parse_price("") is None

    assert split_locations("B12 / A1, B1") == ["B12", "A1", "B1"]
    assert split_locations("A2 & Cooler 1 & C4") == ["A2", "Cooler 1", "C4"]
    assert split_locations("A1") == ["A1"]
    assert split_locations("") == []

    assert normalize_supplier_key("Sensapure Flavors") == normalize_supplier_key("sensapure flavors")
    assert normalize_supplier_key("NutraSci") != normalize_supplier_key("Nutra Sci")

    assert parse_category("1") == "raw"
    assert parse_category("2 Flavor") == "flavor"
    assert parse_category("") is None

    print("cleaning.py self-checks passed")
