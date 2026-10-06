from datetime import date

from pydantic import BaseModel

from papertrail.core.fields import Field, LenientDate, Number, doc_field, doc_list, iter_fields, known, wildcard


class Line(BaseModel):
    name: Field[str] = doc_field("name")
    amount: Field[Number] = doc_field("amount")


class Doc(BaseModel):
    title: Field[str] = doc_field("title")
    when: Field[LenientDate] = doc_field("date")
    lines: list[Line] = doc_list("lines")


def test_full_field_object_parses():
    f = Field[Number].model_validate({"value": "₹ 1,23,456.50", "confidence": 0.8, "evidence": "₹ 1,23,456.50"})
    assert f.value == 123456.5 and f.confidence == 0.8 and f.null_reason is None


def test_bare_value_gets_neutral_confidence():
    f = Field[str].model_validate("Apollo Pharmacy")
    assert f.value == "Apollo Pharmacy" and f.confidence == 0.5


def test_unparseable_value_becomes_null_with_reason():
    f = Field[Number].model_validate({"value": "illegible", "confidence": 0.9})
    assert f.value is None and f.confidence == 0.0
    assert "unparseable" in f.null_reason


def test_null_without_reason_gets_default_reason():
    f = Field[str].model_validate({"value": None, "confidence": 0.7})
    assert f.null_reason == "not found in document"


def test_value_clears_stale_null_reason_and_blank_is_null():
    assert Field[str].model_validate({"value": "x", "null_reason": "illegible"}).null_reason is None
    blank = Field[str].model_validate({"value": "   ", "confidence": 0.9})
    assert blank.value is None and blank.null_reason == "empty value"


def test_confidence_coercion():
    assert Field[str].model_validate({"value": "a", "confidence": "high"}).confidence == 0.9
    assert Field[str].model_validate({"value": "a", "confidence": 85}).confidence == 0.85
    assert Field[str].model_validate({"value": "a", "confidence": "70%"}).confidence == 0.7
    assert Field[str].model_validate({"value": "a", "confidence": -3}).confidence == 0.0


def test_day_first_dates():
    assert Field[LenientDate].model_validate({"value": "03/09/2026"}).value == date(2026, 9, 3)
    assert Field[LenientDate].model_validate({"value": "2026-09-03"}).value == date(2026, 9, 3)
    assert Field[LenientDate].model_validate({"value": "3rd Sep 2026"}).value == date(2026, 9, 3)


def test_missing_attributes_default_to_null_and_iter_paths():
    doc = Doc.model_validate({"title": {"value": "T", "confidence": 1}, "lines": [{"name": "a"}]})
    assert doc.when.value is None and doc.when.null_reason == "not returned by extractor"
    paths = [p for p, _ in iter_fields(doc)]
    assert paths == ["title", "when", "lines[0].name", "lines[0].amount"]
    assert wildcard("lines[12].amount") == "lines[*].amount"


def test_known_helper():
    assert known(5.0).confidence == 1.0
