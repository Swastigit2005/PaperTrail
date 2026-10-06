"""Date parsing (day-first, as on Indian documents) and sanity checks."""

from __future__ import annotations

import re
from datetime import date, datetime

_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d/%m/%y",
    "%d-%m-%y",
    "%d.%m.%y",
    "%d %b %Y",
    "%d %B %Y",
    "%d-%b-%Y",
    "%d-%B-%Y",
    "%b %d %Y",
    "%B %d %Y",
    "%d %b %y",
    "%d-%b-%y",
)


def parse_date(text: str) -> date:
    """Parse a date written in any common format; numeric dates are read day-first.

    Raises ValueError when the text is not a recognisable date.
    """
    cleaned = re.sub(r"(?i)^(date|dt|dated)\s*[:.\-]?\s*", "", text.strip())
    cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", cleaned)
    cleaned = cleaned.replace(",", " ").replace("'", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for fmt in _FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date: {text!r}")


def days_between(earlier: date, later: date) -> int:
    """Signed day count from ``earlier`` to ``later``."""
    return (later - earlier).days


def sanity_problem(
    value: date, *, today: date, earliest: date = date(2000, 1, 1), max_future_days: int = 1
) -> str | None:
    """Return a human-readable problem with a document date, or None if it looks plausible."""
    if value < earliest:
        return f"{value.isoformat()} is before {earliest.isoformat()}"
    if days_between(today, value) > max_future_days:
        return f"{value.isoformat()} is in the future"
    return None
