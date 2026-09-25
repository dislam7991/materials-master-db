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

# A lab sample's RD-ID: exactly RD- then four digits. A typo'd id is rejected,
# not coerced, because it becomes a stable handle other rows point at.
_RD_ID = re.compile(r"^RD-\d{4}$")


def clean_text(value: str | None) -> str | None:
    """Return the trimmed text, or None if it's blank."""
    if value is None:
        return None
    text = value.strip()
    return text or None


def parse_date(value: str | None) -> str | None:
    """Return a date in any of the sheet's formats as ISO yyyy-mm-dd, or None if blank or unrecognized."""
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
    """Return a price cell ($, /kg, spaces or decimal comma allowed) as a float, or None for text like "TBD"."""
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
    """Return a plain numeric cell (a stock quantity) as a float, or None."""
    text = clean_text(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_category(value: str | None) -> str | None:
    """Return "raw" or "flavor" for the sheet's '1'/'2' (or '1 Raw'/'2 Flavor'), else None."""
    text = clean_text(value)
    if text is None:
        return None
    if text.startswith("1"):
        return "raw"
    if text.startswith("2"):
        return "flavor"
    return None


def parse_bool_flag(value: str | None) -> int:
    """Return 1 for a yes-like cell ('Yes', 'y', 'TRUE', '1'), else 0."""
    text = clean_text(value)
    if text is None:
        return 0
    return 1 if text.strip().lower() in {"yes", "y", "true", "1"} else 0


def split_locations(value: str | None) -> list[str]:
    """Return the individual locations in a Locations cell, upper-cased and de-duplicated.

    A token is split on spaces only when every piece is a location code:
    "6R-09-E 6R-10-C" is two locations, "back cooler" is one.
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
    """True if a location is a code like 6L-27-D or a known named location."""
    text = clean_text(location)
    if text is None:
        return False
    upper = text.upper()
    return bool(_LOCATION_CODE.match(upper)) or upper in KNOWN_NAMED_LOCATIONS


def is_valid_rd_id(value: str | None) -> bool:
    """True if the trimmed value is a well-formed RD-ID (RD-0000..RD-9999)."""
    text = clean_text(value)
    if text is None:
        return False
    return bool(_RD_ID.match(text))


def normalize_key(value: str | None) -> str | None:
    """Return a case- and whitespace-insensitive key, so "NutraSci " matches "NUTRASCI" but not "Nutra Sci"."""
    text = clean_text(value)
    if text is None:
        return None
    return re.sub(r"\s+", " ", text).strip().casefold()
