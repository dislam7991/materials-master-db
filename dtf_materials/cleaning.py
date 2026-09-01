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
# Locations are separated by commas (the common case), sometimes a slash or
# semicolon. Deliberately NOT a bare space: a location may be free text
# ("back cooler"), and splitting on space would tear it in half.
_LOCATION_SPLIT = re.compile(r"[,;/]")

# A warehouse location code: hyphen-joined alphanumeric segments, e.g. 6L-27-D.
_LOCATION_CODE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$")

# Named locations that are legitimately words rather than codes.
KNOWN_NAMED_LOCATIONS = {"COOLER"}


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
        # Both separators present: the LAST one is the decimal separator.
        # "1,234.56" -> 1234.56   and   "1.234,56" -> 1234.56
        # (Deciding by presence alone silently turned "1.234,56" into 1.2346.)
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
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
    """Split a free-text Locations cell into individual locations.

    Handles the observed real formats: a single code ("6L-27-D"), several
    comma-separated codes ("6R-09-E, 6R-10-C, 6R-13-C"), a named location
    ("cooler"), and blank/NULL cells.

    Splitting on whitespace is conditional on purpose. "6R-09-E 6R-10-C" is
    two locations, but "back cooler" is one — so a token is only split on
    whitespace when EVERY resulting piece looks like a location code. When
    any piece doesn't, the token is left intact as free text rather than
    guessed at. Returned codes are upper-cased and de-duplicated so they
    match across rows; `lots.locations_raw` keeps the cell verbatim.
    """
    text = clean_text(value)
    if text is None:
        return []

    tokens: list[str] = []
    for raw_token in _LOCATION_SPLIT.split(text):
        token = re.sub(r"\s+", " ", raw_token).strip()
        if not token:
            continue
        pieces = token.split(" ")
        if len(pieces) > 1 and all(_LOCATION_CODE.match(p.upper()) for p in pieces):
            tokens.extend(pieces)
        else:
            tokens.append(token)

    seen: dict[str, None] = {}
    for t in tokens:
        seen.setdefault(t.upper(), None)
    return list(seen.keys())


def is_standard_location(location: str | None) -> bool:
    """True if a parsed location matches the expected code format (6L-27-D)
    or is a known named location. Free-text locations are legal but worth
    surfacing in the quality report — a run of them usually means either a
    typo or a real named location that belongs in KNOWN_NAMED_LOCATIONS."""
    text = clean_text(location)
    if text is None:
        return False
    upper = text.upper()
    return bool(_LOCATION_CODE.match(upper)) or upper in KNOWN_NAMED_LOCATIONS


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

# The self-checks that used to live here in an `if __name__ == "__main__"`
# block now live in `tests/test_cleaning.py`, so they run on every
# `python -m pytest` instead of only when someone remembered to execute this
# module by hand. Every case moved across unchanged, plus the known edge
# formats (two-digit years, "12.50 USD", trailing location separators).
