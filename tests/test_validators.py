from datetime import date

import pytest

from papertrail.core.fields import Field, known, missing
from papertrail.core.findings import Severity
from papertrail.core.validators.arithmetic import close, line_amount_check, sum_check
from papertrail.core.validators.dates import parse_date, sanity_problem
from papertrail.core.validators.duplicates import DuplicateIndex, fingerprint, hamming
from papertrail.core.validators.fuzzy import name_similarity, normalize_person_name, text_similarity
from papertrail.core.validators.gstin import checksum_char, make_gstin, same_state, validate_gstin

# --- GSTIN ---------------------------------------------------------------------------------

KNOWN_VALID = "27AAPFU0939F1ZV"  # widely published sample GSTIN (Maharashtra)


def test_known_valid_gstin():
    assert validate_gstin(KNOWN_VALID).valid
    assert validate_gstin("27 aapfu 0939 f1zv").valid  # normalised


@pytest.mark.parametrize(
    ("gstin", "reason"),
    [
        ("27AAPFU0939F1ZW", "checksum"),
        ("27AAPFU0939F1Z", "length"),
        ("2AAAPFU0939F1ZV", "format"),
        ("27AAPFU0939F0ZV", "format"),  # entity number cannot be 0
        ("27AAPFU0939F1XV", "format"),  # 14th char must be Z
        ("28AAPFU0939F1ZV", "checksum"),
        ("40AAPFU0939F1ZQ", "state"),
    ],
)
def test_invalid_gstins(gstin, reason):
    check = validate_gstin(gstin)
    assert not check.valid
    assert reason in check.reason


def test_make_gstin_roundtrip_and_state():
    g = make_gstin("29", "ABCDE1234F")
    assert validate_gstin(g).valid and g[:2] == "29" and g[-1] == checksum_char(g[:14])
    assert same_state(g, make_gstin("29", "PQRSX9876K")) is True
    assert same_state(g, KNOWN_VALID) is False
    assert same_state(g, "garbage") is None


# --- arithmetic ----------------------------------------------------------------------------


def test_close_tolerance():
    assert close(100.4, 100.0) and not close(102.0, 100.0)
    assert close(100_400.0, 100_000.0)  # 0.5% relative slack on large totals


def test_line_amount_check():
    paths = ("l.qty", "l.rate", "l.amount")
    assert line_amount_check(known(2.0), known(50.0), known(100.0), paths=paths) is None
    bad = line_amount_check(known(2.0), known(50.0), known(120.0), paths=paths)
    assert bad.severity == Severity.HARD and bad.detail["expected"] == 100.0
    assert line_amount_check(known(2.0), known(50.0), missing(), paths=paths).unverifiable


def test_sum_check():
    parts = [("a", known(10.0)), ("b", known(20.0))]
    assert sum_check(parts, ("t", known(30.0)), code="c", label="total") is None
    assert sum_check(parts, ("t", known(35.0)), code="c", label="total").severity == Severity.HARD
    assert sum_check(parts, ("t", missing()), code="c", label="total").unverifiable
    assert sum_check([("a", Field())], ("t", known(1.0)), code="c", label="total").unverifiable


# --- dates ---------------------------------------------------------------------------------


def test_parse_date_formats():
    assert parse_date("Date: 05-01-2026") == date(2026, 1, 5)
    assert parse_date("05.01.26") == date(2026, 1, 5)
    assert parse_date("Jan 5, 2026") == date(2026, 1, 5)
    with pytest.raises(ValueError):
        parse_date("yesterday")


def test_date_sanity():
    today = date(2026, 10, 6)
    assert sanity_problem(date(2026, 10, 1), today=today) is None
    assert "future" in sanity_problem(date(2026, 12, 1), today=today)
    assert "before" in sanity_problem(date(1999, 1, 1), today=today)


# --- fuzzy ---------------------------------------------------------------------------------


def test_person_names():
    assert normalize_person_name("Mr. Rahul  Sharma") == "rahul sharma"
    assert name_similarity("Mr. Rahul Sharma", "SHARMA RAHUL") == 100.0 or name_similarity(
        "Mr. Rahul Sharma", "SHARMA RAHUL"
    ) >= 92
    assert name_similarity("R. Sharma", "Rahul Sharma") >= 90
    assert name_similarity("Rahul Sharma", "Priya Nair") < 50


def test_text_similarity_subset():
    assert text_similarity("Copier paper A4", "Copier Paper A4 500 sheets") == 100.0
    assert text_similarity("stapler", "printer toner") < 60


# --- duplicates ----------------------------------------------------------------------------


def test_duplicate_index():
    idx = DuplicateIndex(max_distance=4)
    fp = fingerprint("Cafe Coffee Day", "2026-09-01", 450.0)
    assert fp == fingerprint("CAFE COFFEE DAY.", "2026-09-01", 450.0)
    idx.add("c1", 0b1010_1010, fp)
    assert idx.check("c1", 0b1010_1010, fp) == []  # same case is not its own duplicate
    hits = idx.check("c2", 0b1010_1011, None)
    assert hits[0].case_id == "c1" and hits[0].kind == "image"
    assert idx.check("c3", 0xFFFF_0000, fp)[0].kind == "fields"
    assert idx.check("c4", 0xFFFF_0000, "other") == []
    assert hamming(0b1111, 0b0000) == 4


def test_initials_any_order():
    assert name_similarity("A. Doctor", "Aahana Doctor") >= 90
    assert name_similarity("Y. Sethi", "Yashica Sethi") >= 90
    assert name_similarity("SETHI Y", "Yashica Sethi") >= 90
    assert name_similarity("A. Doctor", "Aahana Sethi") < 85
