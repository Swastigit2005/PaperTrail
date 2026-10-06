from papertrail.core.matching import Matcher
from tests.conftest import fake_llm

BRANDS = {"dolo": "paracetamol", "crocin": "paracetamol", "augmentin": "amoxicillin clavulanate"}


def canon(s: str) -> str:
    """Drug names (brand or generic) -> ingredient; anything else unchanged."""
    first = s.lower().split()[0]
    if first in BRANDS:
        return BRANDS[first]
    return first if first in {"paracetamol", "amoxicillin"} else s.lower()


def test_rules_exact_normalized_fuzzy_and_miss():
    m = Matcher(mode="rules")
    left = ["Paracetamol 650 mg", "Dolo 650", "Copier paper A4", "Stapler"]
    right = ["paracetamol 650 mg", "Copier Paper A4 500 sheets"]
    v = m.match(left, right, domain="test", normalize=canon)
    assert [x.method for x in v] == ["exact", "normalized", "fuzzy", "none"]
    assert v[1].right == 0 and v[2].right == 1 and v[3].right is None


def test_rules_mode_never_calls_llm():
    llm = fake_llm({"matches": []})
    m = Matcher(mode="rules", llm=llm, model="match-model")
    m.match(["A4 paper ream"], ["Copier paper A4 500 sheets"], domain="office supplies")
    assert llm.api.chat.completions.calls == []


def test_llm_fallback_only_for_unmatched():
    llm = fake_llm({"matches": [{"left": 0, "right": 1, "reason": "same goods"}]})
    m = Matcher(mode="rules+llm", llm=llm, model="match-model")
    v = m.match(["Toner", "A4 paper ream"], ["toner", "Copier paper A4 500 sheets"], domain="office supplies")
    assert v[0].method == "exact"
    assert v[1].method == "llm" and v[1].right == 1 and "same goods" in v[1].reason
    prompt = llm.api.chat.completions.calls[0]["messages"][1]["content"]
    assert "A4 paper ream" in prompt and "0. Toner" not in prompt  # only unmatched items sent
    assert m.llm_calls == 1


def test_llm_bad_indices_are_ignored():
    llm = fake_llm({"matches": [{"left": 5, "right": 0}, {"left": 0, "right": 99, "reason": "?"}]})
    m = Matcher(mode="rules+llm", llm=llm, model="match-model")
    v = m.match(["widget"], ["gadget"], domain="d")
    assert v[0].right is None


def test_unique_secondary_key_matches_only_when_unambiguous():
    m = Matcher(mode="rules")
    left = ["A4 paper ream", "Blue gel pens", "Black gel pens"]
    right = ["Copier paper A4 500 sheets", "Ball pen blue"]
    v = m.match(left, right, domain="d", left_keys=["4802", "9608", "9608"], right_keys=["4802", "9608"])
    assert (v[0].method, v[0].right) == ("key", 0)
    # key 9608 is shared by two invoice lines: each needs text corroboration (>= 75)
    assert v[1].method == "key" and v[1].right == 1  # "Blue gel pens" ~ "Ball pen blue": 77
    assert v[2].right is None  # "Black gel pens" ~ "Ball pen blue": 59
