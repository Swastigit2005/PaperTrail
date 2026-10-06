from datetime import date

import pytest

from papertrail.core.crosscheck import CheckContext, CrossCheck, run_crosschecks
from papertrail.core.decision import DecisionPolicy, Outcome, decide
from papertrail.core.engine import DocumentInput, assign_doc_types, process_case
from papertrail.core.extraction.base import ExtractionResult
from papertrail.core.findings import Severity, hard, soft
from papertrail.core.matching import Matcher
from papertrail.core.pack import get_pack
from papertrail.packs.expenses.schemas import ExpenseClaim, Receipt
from tests.conftest import fake_llm, make_image
from tests.test_expenses_pack import claim_doc, receipt_doc

# --- decision ------------------------------------------------------------------------------


def test_decision_levels_and_reason_order():
    docs = {"receipt": receipt_doc()}
    policy = DecisionPolicy()
    assert decide([], docs, policy).outcome == Outcome.APPROVE
    assert decide([soft("s", "soft one", "receipt.total")], docs, policy).outcome == Outcome.NEEDS_REVIEW
    d = decide([soft("s", "soft"), hard("h", "hard", "receipt.total")], docs, policy)
    assert d.outcome == Outcome.FLAG and d.reasons[0].code == "h"


def test_review_only_demotes_hard():
    d = decide([hard("merchant_mismatch", "x")], {}, DecisionPolicy(review_only=frozenset({"merchant_mismatch"})))
    assert d.outcome == Outcome.NEEDS_REVIEW and d.reasons[0].severity == Severity.SOFT


def test_low_confidence_critical_field_needs_review():
    doc = receipt_doc(total_conf=0.4)
    d = decide([], {"receipt": doc}, DecisionPolicy(critical_fields={"receipt": ("total",)}))
    assert d.outcome == Outcome.NEEDS_REVIEW and d.reasons[0].fields == ["receipt.total"]


def test_wildcard_critical_field_missing():
    doc = receipt_doc()
    doc.items[0].amount.value = None
    doc.items[0].amount.null_reason = "illegible"
    d = decide([], {"receipt": doc}, DecisionPolicy(critical_fields={"receipt": ("items[*].amount",)}))
    assert d.reasons[0].code == "critical_field_missing" and "illegible" in d.reasons[0].message


# --- crosscheck engine -----------------------------------------------------------------------


def test_crashing_check_becomes_error_not_exception():
    def boom(docs, ctx):
        raise RuntimeError("bug")

    out = run_crosschecks([CrossCheck("boom", "explodes", boom)], {}, CheckContext(matcher=Matcher()))
    assert out[0].status == "error" and out[0].findings[0].severity == Severity.SOFT


# --- end to end with a fake extractor ------------------------------------------------------


class FakeExtractor:
    name = "fake"

    def __init__(self, docs):
        self.docs = docs

    def extract(self, image, doc_type, *, ocr_text=None):
        return ExtractionResult(doc_type=doc_type.name, data=self.docs[doc_type.name], pipeline="fake",
                                model="none", latency_s=0.0, usage={"total_tokens": 10, "requests": 1})


def test_process_case_end_to_end():
    pack = get_pack("expenses")
    ex = FakeExtractor({"receipt": receipt_doc(), "expense_claim": claim_doc()})
    ctx = CheckContext(matcher=Matcher(), case_id="t1", today=date(2026, 10, 6))
    inputs = [DocumentInput(make_image("r"), "receipt"), DocumentInput(make_image("c"), "expense_claim")]
    result = process_case(pack, inputs, ex, ctx)
    assert result.decision.outcome == Outcome.APPROVE
    assert result.usage["total_tokens"] == 20
    assert set(result.documents) == {"receipt", "expense_claim"}
    assert result.documents["receipt"].detection["method"] == "override"
    assert result.model_dump(mode="json")["decision"]["outcome"] == "approve"


def test_process_case_requires_both_documents():
    pack = get_pack("expenses")
    ex = FakeExtractor({"receipt": receipt_doc()})
    with pytest.raises(ValueError, match="needs documents"):
        process_case(pack, [DocumentInput(make_image(), "receipt")], ex, CheckContext(matcher=Matcher()))


def test_detection_elimination_when_both_look_alike():
    pack = get_pack("expenses")
    llm = fake_llm(['{"doc_type": "receipt", "confidence": 0.9}', '{"doc_type": "receipt", "confidence": 0.6}'])
    inputs = [DocumentInput(make_image("a")), DocumentInput(make_image("b", seed=3))]
    dets = assign_doc_types(pack, inputs, llm=llm, vision_model="vision-a")
    assert [d.doc_type for d in dets] == ["receipt", "expense_claim"]
    assert dets[1].method == "elimination"


def test_override_beats_detection():
    pack = get_pack("expenses")
    llm = fake_llm({"doc_type": "receipt", "confidence": 0.99})
    inputs = [DocumentInput(make_image("a")), DocumentInput(make_image("b", seed=2), doc_type="receipt")]
    dets = assign_doc_types(pack, inputs, llm=llm, vision_model="vision-a")
    assert dets[1].method == "override" and dets[0].doc_type == "expense_claim"


def test_schemas_parse_types():
    assert isinstance(receipt_doc(), Receipt) and isinstance(claim_doc(), ExpenseClaim)
