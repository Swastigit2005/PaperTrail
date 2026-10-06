import json

import pytest

from papertrail.core.extraction import OcrThenLLMExtractor, VLMDirectExtractor, build_extractor
from papertrail.core.extraction.detect import detect_doc_type
from papertrail.core.extraction.ocr_then_llm import OcrResult, cap_confidence
from papertrail.core.extraction.prompts import template
from papertrail.packs.expenses.schemas import EXPENSE_CLAIM, RECEIPT, Receipt
from tests.conftest import fake_llm, make_image, make_settings

RECEIPT_JSON = {
    "merchant": {"value": "Cafe Aroma", "confidence": 0.95, "evidence": "CAFE AROMA"},
    "date": {"value": "2026-09-01", "confidence": 0.9, "evidence": "01/09/2026"},
    "items": [{"description": {"value": "Coffee", "confidence": 0.9, "evidence": "Coffee"},
               "amount": {"value": 180, "confidence": 0.9, "evidence": "180.00"}}],
    "total": {"value": "₹180.00", "confidence": 0.9, "evidence": "Total 180.00"},
    "payment_mode": {"value": None, "confidence": 0.8, "null_reason": "not present"},
}


def test_template_is_compact_and_complete():
    t = template(Receipt)
    assert '"merchant": F<string>' in t and '"total": F<number>' in t and '"date": F<YYYY-MM-DD>' in t
    assert '"items": [{' in t and '"amount": F<number>' in t
    assert len(t) < 1200  # far smaller than the JSON Schema


def test_vlm_direct_sends_image_and_parses():
    llm = fake_llm(RECEIPT_JSON)
    ex = VLMDirectExtractor(llm, "vision-a")
    res = ex.extract(make_image(), RECEIPT)
    call = llm.api.chat.completions.calls[0]
    assert call["model"] == "vision-a"
    assert call["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert res.data.total.value == 180.0 and res.data.payment_mode.null_reason == "not present"
    assert res.pipeline == "vlm" and res.usage["requests"] == 1


def test_ocr_then_llm_uses_ocr_text_and_caps_confidence():
    llm = fake_llm(RECEIPT_JSON)
    ocr = lambda _img: OcrResult("CAFE AROMA\nCoffee 180.00\nTotal 180.00", {"cafe": 40.0, "aroma": 95.0})  # noqa: E731
    ex = OcrThenLLMExtractor(llm, "text-model", ocr=ocr)
    res = ex.extract(make_image(), RECEIPT)
    call = llm.api.chat.completions.calls[0]
    assert "CAFE AROMA" in call["messages"][1]["content"] and isinstance(call["messages"][1]["content"], str)
    assert res.data.merchant.confidence == pytest.approx(0.40)  # capped by the weakest OCR word
    assert res.data.date.confidence == 0.9  # evidence words not in OCR map: unchanged
    assert res.ocr_text.startswith("CAFE")


def test_ocr_text_supplied_by_caller_skips_tesseract():
    llm = fake_llm(RECEIPT_JSON)
    ex = OcrThenLLMExtractor(llm, "text-model", ocr=lambda _i: pytest.fail("OCR should not run"))
    res = ex.extract(make_image(), RECEIPT, ocr_text="browser OCR text")
    assert res.ocr_text == "browser OCR text"


def test_cap_confidence_ignores_nulls():
    doc = Receipt.model_validate(RECEIPT_JSON)
    cap_confidence(doc, {"not": 1.0})
    assert doc.payment_mode.confidence == 0.8


def test_build_extractor():
    s = make_settings()
    llm = fake_llm({})
    assert build_extractor("vlm", llm, s).model == "vision-a"
    assert build_extractor("ocr+llm", llm, s).model == "text-model"
    with pytest.raises(Exception, match="GROQ_VISION_MODEL_B"):
        build_extractor("vlm_b", llm, s)


def test_detection_by_keywords_then_llm():
    det = detect_doc_type(make_image(), [RECEIPT, EXPENSE_CLAIM],
                          ocr_text="EXPENSE CLAIM FORM  Employee: A  Purpose: travel  Approved by")
    assert det.doc_type == "expense_claim" and det.method == "keywords"
    llm = fake_llm({"doc_type": "receipt", "confidence": 0.8})
    det = detect_doc_type(make_image(), [RECEIPT, EXPENSE_CLAIM], llm=llm, model="vision-a")
    assert det.doc_type == "receipt" and det.method == "llm"
    bad = fake_llm({"doc_type": "passport"})
    with pytest.raises(ValueError):
        detect_doc_type(make_image(), [RECEIPT, EXPENSE_CLAIM], llm=bad, model="vision-a")


def test_detection_without_any_method_raises():
    with pytest.raises(ValueError):
        detect_doc_type(make_image(), [RECEIPT, EXPENSE_CLAIM])


def test_receipt_json_roundtrips():
    doc = Receipt.model_validate(RECEIPT_JSON)
    assert json.loads(doc.model_dump_json())["total"]["value"] == 180.0
