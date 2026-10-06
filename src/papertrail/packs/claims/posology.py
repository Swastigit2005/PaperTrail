"""Parse Indian prescription shorthand into numbers: doses per day, days, units per dose.

Used by the quantity-plausibility check (billed qty vs dose x frequency x duration).
Every parser returns None when the text is absent, ambiguous or "as needed" - callers then
skip the check rather than guess.
"""

from __future__ import annotations

import math
import re
from fractions import Fraction

_FREQ_WORDS: tuple[tuple[str, float | None], ...] = (
    (r"\b(sos|prn|as needed|as required|when required|stat)\b", None),
    (r"\b(qid|qds|four times|4 times|6 ?hourly|q6h|every 6 hours)\b", 4.0),
    (r"\b(tds|tid|thrice|three times|3 times|8 ?hourly|q8h|every 8 hours)\b", 3.0),
    (r"\b(bd|bid|twice|two times|2 times|12 ?hourly|q12h|every 12 hours)\b", 2.0),
    (r"\b(weekly|once a week|per week)\b", 1.0 / 7.0),
    (r"\b(od|qd|hs|once|1 time|daily|at bedtime|at night|every morning|24 ?hourly)\b", 1.0),
)
_PATTERN = re.compile(r"^\s*(\d+(?:/\d+)?|½)(\s*-\s*(\d+(?:/\d+)?|½)){2,3}\s*$")


def _num(token: str) -> float:
    return 0.5 if token == "½" else float(Fraction(token))


def doses_per_day(frequency: str | None) -> float | None:
    """'1-0-1' -> 2, 'BD' -> 2, 'TDS' -> 3, 'once daily' -> 1, 'SOS' -> None."""
    if not frequency:
        return None
    text = frequency.strip().lower().replace("–", "-")
    if _PATTERN.match(text):
        return sum(_num(t.strip()) for t in text.split("-")) or None
    for pattern, value in _FREQ_WORDS:
        if re.search(pattern, text):
            return value
    return None


def duration_days(duration: str | None) -> float | None:
    """'5 days' -> 5, 'x 1 week' -> 7, '2 wks' -> 14, '1 month' -> 30, '10D' -> 10."""
    if not duration:
        return None
    text = duration.strip().lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(d|day|days|w|wk|wks|week|weeks|m|mon|month|months)\b", text)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2)
    if unit.startswith("w"):
        return n * 7
    if unit.startswith("m"):
        return n * 30
    return n


def units_per_dose(dosage: str | None) -> float | None:
    """'1 tab' -> 1, '2 caps' -> 2, '1/2 tab' -> 0.5; liquids ('5 ml') -> None; missing -> 1."""
    if not dosage:
        return 1.0  # prescriptions usually omit "1 tab"
    text = dosage.strip().lower()
    if re.search(r"\b(ml|drops?|puffs?|units?|iu|g|gm|applications?)\b", text):
        return None  # sold by bottle/tube/vial: not countable against dose x days
    m = re.search(r"(\d+/\d+|\d+(?:\.\d+)?|½)", text)
    return _num(m.group(1)) if m else 1.0


def expected_units(dosage: str | None, frequency: str | None, duration: str | None) -> float | None:
    """Units needed for the full course, or None if any part is unknown."""
    per_dose, per_day, days = units_per_dose(dosage), doses_per_day(frequency), duration_days(duration)
    if per_dose is None or per_day is None or days is None:
        return None
    return per_dose * per_day * days


def allowed_units(expected: float) -> float:
    """Most a pharmacy may plausibly bill: rounded up to a full strip of 10 or 15, or +20%."""
    return max(math.ceil(expected / 10) * 10, math.ceil(expected / 15) * 15, expected * 1.2)
