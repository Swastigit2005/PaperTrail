"""Fuzzy string helpers: person names (honorifics, initials) and free-text descriptions."""

from __future__ import annotations

import re

from rapidfuzz import fuzz

HONORIFICS = frozenset(
    {"mr", "mrs", "ms", "miss", "dr", "shri", "sri", "smt", "kumari", "km", "master", "prof", "mx"}
)


def normalize_text(text: str) -> str:
    """Lowercase, replace punctuation with spaces and collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def normalize_person_name(name: str) -> str:
    """Normalise a person's name: drop honorifics and punctuation."""
    tokens = [t for t in normalize_text(name).split() if t not in HONORIFICS]
    return " ".join(tokens)


def _initials_compatible(a: list[str], b: list[str]) -> bool:
    """'r sharma' vs 'rahul sharma': same token count, every token equal or initial-compatible."""
    if len(a) != len(b):
        return False
    for x, y in zip(sorted(a, key=len, reverse=True), sorted(b, key=len, reverse=True), strict=True):
        if x == y:
            continue
        if (len(x) == 1 and y.startswith(x)) or (len(y) == 1 and x.startswith(y)):
            continue
        return False
    return True


def name_similarity(a: str, b: str) -> float:
    """Similarity 0-100 between two person names, robust to order, titles and initials."""
    na, nb = normalize_person_name(a), normalize_person_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 100.0
    ta, tb = na.split(), nb.split()
    if _initials_compatible(ta, tb) or _initials_compatible(sorted(ta), sorted(tb)):
        return 92.0
    return float(fuzz.token_sort_ratio(na, nb))


def text_similarity(a: str, b: str) -> float:
    """Similarity 0-100 for short descriptions (token-set: subset-tolerant)."""
    return float(fuzz.token_set_ratio(normalize_text(a), normalize_text(b)))
